"""Improved logging configuration for Shu RAG Backend.

This module sets up structured logging with better readability, color coding,
proper alignment, and appropriate log levels for different environments.
"""

import json
import logging
import logging.config
import os
import shutil
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings_instance

logger = logging.getLogger(__name__)

# Internal guard to prevent double configuration when setup_logging() is called
# both at import-time and again during lifespan startup
_LOGGING_CONFIGURED = False


def rotate_log_file(log_file_path: str, max_archives: int = 10) -> None:
    """Rotate the log file by archiving the current one and starting fresh.

    Args:
        log_file_path: Path to the log file to rotate
        max_archives: Maximum number of archived log files to keep

    """
    log_path = Path(log_file_path)

    # If the log file doesn't exist, nothing to rotate
    if not log_path.exists():
        return

    # Create archive filename with timestamp
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    archive_name = f"{log_path.stem}_{timestamp}{log_path.suffix}"
    archive_path = log_path.parent / archive_name

    try:
        # Move current log file to archive
        shutil.move(str(log_path), str(archive_path))

        # Clean up old archives if we have too many
        cleanup_old_log_archives(log_path.parent, log_path.stem, max_archives)

        # Log the rotation (this will go to console since file handler isn't set up yet)
        logger.info("Log file rotated: %s -> %s", log_path.name, archive_name)

    except Exception as e:
        # If rotation fails, just log the error but don't fail startup
        logger.error("Failed to rotate log file %s: %s", log_path.name, e)


def cleanup_old_log_archives(log_dir: Path, log_stem: str, max_archives: int) -> None:
    """Clean up old log archives, keeping only the most recent ones.

    Args:
        log_dir: Directory containing log files
        log_stem: Stem of the log file name (e.g., 'shu')
        max_archives: Maximum number of archived log files to keep

    """
    try:
        # Find all archived log files for this log stem
        archive_pattern = f"{log_stem}_*.log"
        archive_files = list(log_dir.glob(archive_pattern))

        # Sort by modification time (oldest first)
        archive_files.sort(key=lambda x: x.stat().st_mtime)

        # Remove oldest files if we have too many
        if len(archive_files) > max_archives:
            files_to_remove = archive_files[:-max_archives]
            for file_path in files_to_remove:
                try:
                    file_path.unlink()
                    logger.info("Removed old log archive: %s", file_path.name)
                except Exception as e:
                    logger.error("Failed to remove old log archive %s: %s", file_path.name, e)

    except Exception as e:
        logger.error("Failed to cleanup old log archives: %s", e)


class ColoredFormatter(logging.Formatter):
    """Custom colored formatter for human-readable logs with proper alignment."""

    # Color codes for different log levels
    COLORS = {
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
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")

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
            if (
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
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
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


def setup_logging() -> None:
    """Set up logging configuration with improved readability."""
    global _LOGGING_CONFIGURED
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

    # Create file handler for logs anchored at repo root so tests work no matter the CWD
    try:
        repo_root = settings.__class__._repo_root_from_this_file()
    except Exception:
        repo_root = Path().resolve()
    log_dir = repo_root / "logs"
    os.makedirs(log_dir, exist_ok=True)

    # Rotate log file before creating new one
    log_file_path = os.path.join(log_dir, "shu.log")
    rotate_log_file(log_file_path)

    file_handler = logging.FileHandler(log_file_path)
    file_handler.setFormatter(formatter)

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


def log_function_call(func):
    """Log function calls at DEBUG level decorator."""

    def wrapper(*args, **kwargs):
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


def log_async_function_call(func):
    """Log async function calls at DEBUG level decorator."""

    async def wrapper(*args, **kwargs):
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
