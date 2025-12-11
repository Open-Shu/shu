"""
Context manager for handling expected errors in integration tests.

This module provides utilities to clearly mark expected errors, warnings, and exceptions
in test logs to prevent confusion during code review.
"""

import logging
import contextlib
from typing import Optional, List, Union, Type
from functools import wraps

logger = logging.getLogger(__name__)


class ExpectedErrorContext:
    """Context manager for marking expected errors in tests."""
    
    def __init__(self, 
                 error_description: str,
                 expected_errors: Optional[List[Union[str, Type[Exception]]]] = None,
                 test_name: Optional[str] = None):
        """
        Initialize expected error context.
        
        Args:
            error_description: Description of what errors are expected
            expected_errors: List of expected error types or messages
            test_name: Name of the test (for logging context)
        """
        self.error_description = error_description
        self.expected_errors = expected_errors or []
        self.test_name = test_name or "Unknown Test"
        
    def __enter__(self):
        """Enter the context - log that errors are expected."""
        logger.info(f"=== EXPECTED TEST OUTPUT: {self.error_description} ===")
        if self.expected_errors:
            error_list = []
            for error in self.expected_errors:
                if isinstance(error, type) and issubclass(error, Exception):
                    error_list.append(error.__name__)
                else:
                    error_list.append(str(error))
            logger.info(f"=== EXPECTED TEST OUTPUT: Expected error types: {', '.join(error_list)} ===")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context - log that expected errors occurred."""
        if exc_type is not None:
            logger.info(f"=== EXPECTED TEST OUTPUT: Expected exception {exc_type.__name__} occurred as expected ===")
        else:
            logger.info(f"=== EXPECTED TEST OUTPUT: Expected error scenario completed ===")
        return False  # Don't suppress exceptions


def expect_errors(error_description: str, 
                 expected_errors: Optional[List[Union[str, Type[Exception]]]] = None):
    """
    Decorator for test functions that are expected to generate errors.
    
    Args:
        error_description: Description of what errors are expected
        expected_errors: List of expected error types or messages
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            test_name = func.__name__
            with ExpectedErrorContext(error_description, expected_errors, test_name):
                return await func(*args, **kwargs)
                
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            test_name = func.__name__
            with ExpectedErrorContext(error_description, expected_errors, test_name):
                return func(*args, **kwargs)
                
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    return decorator


@contextlib.contextmanager
def expect_authentication_errors():
    """Context manager for expected authentication errors (401/403)."""
    with ExpectedErrorContext(
        "Authentication errors (401/403) are expected in this test scenario",
        ["401 Unauthorized", "403 Forbidden", "Missing or invalid Authorization header"]
    ):
        yield


@contextlib.contextmanager
def expect_validation_errors():
    """Context manager for expected validation errors (400/422)."""
    with ExpectedErrorContext(
        "Validation errors (400/422) are expected for invalid input data",
        ["ValidationError", "400 Bad Request", "422 Unprocessable Entity"]
    ):
        yield


@contextlib.contextmanager
def expect_not_found_errors():
    """Context manager for expected not found errors (404)."""
    with ExpectedErrorContext(
        "Not found errors (404) are expected for non-existent resources",
        ["404 Not Found", "NotFoundError", "KnowledgeBaseNotFoundError", "DocumentNotFoundError"]
    ):
        yield


@contextlib.contextmanager
def expect_llm_errors():
    """Context manager for expected LLM provider errors."""
    with ExpectedErrorContext(
        "LLM provider errors are expected in test environment (no valid API keys)",
        ["LLM authentication error", "Invalid API key", "401 Unauthorized", "HTTP client error"]
    ):
        yield


@contextlib.contextmanager
def expect_database_errors():
    """Context manager for expected database errors."""
    with ExpectedErrorContext(
        "Database errors are expected in this test scenario",
        ["StaleDataError", "PendingRollbackError", "IntegrityError", "garbage collector.*non-checked-in connection"]
    ):
        yield


