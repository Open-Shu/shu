"""Custom exceptions for Shu RAG Backend.

This module defines all custom exceptions used throughout the application.
"""

from typing import Any


class ShuException(Exception):
    """Base exception class for Shu RAG Backend."""

    def __init__(
        self,
        message: str,
        error_code: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


# Processing Exceptions
class ProcessingError(ShuException):
    """Raised when there's an error processing a document."""

    def __init__(self, document_id: str, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Error processing document '{document_id}': {reason}",
            error_code="DOCUMENT_PROCESSING_ERROR",
            status_code=500,
            details=details or {"document_id": document_id, "reason": reason},
        )


class FileTooLargeError(ShuException):
    """Raised when a file exceeds the maximum allowed size."""

    def __init__(self, file_path: str, size: int, max_size: int, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"File '{file_path}' size ({size} bytes) exceeds maximum allowed size ({max_size} bytes)",
            error_code="FILE_TOO_LARGE",
            status_code=400,
            details=details or {"file_path": file_path, "size": size, "max_size": max_size},
        )


# Knowledge Base Exceptions
class KnowledgeBaseNotFoundError(ShuException):
    """Raised when a knowledge base is not found."""

    def __init__(self, knowledge_base_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base '{knowledge_base_id}' not found",
            error_code="KNOWLEDGE_BASE_NOT_FOUND",
            status_code=404,
            details=details or {"knowledge_base_id": knowledge_base_id},
        )


class KnowledgeBaseAlreadyExistsError(ShuException):
    """Raised when trying to create a knowledge base that already exists."""

    def __init__(self, knowledge_base_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base '{knowledge_base_id}' already exists",
            error_code="KNOWLEDGE_BASE_ALREADY_EXISTS",
            status_code=409,
            details=details or {"knowledge_base_id": knowledge_base_id},
        )


class InvalidKnowledgeBaseConfigError(ShuException):
    """Raised when knowledge base configuration is invalid."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Invalid knowledge base configuration: {message}",
            error_code="INVALID_KNOWLEDGE_BASE_CONFIG",
            status_code=400,
            details=details,
        )


# Document Exceptions
class DocumentNotFoundError(ShuException):
    """Raised when a document is not found."""

    def __init__(self, document_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Document '{document_id}' not found",
            error_code="DOCUMENT_NOT_FOUND",
            status_code=404,
            details=details or {"document_id": document_id},
        )


# Sync Job Exceptions
class SyncJobNotFoundError(ShuException):
    """Raised when a sync job is not found."""

    def __init__(self, job_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Sync job '{job_id}' not found",
            error_code="SYNC_JOB_NOT_FOUND",
            status_code=404,
            details=details or {"job_id": job_id},
        )


class SyncJobAlreadyRunningError(ShuException):
    """Raised when trying to start a sync job that is already running."""

    def __init__(self, knowledge_base_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Sync job for knowledge base '{knowledge_base_id}' is already running",
            error_code="SYNC_JOB_ALREADY_RUNNING",
            status_code=409,
            details=details or {"knowledge_base_id": knowledge_base_id},
        )


class SyncJobFailedError(ShuException):
    """Raised when a sync job fails."""

    def __init__(self, job_id: str, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Sync job '{job_id}' failed: {reason}",
            error_code="SYNC_JOB_FAILED",
            status_code=500,
            details=details or {"job_id": job_id, "reason": reason},
        )


# Embedding Model Exceptions
class EmbeddingModelError(ShuException):
    """Raised when there's an error with the embedding model."""

    def __init__(self, model_name: str, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Embedding model '{model_name}' error: {reason}",
            error_code="EMBEDDING_MODEL_ERROR",
            status_code=500,
            details=details or {"model_name": model_name, "reason": reason},
        )


# Google Drive Exceptions
class GoogleDriveError(ShuException):
    """Raised when there's an error with Google Drive operations."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Google Drive error: {reason}",
            error_code="GOOGLE_DRIVE_ERROR",
            status_code=500,
            details=details or {"reason": reason},
        )


# Database Exceptions - Granular Types
class DatabaseConnectionError(ShuException):
    """Raised when there's a database connection error (network, auth, etc.)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database connection error: {reason}",
            error_code="DATABASE_CONNECTION_ERROR",
            status_code=503,  # Service Unavailable
            details=details or {"reason": reason},
        )


