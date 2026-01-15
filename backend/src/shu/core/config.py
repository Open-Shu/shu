"""
Configuration management for Shu RAG Backend.

Uses Pydantic Settings for type-safe, environment-based configuration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import os
from functools import lru_cache
from dotenv import load_dotenv

# Load environment variables from .env file
# Use override=True to ensure .env changes take effect immediately
load_dotenv(override=True)


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # App configuration
    app_name: str = Field("Shu", alias="SHU_APP_NAME")
    debug: bool = Field(False, alias="SHU_DEBUG")
    # Build/version metadata (baked at build-time; defaults are safe for dev)
    version: str = Field("0.0.0-dev", alias="SHU_APP_VERSION")
    git_sha: str = Field("unknown", alias="SHU_GIT_SHA")
    build_timestamp: str = Field("unknown", alias="SHU_BUILD_TIMESTAMP")
    db_release: Optional[str] = Field(None, alias="SHU_DB_RELEASE")

    # API configuration
    api_v1_prefix: str = "/api/v1"
    api_host: str = Field("0.0.0.0", alias="SHU_API_HOST")
    api_port: int = Field(8000, alias="SHU_API_PORT")
    environment: str = Field("development", alias="SHU_ENVIRONMENT")
    reload: bool = Field(False, alias="SHU_RELOAD")

    # Database configuration
    database_url: str = Field(alias="SHU_DATABASE_URL")
    database_pool_size: int = 20
    database_max_overflow: int = 30
    database_pool_timeout: int = 30
    database_pool_recycle: int = 3600

    # Redis configuration
    redis_url: str = Field("redis://localhost:6379", alias="SHU_REDIS_URL")
    redis_connection_timeout: int = Field(5, alias="SHU_REDIS_CONNECTION_TIMEOUT")
    redis_socket_timeout: int = Field(5, alias="SHU_REDIS_SOCKET_TIMEOUT")
    redis_fallback_enabled: bool = Field(True, alias="SHU_REDIS_FALLBACK_ENABLED")
    redis_required: bool = Field(False, alias="SHU_REDIS_REQUIRED")

    # Google Drive configuration
    google_service_account_json: Optional[str] = Field(None, alias="GOOGLE_SERVICE_ACCOUNT_JSON")

    # Google SSO configuration
    google_client_id: Optional[str] = Field(None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = Field(None, alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field("http://localhost:8000/auth/callback", alias="GOOGLE_REDIRECT_URI")

    # Microsoft 365 OAuth configuration
    microsoft_client_id: Optional[str] = Field(None, alias="MICROSOFT_CLIENT_ID")
    microsoft_client_secret: Optional[str] = Field(None, alias="MICROSOFT_CLIENT_SECRET")
    microsoft_redirect_uri: Optional[str] = Field(None, alias="MICROSOFT_REDIRECT_URI")
    microsoft_tenant_id: Optional[str] = Field(None, alias="MICROSOFT_TENANT_ID")

    # Google Workspace configuration for organizational intelligence
    google_service_account_file: Optional[str] = Field(None, alias="GOOGLE_SERVICE_ACCOUNT_FILE")
    google_admin_user_email: Optional[str] = Field(None, alias="GOOGLE_ADMIN_USER_EMAIL") # only needed for Admin Directory API integration tests
    google_domain: Optional[str] = Field(None, alias="GOOGLE_DOMAIN")

    # JWT configuration
    jwt_secret_key: Optional[str] = Field(None, alias="JWT_SECRET_KEY")
    jwt_access_token_expire_minutes: int = Field(60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(30, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    # Admin configuration
    admin_emails: List[str] = Field(default_factory=list, alias="ADMIN_EMAILS")

    # Embedding configuration
    default_embedding_model: str = Field("sentence-transformers/all-MiniLM-L6-v2", alias="SHU_EMBEDDING_MODEL")
    embedding_device: str = "cpu"
    embedding_batch_size: int = Field(32, alias="SHU_EMBEDDING_BATCH_SIZE")
    embedding_dimension: int = Field(384, alias="SHU_EMBEDDING_DIMENSION")
    # Execution mode for embedding generation: "thread" (optimized, default) or "process"
    embedding_execution_mode: str = Field("thread", alias="SHU_EMBEDDING_EXECUTION_MODE")

    # Text processing configuration
    default_chunk_size: int = Field(1000, alias="SHU_DEFAULT_CHUNK_SIZE")
    default_chunk_overlap: int = Field(200, alias="SHU_DEFAULT_CHUNK_OVERLAP")
    max_chunk_size: int = 2000
    # OCR/Text extraction execution mode: "thread" (default) or "process"
    ocr_execution_mode: str = Field("thread", alias="SHU_OCR_EXECUTION_MODE")

    # Text extraction timeout configuration (system-level, not user-configurable)
    text_extraction_timeout_default: int = Field(1800, alias="SHU_TEXT_EXTRACTION_TIMEOUT")  # 30 minutes for OCR
    text_extraction_fast_timeout_default: int = Field(300, alias="SHU_TEXT_EXTRACTION_FAST_TIMEOUT")  # 5 minutes for fast extraction

    # Vector database configuration
    vector_index_type: str = Field("ivfflat", alias="SHU_VECTOR_INDEX_TYPE")
    vector_index_lists: int = Field(100, alias="SHU_VECTOR_INDEX_LISTS")

    # Performance configuration
    batch_size: int = Field(10, alias="SHU_BATCH_SIZE")
    max_workers: int = Field(4, alias="SHU_MAX_WORKERS")
    download_concurrency: int = Field(3, alias="SHU_DOWNLOAD_CONCURRENCY")
    cache_ttl: int = Field(3600, alias="SHU_CACHE_TTL")

    # Logging configuration
    log_level: str = Field("INFO", alias="SHU_LOG_LEVEL")
    log_format: str = Field("text", alias="SHU_LOG_FORMAT")  # text or json - text is more readable for development

    # Branding configuration
    branding_assets_dir: str = Field("./data/branding", alias="SHU_BRANDING_ASSETS_DIR")
    branding_default_logo_url: str = Field("/logo-wide.png", alias="SHU_BRANDING_DEFAULT_LOGO_URL")
    branding_default_favicon_url: str = Field("/favicon.png", alias="SHU_BRANDING_DEFAULT_FAVICON_URL")
    branding_allowed_logo_extensions: List[str] = Field(default_factory=lambda: ["png", "jpg", "jpeg", "svg", "webp"], alias="SHU_BRANDING_ALLOWED_LOGO_EXTENSIONS")
    branding_allowed_favicon_extensions: List[str] = Field(default_factory=lambda: ["ico", "png", "svg", "webp"], alias="SHU_BRANDING_ALLOWED_FAVICON_EXTENSIONS")
    branding_max_asset_size_bytes: int = Field(2 * 1024 * 1024, alias="SHU_BRANDING_MAX_ASSET_SIZE_BYTES")

    # Security configuration
    api_key: Optional[str] = Field(None, alias="SHU_API_KEY")
    # When using the global API key (Tier 0), map it to this user's identity for RBAC
    api_key_user_email: Optional[str] = Field(None, alias="SHU_API_KEY_USER_EMAIL")
    secret_key: Optional[str] = Field(None, alias="SHU_SECRET_KEY")
    allowed_origins: List[str] = ["*"]
    cors_credentials: bool = True
    # Trusted hosts for Host header validation (non-dev)
    allowed_hosts: List[str] = Field(default=["*"], alias="SHU_ALLOWED_HOSTS")

    # Monitoring configuration
    enable_metrics: bool = True
    metrics_port: int = 8001

    # Background task configuration
    max_sync_workers: int = 5
    sync_timeout: int = Field(3600, alias="SHU_SYNC_TIMEOUT")  # 1 hour
    sync_retry_attempts: int = Field(3, alias="SHU_SYNC_RETRY_ATTEMPTS")  # Default retry attempts

    # Worker mode configuration
    worker_mode: str = Field("inline", alias="SHU_WORKER_MODE")  # "inline" or "dedicated"
    worker_poll_interval: float = Field(1.0, alias="SHU_WORKER_POLL_INTERVAL")  # seconds
    worker_shutdown_timeout: float = Field(30.0, alias="SHU_WORKER_SHUTDOWN_TIMEOUT")  # seconds

    # Rate limiting
    enable_rate_limiting: bool = Field(True, alias="SHU_ENABLE_RATE_LIMITING")
    rate_limit_requests: int = Field(100, alias="SHU_RATE_LIMIT_REQUESTS")
    rate_limit_period: int = Field(60, alias="SHU_RATE_LIMIT_PERIOD")  # seconds
    rate_limit_user_requests: int = Field(50, alias="SHU_RATE_LIMIT_USER_REQUESTS")  # per user
    rate_limit_user_period: int = Field(60, alias="SHU_RATE_LIMIT_USER_PERIOD")  # seconds

    # Quotas (per-plugin/per-user)
    plugin_quota_daily_requests_default: int = Field(0, alias="SHU_PLUGIN_QUOTA_DAILY_REQUESTS_DEFAULT")
    plugin_quota_monthly_requests_default: int = Field(0, alias="SHU_PLUGIN_QUOTA_MONTHLY_REQUESTS_DEFAULT")

    # Plugins
    plugins_auto_sync: bool = Field(False, alias="SHU_PLUGINS_AUTO_SYNC")
    # Root directory where plugins are discovered/installed (relative paths are resolved from repo root)
    plugins_root: str = Field("./plugins", alias="SHU_PLUGINS_ROOT")

    # HTTP Egress Policy for HostCapabilities.http
    # Comma-separated domain suffixes or exact hosts; empty = allow all (development default)
    http_egress_allowlist: Optional[List[str]] = Field(default=None, alias="SHU_HTTP_EGRESS_ALLOWLIST")
    # Default timeout (seconds) for host.http requests
    http_default_timeout: float = Field(30.0, alias="SHU_HTTP_DEFAULT_TIMEOUT")

    # Request size limits
    max_query_length: int = Field(10000, alias="SHU_MAX_QUERY_LENGTH")  # characters
    max_file_size: int = Field(50 * 1024 * 1024, alias="SHU_MAX_FILE_SIZE")  # 50MB in bytes
    max_batch_size: int = Field(100, alias="SHU_MAX_BATCH_SIZE")  # items per batch
    max_request_size: int = Field(10 * 1024 * 1024, alias="SHU_MAX_REQUEST_SIZE")  # 10MB in bytes


    # Plugin execution size caps (0 disables limit for that direction)
    plugin_exec_input_max_bytes: int = Field(256 * 1024, alias="SHU_PLUGIN_EXEC_INPUT_MAX_BYTES")
    plugin_exec_output_max_bytes: int = Field(1 * 1024 * 1024, alias="SHU_PLUGIN_EXEC_OUTPUT_MAX_BYTES")

    # Chat attachments
    chat_attachment_max_size: int = Field(20 * 1024 * 1024, alias="SHU_CHAT_ATTACHMENT_MAX_SIZE")  # 20MB
    chat_attachment_allowed_types: List[str] = Field(default_factory=lambda: ["pdf", "docx", "txt", "md", "png", "jpg", "jpeg", "gif", "webp"], alias="SHU_CHAT_ATTACHMENT_ALLOWED_TYPES")
    chat_attachment_ttl_days: int = Field(14, alias="SHU_CHAT_ATTACHMENT_TTL_DAYS")
    chat_attachment_storage_dir: str = Field("./data/attachments", alias="SHU_CHAT_ATTACHMENT_STORAGE_DIR")
    chat_ensemble_max_models: int = Field(3, alias="SHU_CHAT_ENSEMBLE_MAX_MODELS")

    # KB document upload (types supported by text extractor - no standalone image OCR)
    kb_upload_max_size: int = Field(50 * 1024 * 1024, alias="SHU_KB_UPLOAD_MAX_SIZE")  # 50MB
    kb_upload_allowed_types: List[str] = Field(
        default_factory=lambda: ["pdf", "docx", "doc", "txt", "md", "rtf", "html", "htm", "csv", "py", "js", "xlsx", "pptx"],
        alias="SHU_KB_UPLOAD_ALLOWED_TYPES"
    )

    # Shu RAG Document Profiling (SHU-343)
    enable_document_profiling: bool = Field(False, alias="SHU_ENABLE_DOCUMENT_PROFILING")
    # Routing threshold: docs at or below this size use full-doc profiling;
    # larger docs use chunk-first aggregation
    profiling_full_doc_max_tokens: int = Field(4000, alias="SHU_PROFILING_FULL_DOC_MAX_TOKENS")
    # Hard ceiling on any single profiling LLM call (full-doc or aggregate)
    profiling_max_input_tokens: int = Field(8000, alias="SHU_PROFILING_MAX_INPUT_TOKENS")
    profiling_timeout_seconds: int = Field(60, alias="SHU_PROFILING_TIMEOUT_SECONDS")
    # Process chunks in batches for efficiency
    chunk_profiling_batch_size: int = Field(10, alias="SHU_CHUNK_PROFILING_BATCH_SIZE")
    # Max concurrent profiling tasks to prevent LLM rate-limit storms during bulk imports
    # Tasks beyond this limit queue in memory; see SHU-211 for persistent queue migration
    profiling_max_concurrent_tasks: int = Field(5, alias="SHU_PROFILING_MAX_CONCURRENT_TASKS")

    @staticmethod
    def _repo_root_from_this_file() -> Path:
        """Resolve repository root robustly for both local and container layouts.

        - Local dev: <repo>/backend/src/shu/core/config.py -> repo root = <repo>
        - Container: /app/src/shu/core/config.py -> repo root = /app
        """
        here = Path(__file__).resolve()
        src_dir = here.parents[2]  # .../src
        candidate_parent = src_dir.parent  # repo/app or backend
        return candidate_parent.parent if candidate_parent.name == "backend" else candidate_parent

    @field_validator("branding_assets_dir", mode="before")
    @classmethod
    def _resolve_branding_dir(cls, v: str) -> str:
        try:
            p = Path(v)
            if p.is_absolute():
                return str(p)
            root = cls._repo_root_from_this_file()
            return str((root / p).resolve())
        except Exception:
            return v

    @field_validator("chat_attachment_storage_dir", mode="before")
    @classmethod
    def _resolve_attachments_dir(cls, v: str) -> str:
        try:
            p = Path(v)
            if p.is_absolute():
                return str(p)
            root = cls._repo_root_from_this_file()
            return str((root / p).resolve())
        except Exception:
            return v

    # LLM Configuration
    llm_encryption_key: Optional[str] = Field(None, alias="SHU_LLM_ENCRYPTION_KEY")

    # Chat attachment context limits
    chat_attachment_max_chars_per_file: int = Field(5000, alias="SHU_CHAT_ATTACHMENT_MAX_CHARS_PER_FILE")
    chat_attachment_max_total_chars: int = Field(15000, alias="SHU_CHAT_ATTACHMENT_MAX_TOTAL_CHARS")


    # Attachment cleanup scheduler
    chat_attachment_cleanup_interval_seconds: int = Field(6 * 3600, alias="SHU_CHAT_ATTACHMENT_CLEANUP_INTERVAL_SECONDS")

    # Conversation automation defaults
    conversation_summary_prompt: str = Field(
        """
            You are a summarizer.
            Input: (A) PREVIOUS_SUMMARY as bullet points; (B) NEW_MESSAGES.
            Task: output ONLY a refreshed bullet list.

            Rules:
            - Keep ≤ 7 bullets, each ≤ 20 words.
            - Preserve correct prior bullets unless contradicted by NEW_MESSAGES.
            - Update or drop bullets that are outdated or redundant.
            - Add new bullets only if clearly supported by NEW_MESSAGES.
            - No prose, no headers, no explanations—just bullets.
            - If evidence is thin, append " (?)" at the end of that bullet.
        """,
        alias="SHU_CONVERSATION_SUMMARY_PROMPT"
    )
    conversation_summary_timeout_ms: int = Field(15000, alias="SHU_CONVERSATION_SUMMARY_TIMEOUT_MS")
    conversation_summary_max_recent_messages: int = Field(
        40, alias="SHU_CONVERSATION_SUMMARY_MAX_RECENT_MESSAGES"
    )
    conversation_summary_search_min_token_length: int = Field(
        3, alias="SHU_CONVERSATION_SUMMARY_SEARCH_MIN_TOKEN_LENGTH"
    )
    conversation_summary_search_max_tokens: int = Field(
        10, alias="SHU_CONVERSATION_SUMMARY_SEARCH_MAX_TOKENS"
    )

    conversation_auto_rename_prompt: str = Field(
        """
            Your purpose is to determine what the user is chatting about and give the chat a meaningful name.
            Input: (A) SUMMARY containing the chat context;

            Rules:
            - It is very important that you only return the name, nothing else.
            - Be concise, no more than five words or 200 characters, whichever comes first.
            - Do not use bullet points.
            - No prose, no headers, no explanations—just text.
        """,
        alias="SHU_CONVERSATION_AUTO_RENAME_PROMPT"
    )
    conversation_auto_rename_timeout_ms: int = Field(8000, alias="SHU_CONVERSATION_AUTO_RENAME_TIMEOUT_MS")

    # Tools Feeds Scheduler (in-process)
    plugins_scheduler_enabled: bool = Field(True, alias="SHU_PLUGINS_SCHEDULER_ENABLED")
    plugins_scheduler_tick_seconds: int = Field(60, alias="SHU_PLUGINS_SCHEDULER_TICK_SECONDS")
    plugins_scheduler_batch_limit: int = Field(10, alias="SHU_PLUGINS_SCHEDULER_BATCH_LIMIT")
    # Mark RUNNING executions older than this many seconds as stale (0 disables cleanup)
    plugins_scheduler_running_timeout_seconds: int = Field(3600, alias="SHU_PLUGINS_SCHEDULER_RUNNING_TIMEOUT_SECONDS")

    plugins_scheduler_retry_backoff_seconds: int = Field(5, alias="SHU_PLUGINS_SCHEDULER_RETRY_BACKOFF_SECONDS")


    # Chat Plugins (disabled by default; enable when Chat M1 slice resumes)
    chat_plugins_enabled: bool = Field(False, alias="SHU_CHAT_PLUGINS_ENABLED")

    llm_dev_mode: bool = Field(False, alias="SHU_LLM_DEV_MODE")

    # OAuth Token Encryption
    oauth_encryption_key: Optional[str] = Field(None, alias="SHU_OAUTH_ENCRYPTION_KEY")

    # Development fallback LLM configuration
    default_llm_provider: str = Field("openai", alias="SHU_DEFAULT_LLM_PROVIDER")
    default_llm_model: str = Field("gpt-4", alias="SHU_DEFAULT_LLM_MODEL")
    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")

    # Global LLM limits
    llm_global_timeout: int = Field(30, alias="SHU_LLM_GLOBAL_TIMEOUT")
    llm_streaming_read_timeout: int = Field(120, alias="SHU_LLM_STREAMING_READ_TIMEOUT")
    llm_max_tokens_default: int = Field(50_000, alias="SHU_LLM_MAX_TOKENS_DEFAULT")
    llm_temperature_default: float = Field(0.7, alias="SHU_LLM_TEMPERATURE_DEFAULT")

    # RAG Configuration Defaults (global fallbacks)
    rag_search_threshold_default: float = Field(0.7, alias="SHU_RAG_SEARCH_THRESHOLD_DEFAULT")
    rag_max_results_default: int = Field(10, alias="SHU_RAG_MAX_RESULTS_DEFAULT")
    rag_chunk_overlap_ratio_default: float = Field(0.2, alias="SHU_RAG_CHUNK_OVERLAP_RATIO_DEFAULT")
    rag_search_type_default: str = Field("hybrid", alias="SHU_RAG_SEARCH_TYPE_DEFAULT")
    rag_context_format_default: str = Field("detailed", alias="SHU_RAG_CONTEXT_FORMAT_DEFAULT")
    rag_reference_format_default: str = Field("markdown", alias="SHU_RAG_REFERENCE_FORMAT_DEFAULT")
    rag_include_references_default: bool = Field(True, alias="SHU_RAG_INCLUDE_REFERENCES_DEFAULT")
    rag_prompt_template_default: str = Field("custom", alias="SHU_RAG_PROMPT_TEMPLATE_DEFAULT")

    # Hybrid Search Configuration (global defaults)
    hybrid_similarity_weight_default: float = Field(0.7, alias="SHU_HYBRID_SIMILARITY_WEIGHT_DEFAULT")
    hybrid_keyword_weight_default: float = Field(0.3, alias="SHU_HYBRID_KEYWORD_WEIGHT_DEFAULT")

    # Title Search Configuration (global defaults)
    title_weighting_enabled_default: bool = Field(True, alias="SHU_TITLE_WEIGHTING_ENABLED_DEFAULT")
    title_weight_multiplier_default: float = Field(3.0, alias="SHU_TITLE_WEIGHT_MULTIPLIER_DEFAULT")
    title_chunk_enabled_default: bool = Field(True, alias="SHU_TITLE_CHUNK_ENABLED_DEFAULT")

    # Query Processing Configuration (global defaults)
    rag_minimum_query_words_default: int = Field(3, alias="SHU_RAG_MINIMUM_QUERY_WORDS_DEFAULT")

    # Document Chunk Configuration (global defaults)
    max_chunks_per_document_default: int = Field(2, alias="SHU_MAX_CHUNKS_PER_DOCUMENT_DEFAULT")

    # Full Document Escalation Defaults
    rag_full_doc_fetch_default: bool = Field(False, alias="SHU_RAG_FULL_DOC_FETCH_DEFAULT")
    rag_full_doc_max_docs_default: int = Field(2, alias="SHU_RAG_FULL_DOC_MAX_DOCS_DEFAULT")
    rag_full_doc_token_cap_default: int = Field(80000, alias="SHU_RAG_FULL_DOC_TOKEN_CAP_DEFAULT")

    # User Preferences Defaults (what users can actually configure)
    user_memory_depth_default: int = Field(5, alias="SHU_USER_MEMORY_DEPTH_DEFAULT")
    user_memory_similarity_threshold_default: float = Field(0.6, alias="SHU_USER_MEMORY_SIMILARITY_THRESHOLD_DEFAULT")
    user_theme_default: str = Field("light", alias="SHU_USER_THEME_DEFAULT")
    user_language_default: str = Field("en", alias="SHU_USER_LANGUAGE_DEFAULT")
    user_timezone_default: str = Field("UTC", alias="SHU_USER_TIMEZONE_DEFAULT")

    # Strict Rate Limiting Configuration
    strict_rate_limit_requests: int = Field(10, alias="SHU_STRICT_RATE_LIMIT_REQUESTS")
    strict_rate_limit_user_requests: int = Field(5, alias="SHU_STRICT_RATE_LIMIT_USER_REQUESTS")
    max_pagination_limit: int = Field(1000, alias="SHU_MAX_PAGINATION_LIMIT")

    # OCR Configuration
    ocr_primary_engine: str = Field(default="easyocr", description="Primary OCR engine: easyocr, tesseract")
    ocr_use_gpu: bool = Field(default=False, description="Use GPU acceleration for OCR (if available)")
    ocr_confidence_threshold: float = Field(default=0.6, description="Minimum confidence threshold for OCR results")
    # Note: No page limits - OCR processes all pages in document

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v):
        """Validate database URL format."""
        if not v.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+asyncpg://")):
            raise ValueError("Database URL must be PostgreSQL")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v):
        """Validate log format."""
        valid_formats = ["text", "json"]
        if v.lower() not in valid_formats:
            raise ValueError(f"Log format must be one of: {valid_formats}")
        return v.lower()

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment setting."""
        valid_environments = ["development", "staging", "production"]
        if v.lower() not in valid_environments:
            raise ValueError(f"Environment must be one of: {valid_environments}")
        return v.lower()

    @field_validator("vector_index_type")
    @classmethod
    def validate_vector_index_type(cls, v):
        """Validate vector index type."""
        valid_types = ["ivfflat", "hnsw"]
        if v.lower() not in valid_types:
            raise ValueError(f"Vector index type must be one of: {valid_types}")
        return v.lower()

    @field_validator("google_service_account_json")
    @classmethod
    def validate_google_credentials(cls, v):
        """Validate Google service account credentials."""
        if v and not v.strip():
            return None
        return v

    @field_validator("http_egress_allowlist", mode="before")
    @classmethod
    def validate_http_allowlist(cls, v):
        """Allow comma-separated string or list for egress allowlist. Empty => None (allow all)."""
        if v is None:
            return None
        if isinstance(v, str):
            if not v.strip():
                return None
            # split by comma and strip
            items = [part.strip() for part in v.split(",") if part.strip()]
            return items or None
        if isinstance(v, list):
            items = [str(part).strip() for part in v if str(part).strip()]
            return items or None
        return None

    @field_validator("admin_emails", mode="before")
    @classmethod
    def validate_admin_emails(cls, v):
        """Parse admin emails from comma-separated string or list."""
        if isinstance(v, str):
            if not v.strip():
                return []
            return [email.strip() for email in v.split(",") if email.strip()]
        elif isinstance(v, list):
            return [email.strip() for email in v if email.strip()]
        return []

    @field_validator("worker_mode")
    @classmethod
    def validate_worker_mode(cls, v):
        """Validate worker mode setting."""
        valid_modes = ["inline", "dedicated"]
        if v.lower() not in valid_modes:
            raise ValueError(f"Worker mode must be one of: {valid_modes}")
        return v.lower()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # Ignore extra environment variables instead of forbidding them
    )