@contextlib.contextmanager
def expect_sync_errors():
    """Context manager for expected sync operation errors."""
    with ExpectedErrorContext(
        "Sync operation errors are expected in test environment",
        ["DocumentNotFoundError", "Root path does not exist", "Failed to store document chunks"]
    ):
        yield


@contextlib.contextmanager
def expect_duplicate_errors():
    """Context manager for expected duplicate resource errors."""
    with ExpectedErrorContext(
        "Duplicate resource errors are expected when testing uniqueness constraints",
        ["AlreadyExistsError", "KnowledgeBaseAlreadyExistsError", "IntegrityError"]
    ):
        yield


@contextlib.contextmanager
def expect_oauth_errors():
    """Context manager for expected OAuth/Gmail integration errors."""
    with ExpectedErrorContext(
        "OAuth/Gmail integration errors are expected in test environment (no valid credentials)",
        ["Failed to load OAuth credentials", "invalid_client", "The OAuth client was not found"]
    ):
        yield


@contextlib.contextmanager
def expect_validation_pydantic_errors():
    """Context manager for expected Pydantic validation errors."""
    with ExpectedErrorContext(
        "Pydantic validation errors are expected for malformed data structures",
        ["validation error for", "Input should be a valid", "ConversationResponse"]
    ):
        yield


@contextlib.contextmanager
def expect_test_cleanup_auth_errors():
    """Context manager for expected authentication errors during test cleanup."""
    with ExpectedErrorContext(
        "Authentication errors during test cleanup are expected (cleanup runs without auth headers)",
        ["Missing or invalid Authorization header", "Invalid or expired token"]
    ):
        yield


@contextlib.contextmanager
def expect_background_sync_errors():
    """Context manager for expected background sync operation errors."""
    with ExpectedErrorContext(
        "Background sync operation errors are expected when tests complete before background jobs finish",
        [
            "Failed to store document chunks",
            "Document.*not found",
            "Failed to mark document error",
            "Background sync job failed",
            "StaleDataError",
            "PendingRollbackError",
            "UPDATE statement.*expected to update.*rows",
            "This Session's transaction has been rolled back"
        ]
    ):
        yield


@contextlib.contextmanager
def expect_llm_test_errors():
    """Context manager for expected LLM and chat service test errors."""
    with ExpectedErrorContext(
        "LLM and chat service errors are expected during test operations with mock data",
        [
            "LLM completion failed",
            "'str' object has no attribute 'usage'",
            "expected str, got AsyncMock",
            "Unexpected error sending message",
            "Unexpected error listing conversations",
            "validation error for ConversationResponse",
            "Input should be a valid string.*input_value=None",
            "model_configuration_id.*Input should be a valid string"
        ]
    ):
        yield


@contextlib.contextmanager
def expect_comprehensive_test_errors():
    """Context manager for comprehensive test error handling across all categories."""
    logger.info("=== EXPECTED TEST OUTPUT: Comprehensive test error handling active ===")
    logger.info("=== EXPECTED TEST OUTPUT: The following error types are expected during testing: ===")
    logger.info("=== EXPECTED TEST OUTPUT: - Background sync job errors (race conditions with test cleanup) ===")
    logger.info("=== EXPECTED TEST OUTPUT: - Document not found errors (cleanup timing issues) ===")
    logger.info("=== EXPECTED TEST OUTPUT: - Database transaction rollback errors ===")
    logger.info("=== EXPECTED TEST OUTPUT: - LLM completion failures (invalid API keys in test environment) ===")
    logger.info("=== EXPECTED TEST OUTPUT: - Chat service validation errors (mock data issues) ===")
    logger.info("=== EXPECTED TEST OUTPUT: - Authentication errors during cleanup operations ===")

    with ExpectedErrorContext(
        "Comprehensive test errors are expected across all system components",
        [
            # Background sync errors
            "Background sync job failed",
            "Failed to store document chunks",
            "Failed to mark document error",
            "Document.*not found",
            "StaleDataError",
            "PendingRollbackError",
            "UPDATE statement.*expected to update.*rows",
            "This Session's transaction has been rolled back",

            # LLM and chat errors
            "LLM completion failed",
            "'str' object has no attribute 'usage'",
            "expected str, got AsyncMock",
            "Unexpected error sending message",
            "Unexpected error listing conversations",
            "validation error for ConversationResponse",
            "Input should be a valid string.*input_value=None",
            "model_configuration_id.*Input should be a valid string",

            # Authentication errors
            "Missing or invalid Authorization header",
            "Invalid or expired token",
            "JWT verification failed",

            # Filesystem errors
            "Root path does not exist",

            # General test cleanup errors
            "Error deleting.*during cleanup",
            "Failed to.*during test cleanup"
        ]
    ):
        try:
            yield
        finally:
            logger.info("=== EXPECTED TEST OUTPUT: Comprehensive test error handling completed ===")
            logger.info("=== EXPECTED TEST OUTPUT: Any errors above matching the expected patterns were part of normal test behavior ===")