class DatabaseInitializationError(ShuException):
    """Raised when database initialization fails (schema creation, etc.)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database initialization error: {reason}",
            error_code="DATABASE_INITIALIZATION_ERROR",
            status_code=500,
            details=details or {"reason": reason},
        )


class DatabaseQueryError(ShuException):
    """Raised when a database query fails (syntax, permissions, etc.)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database query error: {reason}",
            error_code="DATABASE_QUERY_ERROR",
            status_code=500,
            details=details or {"reason": reason},
        )


class DatabaseConstraintError(ShuException):
    """Raised when a database constraint is violated (unique, foreign key, etc.)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database constraint error: {reason}",
            error_code="DATABASE_CONSTRAINT_ERROR",
            status_code=400,  # Bad Request
            details=details or {"reason": reason},
        )


class DatabaseTransactionError(ShuException):
    """Raised when a database transaction fails (deadlock, timeout, etc.)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database transaction error: {reason}",
            error_code="DATABASE_TRANSACTION_ERROR",
            status_code=500,
            details=details or {"reason": reason},
        )


class DatabaseSessionError(ShuException):
    """Raised when there's an error with database session management."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database session error: {reason}",
            error_code="DATABASE_SESSION_ERROR",
            status_code=500,
            details=details or {"reason": reason},
        )


# Generic Exceptions
class NotFoundError(ShuException):
    """Generic exception for when a resource is not found."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="NOT_FOUND",
            status_code=404,
            details=details,
        )


class ConflictError(ShuException):
    """Generic exception for when a resource conflict occurs."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="CONFLICT",
            status_code=409,
            details=details,
        )


# Validation and Authentication Exceptions
class ValidationError(ShuException):
    """Raised when input validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Validation error: {message}",
            error_code="VALIDATION_ERROR",
            status_code=400,
            details=details,
        )


class AuthenticationError(ShuException):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="AUTHENTICATION_ERROR",
            status_code=401,
            details=details,
        )


class AuthorizationError(ShuException):
    """Raised when authorization fails."""

    def __init__(self, message: str = "Authorization failed", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="AUTHORIZATION_ERROR",
            status_code=403,
            details=details,
        )


class RateLimitExceededError(ShuException):
    """Raised when rate limits are exceeded."""

    def __init__(self, message: str = "Rate limit exceeded", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="RATE_LIMIT_EXCEEDED",
            status_code=429,
            details=details,
        )


class ServiceUnavailableError(ShuException):
    """Raised when a service is temporarily unavailable."""

    def __init__(self, service_name: str, reason: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Service '{service_name}' unavailable: {reason}",
            error_code="SERVICE_UNAVAILABLE",
            status_code=503,
            details=details or {"service_name": service_name, "reason": reason},
        )


# Knowledge Base Source Exceptions
class KnowledgeBaseSourceNotFoundError(ShuException):
    """Raised when a knowledge base source is not found."""

    def __init__(self, source_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base source '{source_id}' not found",
            error_code="KNOWLEDGE_BASE_SOURCE_NOT_FOUND",
            status_code=404,
            details=details or {"source_id": source_id},
        )


class KnowledgeBaseSourceAlreadyExistsError(ShuException):
    """Raised when trying to create a knowledge base source that already exists."""

    def __init__(self, source_name: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base source '{source_name}' already exists",
            error_code="KNOWLEDGE_BASE_SOURCE_ALREADY_EXISTS",
            status_code=409,
            details=details or {"source_name": source_name},
        )


# Knowledge Base Prompt Exceptions
class KnowledgeBasePromptNotFoundError(ShuException):
    """Raised when a knowledge base prompt is not found."""

    def __init__(self, prompt_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base prompt '{prompt_id}' not found",
            error_code="KNOWLEDGE_BASE_PROMPT_NOT_FOUND",
            status_code=404,
            details=details or {"prompt_id": prompt_id},
        )


class KnowledgeBasePromptAlreadyExistsError(ShuException):
    """Raised when trying to create a knowledge base prompt that already exists."""

    def __init__(self, prompt_name: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Knowledge base prompt '{prompt_name}' already exists",
            error_code="KNOWLEDGE_BASE_PROMPT_ALREADY_EXISTS",
            status_code=409,
            details=details or {"prompt_name": prompt_name},
        )