def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance - will be created when first accessed
settings = None


def get_settings_instance() -> Settings:
    """Get the global settings instance, creating it if necessary."""
    global settings
    if settings is None:
        settings = get_settings()
    return settings


class ConfigurationManager:
    """
    Centralized configuration manager that handles the priority cascade:
    User Preferences → Model Config → KB Config → Global Defaults

    This replaces hardcoded values throughout the codebase and ensures
    consistent configuration resolution following the established hierarchy.

    Note: This class is designed for dependency injection for better testability
    and loose coupling. Use get_config_manager() dependency in FastAPI endpoints.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # RAG Configuration Resolution
    def get_rag_search_threshold(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Get search threshold with proper priority cascade.

        Priority: user_prefs → model_config → kb_config → global_default
        Note: Currently user_prefs should NOT override KB settings (per user feedback)
        """
        # For now, skip user preferences for RAG settings (they shouldn't override KB/admin settings)
        if kb_config and kb_config.get("search_threshold") is not None:
            return float(kb_config["search_threshold"])
        if model_config and model_config.get("search_threshold") is not None:
            return float(model_config["search_threshold"])
        return self.settings.rag_search_threshold_default

    def get_rag_max_results(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get max results with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("max_results") is not None:
            return int(kb_config["max_results"])
        if model_config and model_config.get("max_results") is not None:
            return int(model_config["max_results"])
        return self.settings.rag_max_results_default

    def get_rag_search_type(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get search type with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("search_type"):
            return str(kb_config["search_type"])
        if model_config and model_config.get("search_type"):
            return str(model_config["search_type"])
        return self.settings.rag_search_type_default

    def get_rag_context_format(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get context format with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("context_format"):
            return str(kb_config["context_format"])
        if model_config and model_config.get("context_format"):
            return str(model_config["context_format"])
        return self.settings.rag_context_format_default

    def get_rag_reference_format(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get reference format with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("reference_format"):
            return str(kb_config["reference_format"])
        if model_config and model_config.get("reference_format"):
            return str(model_config["reference_format"])
        return self.settings.rag_reference_format_default

    def get_rag_include_references(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Get include references with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("include_references") is not None:
            return bool(kb_config["include_references"])
        if model_config and model_config.get("include_references") is not None:
            return bool(model_config["include_references"])
        return self.settings.rag_include_references_default

    def get_rag_chunk_overlap_ratio(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """Get chunk overlap ratio with proper priority cascade."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("chunk_overlap_ratio") is not None:
            return float(kb_config["chunk_overlap_ratio"])
        if model_config and model_config.get("chunk_overlap_ratio") is not None:
            return float(model_config["chunk_overlap_ratio"])
        return self.settings.rag_chunk_overlap_ratio_default



    # LLM Configuration Resolution
    def get_llm_temperature(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Get LLM temperature with proper priority cascade.

        Priority: model_config → global_default
        Note: User preferences should NOT override model configuration
        """
        if model_config and model_config.get("temperature") is not None:
            return float(model_config["temperature"])
        return self.settings.llm_temperature_default

    def get_llm_max_tokens(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get LLM max tokens with proper priority cascade."""
        if model_config and model_config.get("max_tokens") is not None:
            return int(model_config["max_tokens"])
        return self.settings.llm_max_tokens_default

    # User Preferences Resolution (legitimate user settings)
    def get_user_memory_depth(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Get user memory depth with proper priority cascade.

        Priority: user_prefs → global_default
        This is a legitimate user preference that users can control.
        """
        if user_prefs and user_prefs.get("memory_depth") is not None:
            return int(user_prefs["memory_depth"])
        return self.settings.user_memory_depth_default

    def get_user_memory_similarity_threshold(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """Get user memory similarity threshold with proper priority cascade."""
        if user_prefs and user_prefs.get("memory_similarity_threshold") is not None:
            return float(user_prefs["memory_similarity_threshold"])
        return self.settings.user_memory_similarity_threshold_default

    def get_user_theme(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get user theme with proper priority cascade."""
        if user_prefs and user_prefs.get("theme"):
            return str(user_prefs["theme"])
        return self.settings.user_theme_default

    def get_user_language(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get user language with proper priority cascade."""
        if user_prefs and user_prefs.get("language"):
            return str(user_prefs["language"])
        return self.settings.user_language_default

    def get_user_timezone(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get user timezone with proper priority cascade."""
        if user_prefs and user_prefs.get("timezone"):
            return str(user_prefs["timezone"])
        return self.settings.user_timezone_default

    # Hybrid Search Configuration Methods
    def get_hybrid_similarity_weight(
        self,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Get hybrid search similarity weight with proper priority cascade.

        Priority: kb_config → global_default
        """
        if kb_config and kb_config.get("hybrid_similarity_weight") is not None:
            return float(kb_config["hybrid_similarity_weight"])
        return self.settings.hybrid_similarity_weight_default

    def get_hybrid_keyword_weight(
        self,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Get hybrid search keyword weight with proper priority cascade.

        Priority: kb_config → global_default
        """
        if kb_config and kb_config.get("hybrid_keyword_weight") is not None:
            return float(kb_config["hybrid_keyword_weight"])
        return self.settings.hybrid_keyword_weight_default

    def get_hybrid_search_weights(
        self,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, float]:
        """
        Get hybrid search weights with proper priority cascade.

        Returns a dictionary with 'similarity_weight' and 'keyword_weight'.
        """
        similarity_weight = self.get_hybrid_similarity_weight(kb_config)
        keyword_weight = self.get_hybrid_keyword_weight(kb_config)

        return {
            "similarity_weight": similarity_weight,
            "keyword_weight": keyword_weight
        }

    # Title Search Configuration Methods
    def get_title_weighting_enabled(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Get whether title weighting is enabled."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("title_weighting_enabled") is not None:
            return bool(kb_config["title_weighting_enabled"])
        if model_config and model_config.get("title_weighting_enabled") is not None:
            return bool(model_config["title_weighting_enabled"])
        return self.settings.title_weighting_enabled_default

    def get_title_weight_multiplier(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> float:
        """Get title weight multiplier."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("title_weight_multiplier") is not None:
            return float(kb_config["title_weight_multiplier"])
        if model_config and model_config.get("title_weight_multiplier") is not None:
            return float(model_config["title_weight_multiplier"])
        return self.settings.title_weight_multiplier_default

    def get_title_chunk_enabled(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Get whether title chunks are enabled."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("title_chunk_enabled") is not None:
            return bool(kb_config["title_chunk_enabled"])
        if model_config and model_config.get("title_chunk_enabled") is not None:
            return bool(model_config["title_chunk_enabled"])
        return self.settings.title_chunk_enabled_default

    def get_max_chunks_per_document(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get maximum chunks per document."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("max_chunks_per_document") is not None:
            return int(kb_config["max_chunks_per_document"])
        if model_config and model_config.get("max_chunks_per_document") is not None:
            return int(model_config["max_chunks_per_document"])
        return self.settings.max_chunks_per_document_default

    def get_rag_minimum_query_words(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get minimum query words required for RAG processing."""
        # Skip user preferences for RAG settings
        if kb_config and kb_config.get("minimum_query_words") is not None:
            return int(kb_config["minimum_query_words"])
        if model_config and model_config.get("minimum_query_words") is not None:
            return int(model_config["minimum_query_words"])
        return self.settings.rag_minimum_query_words_default

    # Utility Methods
    def get_rag_config_dict(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get complete RAG configuration as a dictionary.

        This replaces all the hardcoded default dictionaries scattered
        throughout the codebase (like in query_service.py, chat_service.py, etc.)
        """
        return {
            "include_references": self.get_rag_include_references(user_prefs, model_config, kb_config),
            "reference_format": self.get_rag_reference_format(user_prefs, model_config, kb_config),
            "context_format": self.get_rag_context_format(user_prefs, model_config, kb_config),
            "prompt_template": self.settings.rag_prompt_template_default,  # Always use default for now
            "search_threshold": self.get_rag_search_threshold(user_prefs, model_config, kb_config),
            "max_results": self.get_rag_max_results(user_prefs, model_config, kb_config),
            "chunk_overlap_ratio": self.get_rag_chunk_overlap_ratio(user_prefs, model_config, kb_config),
            "search_type": self.get_rag_search_type(user_prefs, model_config, kb_config),
            # Title search
            "title_weighting_enabled": self.get_title_weighting_enabled(user_prefs, model_config, kb_config),
            "title_weight_multiplier": self.get_title_weight_multiplier(user_prefs, model_config, kb_config),
            "title_chunk_enabled": self.get_title_chunk_enabled(user_prefs, model_config, kb_config),

            # Chunking
            "max_chunks_per_document": self.get_max_chunks_per_document(user_prefs, model_config, kb_config),

            # Query
            "minimum_query_words": self.get_rag_minimum_query_words(user_prefs, model_config, kb_config),

            # Full document escalation
            "fetch_full_documents": self.get_full_document_enabled(user_prefs, model_config, kb_config),
            "full_doc_max_docs": self.get_full_document_max_docs(user_prefs, model_config, kb_config),
            "full_doc_token_cap": self.get_full_document_token_cap(user_prefs, model_config, kb_config),

            # Version
            "version": "1.0",
        }


    def get_llm_config_dict(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Return the resolved LLM configuration built from optional user, model, and KB overrides.
        
        Parameters:
            user_prefs (Optional[Dict[str, Any]]): User-specific LLM preferences that can override model or KB settings.
            model_config (Optional[Dict[str, Any]]): Model-specific LLM configuration that can override KB defaults.
            kb_config (Optional[Dict[str, Any]]): Knowledge-base-specific LLM configuration with the lowest override precedence.
        
        Returns:
            Dict[str, Any]: Dictionary with keys:
                - "temperature" (float): Resolved sampling temperature.
                - "max_tokens" (int): Resolved maximum token count for responses.
                - "timeout" (float): Global LLM request timeout from settings.
        
        Notes:
            Rate limits are provider-specific and are not included in this dictionary.
        """
        return {
            "temperature": self.get_llm_temperature(user_prefs, model_config, kb_config),
            "max_tokens": self.get_llm_max_tokens(user_prefs, model_config, kb_config),
            "timeout": self.settings.llm_global_timeout,
        }

    def get_user_preferences_dict(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Assembles the effective user-controllable preferences by resolving available overrides.
        
        Parameters:
            user_prefs (Optional[Dict[str, Any]]): User-provided preference overrides.
            model_config (Optional[Dict[str, Any]]): Model-level preference overrides.
            kb_config (Optional[Dict[str, Any]]): Knowledge-base-level preference overrides.
        
        Returns:
            Dict[str, Any]: Dictionary with keys `memory_depth`, `memory_similarity_threshold`, `theme`, `language`, and `timezone`, resolved with priority: user_prefs → model_config → kb_config → global defaults.
        """
        return {
            # Memory settings (legitimate user preferences)
            "memory_depth": self.get_user_memory_depth(user_prefs, model_config, kb_config),
            "memory_similarity_threshold": self.get_user_memory_similarity_threshold(user_prefs, model_config, kb_config),

            # UI/UX preferences (legitimate user preferences)
            "theme": self.get_user_theme(user_prefs, model_config, kb_config),
            "language": self.get_user_language(user_prefs, model_config, kb_config),
            "timezone": self.get_user_timezone(user_prefs, model_config, kb_config),
        }

    # Full Document Escalation Methods
    def get_full_document_enabled(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        if kb_config and kb_config.get("fetch_full_documents") is not None:
            return bool(kb_config["fetch_full_documents"])
        if model_config and model_config.get("fetch_full_documents") is not None:
            return bool(model_config["fetch_full_documents"])
        return self.settings.rag_full_doc_fetch_default

    def get_full_document_max_docs(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        if kb_config and kb_config.get("full_doc_max_docs") is not None:
            return int(kb_config["full_doc_max_docs"])
        if model_config and model_config.get("full_doc_max_docs") is not None:
            return int(model_config["full_doc_max_docs"])
        return self.settings.rag_full_doc_max_docs_default

    def get_full_document_token_cap(
        self,
        user_prefs: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, Any]] = None,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        if kb_config and kb_config.get("full_doc_token_cap") is not None:
            return int(kb_config["full_doc_token_cap"])
        if model_config and model_config.get("full_doc_token_cap") is not None:
            return int(model_config["full_doc_token_cap"])
        return self.settings.rag_full_doc_token_cap_default

    # Text Extraction Configuration (System-level, not user-configurable)
    def get_text_extraction_timeout(
        self,
        use_ocr: bool = True,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Get text extraction timeout based on processing method.

        Args:
            use_ocr: Whether OCR processing is being used
            kb_config: Knowledge base configuration (for future per-KB timeout overrides)

        Returns:
            Timeout in seconds
        """
        # Future: Could allow KB-level timeout overrides
        if kb_config and "text_extraction_timeout" in kb_config:
            return kb_config["text_extraction_timeout"]

        # Use OCR timeout for OCR processing, fast timeout for text extraction
        if use_ocr:
            return self.settings.text_extraction_timeout_default
        else:
            return self.settings.text_extraction_fast_timeout_default

    def get_text_extraction_configuration(
        self,
        use_ocr: bool = True,
        kb_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get complete text extraction configuration.

        Args:
            use_ocr: Whether OCR processing is being used
            kb_config: Knowledge base configuration

        Returns:
            Dictionary with text extraction configuration
        """
        return {
            "timeout": self.get_text_extraction_timeout(use_ocr, kb_config),
            "ocr_execution_mode": self.settings.ocr_execution_mode,
            "max_file_size": self.settings.max_file_size,
            "processing_method": "ocr" if use_ocr else "fast_extraction"
        }


# Global configuration manager instance (for backward compatibility)
_config_manager: Optional[ConfigurationManager] = None


def get_config_manager() -> ConfigurationManager:
    """
    Get the global configuration manager instance.

    Note: This function provides backward compatibility for existing code.
    For new code, prefer dependency injection using get_config_manager_dependency().
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigurationManager(get_settings_instance())
    return _config_manager


def get_config_manager_dependency() -> ConfigurationManager:
    """
    Dependency injection function for ConfigurationManager.

    Use this in FastAPI endpoints and services for better testability:

    ```python
    async def some_endpoint(
        config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
    ):
        config = await config_manager.get_rag_configuration(kb_id, user_id)
    ```
    """
    return ConfigurationManager(get_settings_instance())