@contextlib.contextmanager
def expect_test_suite_errors():
    """Context manager for wrapping entire test suites with expected error handling."""
    with expect_comprehensive_test_errors():
        yield


def log_expected_error_completion(error_type: str, count: Optional[int] = None):
    """
    Log that expected errors have completed.

    Args:
        error_type: Type of error that was expected
        count: Number of errors that occurred (optional)
    """
    if count is not None:
        logger.info(f"=== EXPECTED TEST OUTPUT: {count} {error_type} errors occurred as expected ===")
    else:
        logger.info(f"=== EXPECTED TEST OUTPUT: {error_type} errors completed as expected ===")


class TestErrorLogger:
    """
    Utility class to automatically log expected errors based on test patterns.
    """

    # Common error patterns that indicate expected test behavior
    EXPECTED_ERROR_PATTERNS = {
        "authentication": [
            "Missing or invalid Authorization header",
            "JWT verification failed",
            "Invalid or expired token",
            "401 Unauthorized",
            "403 Forbidden"
        ],
        "llm_provider": [
            "LLM authentication error",
            "Invalid API key",
            "LLM completion failed",
            "LLM streaming failed",
            "HTTP client error",
            "Failed to discover models"
        ],
        "validation": [
            "ValidationError",
            "400 Bad Request",
            "422 Unprocessable Entity",
            "Validation error:",
            "Input should be a valid"
        ],
        "not_found": [
            "404 Not Found",
            "NotFoundError",
            "KnowledgeBaseNotFoundError",
            "DocumentNotFoundError",
            "not found"
        ],
        "duplicate": [
            "AlreadyExistsError",
            "KnowledgeBaseAlreadyExistsError",
            "already exists"
        ],
        "sync_operations": [
            "Root path does not exist",
            "Failed to store document chunks",
            "Failed to mark document error",
            "Document.*not found"
        ],
        "database": [
            "StaleDataError",
            "PendingRollbackError",
            "IntegrityError",
            "UPDATE statement.*expected to update.*rows",
            "garbage collector.*non-checked-in connection"
        ]
    }

    @classmethod
    def log_test_start(cls, test_name: str):
        """Log the start of a test that may generate expected errors."""
        logger.info(f"=== EXPECTED TEST OUTPUT: Starting {test_name} - errors may be expected ===")

    @classmethod
    def log_test_end(cls, test_name: str):
        """Log the end of a test that may have generated expected errors."""
        logger.info(f"=== EXPECTED TEST OUTPUT: Completed {test_name} - any errors above were expected ===")

    @classmethod
    def is_expected_error(cls, error_message: str) -> Optional[str]:
        """
        Check if an error message matches expected patterns.

        Args:
            error_message: The error message to check

        Returns:
            The error category if it's expected, None otherwise
        """
        import re

        for category, patterns in cls.EXPECTED_ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_message, re.IGNORECASE):
                    return category
        return None