# LLM-specific exceptions
class LLMError(ShuException):
    """Base exception for LLM-related errors."""

    pass


class LLMProviderError(LLMError):
    """Exception raised when LLM provider operations fail."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"LLM provider error: {message}",
            error_code="LLM_PROVIDER_ERROR",
            status_code=500,
            details=details,
        )


class LLMConfigurationError(LLMError):
    """Exception raised when LLM configuration is invalid."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"LLM configuration error: {message}",
            error_code="LLM_CONFIGURATION_ERROR",
            status_code=400,
            details=details,
        )


class LLMAuthenticationError(LLMError):
    """Exception raised when LLM authentication fails."""

    def __init__(self, message: str = "LLM authentication failed", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="LLM_AUTHENTICATION_ERROR",
            status_code=401,
            details=details,
        )


class LLMRateLimitError(LLMError):
    """Exception raised when LLM rate limits are exceeded."""

    def __init__(self, message: str = "LLM rate limit exceeded", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="LLM_RATE_LIMIT_ERROR",
            status_code=429,
            details=details,
        )


class LLMTimeoutError(LLMError):
    """Exception raised when LLM requests timeout."""

    def __init__(self, message: str = "LLM request timeout", details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="LLM_TIMEOUT_ERROR",
            status_code=504,
            details=details,
        )


class LLMModelNotFoundError(LLMError):
    """Exception raised when requested LLM model is not found."""

    def __init__(self, model_name: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"LLM model '{model_name}' not found",
            error_code="LLM_MODEL_NOT_FOUND",
            status_code=404,
            details=details or {"model_name": model_name},
        )


# Chat-specific exceptions
class ConversationNotFoundError(ShuException):
    """Exception raised when a conversation is not found."""

    def __init__(self, conversation_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Conversation '{conversation_id}' not found",
            error_code="CONVERSATION_NOT_FOUND",
            status_code=404,
            details=details or {"conversation_id": conversation_id},
        )


class MessageNotFoundError(ShuException):
    """Exception raised when a message is not found."""

    def __init__(self, message_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Message '{message_id}' not found",
            error_code="MESSAGE_NOT_FOUND",
            status_code=404,
            details=details or {"message_id": message_id},
        )


# Prompt-specific exceptions
class PromptNotFoundError(ShuException):
    """Exception raised when a prompt is not found."""

    def __init__(self, prompt_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Prompt '{prompt_id}' not found",
            error_code="PROMPT_NOT_FOUND",
            status_code=404,
            details=details or {"prompt_id": prompt_id},
        )


class PromptAlreadyExistsError(ShuException):
    """Exception raised when trying to create a prompt that already exists."""

    def __init__(self, prompt_name: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Prompt '{prompt_name}' already exists",
            error_code="PROMPT_ALREADY_EXISTS",
            status_code=409,
            details=details or {"prompt_name": prompt_name},
        )


# Model Configuration-specific exceptions
class ModelConfigurationError(ShuException):
    """Base exception for model configuration-related errors."""

    pass


class ModelConfigurationNotFoundError(ModelConfigurationError):
    """Exception raised when a model configuration is not found."""

    def __init__(self, config_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Model configuration '{config_id}' not found",
            error_code="MODEL_CONFIGURATION_NOT_FOUND",
            status_code=404,
            details=details or {"config_id": config_id},
        )


class ModelConfigurationInactiveError(ModelConfigurationError):
    """Exception raised when a model configuration is inactive."""

    def __init__(self, config_name: str, config_id: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Model configuration '{config_name}' is not active and cannot be used for execution",
            error_code="MODEL_CONFIGURATION_INACTIVE",
            status_code=400,
            details=details or {"config_id": config_id, "config_name": config_name},
        )


class ModelConfigurationProviderInactiveError(ModelConfigurationError):
    """Exception raised when a model configuration's underlying provider is inactive."""

    def __init__(
        self,
        config_name: str,
        provider_name: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        message = f"Model configuration '{config_name}' cannot be used because its underlying LLM provider is inactive"
        if provider_name:
            message += f" (provider: '{provider_name}')"

        super().__init__(
            message=message,
            error_code="MODEL_CONFIGURATION_PROVIDER_INACTIVE",
            status_code=400,
            details=details or {"config_name": config_name, "provider_name": provider_name},
        )
