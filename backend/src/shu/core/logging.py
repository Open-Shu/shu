"""Improved logging configuration for Shu RAG Backend.

This module sets up structured logging with better readability, color coding,
proper alignment, and appropriate log levels for different environments.

Log file management:
- Each process startup archives the previous log file with a timestamp suffix.
- A plain FileHandler writes to a fresh file (no TimedRotatingFileHandler).
- The unified scheduler's LogMaintenanceSource handles midnight rotation and
  retention cleanup on every tick, so old archives are pruned continuously
  rather than only at startup.
"""

import json
import logging
import os
import socket
import sys
import threading
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from .config import get_settings_instance

logger = logging.getLogger(__name__)

# Internal guard to prevent double configuration when setup_logging() is called
# both at import-time and again during lifespan startup
_LOGGING_CONFIGURED = False


class ColoredFormatter(logging.Formatter):
    """Custom colored formatter for human-readable logs with proper alignment."""

    # Color codes for different log levels
    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",  # Reset
    }

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors and proper alignment."""
        # Get color for log level
        level_color = self.COLORS.get(record.levelname, "")
        reset_color = self.COLORS["RESET"] if self.use_colors else ""

        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%d %H:%M:%S")

        # Format logger name with fixed width (truncate if too long)
        logger_name = record.name
        if len(logger_name) > 25:
            logger_name = logger_name[:22] + "..."
        logger_name = f"{logger_name:25}"

        # Format the message
        message = record.getMessage()

        # Add extra fields if present (but not too verbose)
        extra_fields = []
        for key, value in record.__dict__.items():
            if (  # noqa: SIM102 # we'll need to fix this at some point, limiting  bugs for now
                key
                not in [
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "getMessage",
                ]
                and value is not None
            ):
                # Only include meaningful extra fields
                if isinstance(value, (str, int, float, bool)) and len(str(value)) < 100:
                    extra_fields.append(f"{key}={value}")

        # Build the log line with dash format
        log_line = f"{timestamp} - {level_color}{record.levelname}{reset_color} - {message}"

        # Add extra fields if present
        if extra_fields:
            log_line += f" | {' '.join(extra_fields)}"

        # Add exception info if present, otherwise include stack for warnings/errors
        if record.exc_info:
            exc_info = traceback.format_exception(*record.exc_info)
            log_line += f"\n{level_color}Exception:{reset_color}\n" + "".join(exc_info)
        else:
            try:
                if record.levelno >= logging.ERROR:
                    # Capture current stack (excluding logging call frames)
                    stack = traceback.format_stack()
                    # Trim last frame (this formatter) to reduce noise
                    if stack:
                        stack = stack[:-1]
                    log_line += f"\n{level_color}Stack:{reset_color}\n" + "".join(stack)
            except Exception:
                pass

        return log_line


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging (for production/monitoring)."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Basic log data
        log_data = {
            "timestamp": datetime.now(UTC).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present; otherwise include stack for warnings/errors
        if record.exc_info:
            exc_type = record.exc_info[0]
            log_data["exception"] = {
                "type": exc_type.__name__ if exc_type is not None else "Unknown",
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }
        elif record.levelno >= logging.ERROR:
            try:
                log_data["stack"] = traceback.format_stack()[:-1]
            except Exception:
                pass

        # Add extra fields from log record
        for key, value in record.__dict__.items():
            if key not in [
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "getMessage",
            ]:
                log_data[key] = value

        return json.dumps(log_data, default=str)


def _cleanup_old_log_archives(log_dir: Path, hostname: str, retention_days: int) -> None:
    """Remove archived log files older than the retention window.

    Handles both date-suffixed files (e.g., shu_host.log.2026-02-10)
    and startup-archived files (e.g., shu_host.log.2026-02-10_14-30-00).
    """
    prefix = f"shu_{hostname}.log."
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    try:
        for entry in os.scandir(log_dir):
            if not entry.name.startswith(prefix) or not entry.is_file():
                continue
            # Extract the date portion (first 10 chars of the suffix: YYYY-MM-DD)
            suffix = entry.name[len(prefix) :]  # pragma: allowlist secret
            date_part = suffix[:10]
            try:
                file_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=UTC)
                if file_date < cutoff:
                    os.unlink(entry.path)
            except ValueError:
                continue  # not a date-suffixed file we manage
    except OSError:
        pass  # directory listing failed; not worth crashing over


class ManagedFileHandler(logging.FileHandler):
    """FileHandler with built-in rotation and retention support.

    Replaces TimedRotatingFileHandler with a simpler approach that we fully
    control. Rotation happens in two scenarios:

    1. At process startup: setup_logging() renames the existing log file
       before creating this handler, so each app cycle starts fresh.
    2. At midnight: the scheduler calls rotate_if_needed() every tick.
       If the UTC date has changed since the file was opened, the current
       file is archived with a date suffix and a new file is opened.

    Retention cleanup also runs via rotate_if_needed(), pruning archived
    files older than the configured retention window.
    """

    def __init__(self, filename: str, hostname: str, retention_days: int) -> None:
        super().__init__(filename, mode="a", encoding="utf-8")
        self._hostname = hostname
        self._retention_days = retention_days
        self._log_dir = Path(filename).parent
        self._current_date = datetime.now(UTC).date()
        self._lock_rotate = threading.Lock()

    def rotate_if_needed(self) -> None:
        """Check if midnight has passed and rotate if so, then prune old archives.

        Called by the unified scheduler on every tick. Thread-safe.
        """
        today = datetime.now(UTC).date()
        if today != self._current_date:
            with self._lock_rotate:
                # Double-check after acquiring lock
                if today != self._current_date:
                    self._do_midnight_rotate()
                    self._current_date = today
        # Always prune old archives (cheap filesystem scan)
        _cleanup_old_log_archives(self._log_dir, self._hostname, self._retention_days)

    def _do_midnight_rotate(self) -> None:
        """Archive the current log file with a date suffix and open a fresh one."""
        # Use yesterday's date as the suffix since midnight just passed
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        archive_path = f"{self.baseFilename}.{yesterday}"
        try:
            self.stream.close()
            base_path = Path(self.baseFilename)
            if base_path.exists() and base_path.stat().st_size > 0:
                # Avoid clobbering if a startup archive already used this date
                if os.path.exists(archive_path):
                    archive_path = f"{self.baseFilename}.{yesterday}_midnight"
                base_path.rename(archive_path)
            self.stream = self._open()
        except OSError as e:
            # Re-open even on failure so logging doesn't break
            try:
                self.stream = self._open()
            except Exception:
                pass
            logging.getLogger(__name__).warning("Midnight log rotation failed: %s", e)


# Module-level reference so the scheduler source can call rotate_if_needed()
_managed_file_handler: ManagedFileHandler | None = None


def get_managed_file_handler() -> ManagedFileHandler | None:
    """Return the active ManagedFileHandler, if logging has been configured."""
    return _managed_file_handler


# TODO: Refactor this function. It's too complex (number of branches and statements).
def setup_logging() -> None:  # noqa: PLR0915
    """Set up logging configuration with improved readability."""
    global _LOGGING_CONFIGURED  # noqa: PLW0603 # it's working, so we will leave it as is for now
    if _LOGGING_CONFIGURED:
        return

    # Clear existing handlers
    logging.getLogger().handlers.clear()

    # Get settings instance
    settings = get_settings_instance()

    # Determine if we should use colors (only in development and when output is a terminal)
    use_colors = settings.environment == "development" and sys.stdout.isatty()

    # Create formatter based on settings
    formatter = JSONFormatter() if settings.log_format == "json" else ColoredFormatter(use_colors=use_colors)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Create file handler using configurable log directory and our ManagedFileHandler.
    # Startup archiving gives each app cycle a clean file. Midnight rotation and
    # retention cleanup are handled by the scheduler's LogMaintenanceSource.
    global _managed_file_handler  # noqa: PLW0603
    log_dir = Path(settings.log_dir)
    os.makedirs(log_dir, exist_ok=True)

    # Include hostname in filename so horizontally scaled replicas don't clobber each other
    hostname = socket.gethostname()
    log_file_path = os.path.join(log_dir, f"shu_{hostname}.log")

    # Archive the previous run's log file before the handler opens it.
    # Each restart gets a unique timestamp suffix so same-day restarts
    # never collide.
    log_path = Path(log_file_path)
    if log_path.exists() and log_path.stat().st_size > 0:
        ts = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
        archive_path = f"{log_file_path}.{ts}"
        try:
            log_path.rename(archive_path)
        except OSError:
            pass  # worst case we append; not worth crashing over

    file_handler = ManagedFileHandler(
        log_file_path,
        hostname=hostname,
        retention_days=settings.log_retention_days,
    )
    file_handler.setFormatter(formatter)
    _managed_file_handler = file_handler

    # Run an initial cleanup of old archives at startup
    _cleanup_old_log_archives(log_dir, hostname, settings.log_retention_days)

    # Immediately configure SQLAlchemy loggers to use our formatter
    # This prevents SQLAlchemy from setting up its own logging
    sqlalchemy_loggers = [
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
        "sqlalchemy.orm",
        "sqlalchemy",
    ]
    for logger_name in sqlalchemy_loggers:
        log = logging.getLogger(logger_name)
        log.handlers.clear()
        log.addHandler(console_handler)
        log.propagate = False

    # Configure root logger with our formatter
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level))

    # Clear existing handlers and add our handlers
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Set specific logger levels based on config
    # Uvicorn logs - use config level but cap at WARNING to reduce noise
    uvicorn_level = min(getattr(logging, settings.log_level), logging.WARNING)
    logging.getLogger("uvicorn").setLevel(uvicorn_level)
    logging.getLogger("uvicorn.access").setLevel(uvicorn_level)

    # SQLAlchemy logging - always set to ERROR to hide SQL queries unless explicitly needed
    # SQLAlchemy logs SQL queries at INFO level, which is too verbose for normal operation
    sqlalchemy_level = logging.ERROR
    sqlalchemy_loggers = [
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
        "sqlalchemy.orm",
        "sqlalchemy",
    ]
    for logger_name in sqlalchemy_loggers:
        log = logging.getLogger(logger_name)
        log.setLevel(sqlalchemy_level)
        # Force SQLAlchemy to use our formatter by clearing existing handlers
        log.handlers.clear()
        # Add our handlers with our formatter for consistent styling
        log.addHandler(console_handler)
        log.addHandler(file_handler)
        # Disable propagation to prevent double logging
        log.propagate = False

    # Reduce noise from other libraries - use config level but cap at WARNING
    external_lib_level = min(getattr(logging, settings.log_level), logging.WARNING)
    logging.getLogger("httpx").setLevel(external_lib_level)
    logging.getLogger("urllib3").setLevel(external_lib_level)
    logging.getLogger("urllib3.connectionpool").setLevel(external_lib_level)
    logging.getLogger("urllib3.connection").setLevel(external_lib_level)
    logging.getLogger("requests").setLevel(external_lib_level)
    logging.getLogger("google.auth").setLevel(external_lib_level)
    logging.getLogger("google.auth.transport").setLevel(external_lib_level)
    logging.getLogger("googleapiclient").setLevel(external_lib_level)

    # Reduce Hugging Face logging noise - these are very verbose
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub.file_download").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub.utils").setLevel(logging.WARNING)
    logging.getLogger("tokenizers").setLevel(logging.WARNING)

    # Silence all HTTP-related debug messages
    for logger_name in [
        "urllib3",
        "urllib3.connectionpool",
        "urllib3.connection",
        "httpx",
        "requests",
    ]:
        log = logging.getLogger(logger_name)
        log.setLevel(logging.WARNING)
        # Also disable propagation to prevent any debug messages from bubbling up
        log.propagate = False

    # Create Shu logger
    logger = logging.getLogger("shu")
    logger.setLevel(getattr(logging, settings.log_level))

    # Configure uvicorn logging to respect Shu log level and use our formatter
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")

    # Set uvicorn loggers to use Shu log level
    shu_log_level = getattr(logging, settings.log_level)
    uvicorn_logger.setLevel(shu_log_level)
    uvicorn_access_logger.setLevel(shu_log_level)
    uvicorn_error_logger.setLevel(shu_log_level)

    # Configure uvicorn loggers to use our formatter
    for uvicorn_log in [uvicorn_logger, uvicorn_access_logger, uvicorn_error_logger]:
        uvicorn_log.handlers.clear()
        uvicorn_log.addHandler(console_handler)
        uvicorn_log.addHandler(file_handler)
        uvicorn_log.propagate = False

    logger.info(
        "Logging configured",
        extra={
            "log_level": settings.log_level,
            "log_format": settings.log_format,
            "environment": settings.environment,
            "use_colors": use_colors,
        },
    )
    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name."""
    return logging.getLogger(f"shu.{name}")


class LoggerMixin:
    """Mixin class to add logging capabilities to any class."""

    @property
    def logger(self) -> logging.Logger:
        """Get logger for this class."""
        return get_logger(self.__class__.__name__)


def log_function_call(func):  # type: ignore[no-untyped-def]
    """Log function calls at DEBUG level decorator."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger = get_logger(func.__module__)
        logger.debug(
            f"Calling {func.__name__}",
            extra={
                "function": func.__name__,
                "args": str(args)[:100],  # Truncate to avoid large logs
                "kwargs": str(kwargs)[:100],
            },
        )
        try:
            result = func(*args, **kwargs)
            logger.debug(f"Function {func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(
                f"Function {func.__name__} failed",
                extra={
                    "function": func.__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise

    return wrapper


def log_async_function_call(func):  # type: ignore[no-untyped-def]
    """Log async function calls at DEBUG level decorator."""

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        logger = get_logger(func.__module__)
        logger.debug(
            f"Calling async {func.__name__}",
            extra={
                "function": func.__name__,
                "args": str(args)[:100],
                "kwargs": str(kwargs)[:100],
            },
        )
        try:
            result = await func(*args, **kwargs)
            logger.debug(f"Async function {func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(
                f"Async function {func.__name__} failed",
                extra={
                    "function": func.__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise

    return wrapper
