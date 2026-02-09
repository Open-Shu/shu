"""
Comprehensive Error Logger for Integration Tests

This module provides automatic expected error logging for entire test suites,
wrapping test execution with appropriate error context based on test patterns.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from functools import wraps

from integ.expected_error_context import (
    expect_authentication_errors,
    expect_database_errors,
    expect_duplicate_errors,
    expect_llm_errors,
    expect_not_found_errors,
    expect_oauth_errors,
    expect_sync_errors,
    expect_test_cleanup_auth_errors,
    expect_validation_errors,
    expect_validation_pydantic_errors,
)

logger = logging.getLogger(__name__)


class ComprehensiveErrorLogger:
    """
    Comprehensive error logging system that automatically wraps test functions
    with appropriate expected error contexts based on test patterns and content.
    """

    # Test suite patterns that indicate specific error types
    SUITE_ERROR_PATTERNS = {
        "sync": ["sync_errors", "database_errors", "not_found_errors"],
        "chat": ["llm_errors", "validation_pydantic_errors"],
        "auth": ["authentication_errors", "test_cleanup_auth_errors"],
        "knowledge_base": ["duplicate_errors", "validation_errors", "not_found_errors"],
        "llm": ["llm_errors", "duplicate_errors", "authentication_errors"],
        "gmail": ["oauth_errors", "authentication_errors"],
        "model_configuration": ["duplicate_errors", "not_found_errors"],
        "prompt": ["not_found_errors", "validation_errors"],
        "query": ["authentication_errors", "validation_errors"],
        "kb_sources": ["not_found_errors", "validation_errors"],
        "rbac": ["authentication_errors", "validation_errors"],
    }

    # Function name patterns that indicate specific error types
    FUNCTION_ERROR_PATTERNS = {
        "authentication_errors": [
            r".*unauthorized.*",
            r".*auth.*invalid.*",
            r".*missing.*auth.*",
            r".*invalid.*token.*",
            r".*malformed.*auth.*",
        ],
        "validation_errors": [
            r".*invalid.*data.*",
            r".*validation.*",
            r".*malformed.*",
            r".*empty.*data.*",
            r".*bad.*request.*",
        ],
        "not_found_errors": [
            r".*not.*found.*",
            r".*invalid.*id.*",
            r".*nonexistent.*",
            r".*fake.*id.*",
            r".*missing.*resource.*",
        ],
        "duplicate_errors": [r".*duplicate.*", r".*already.*exists.*", r".*same.*name.*"],
        "llm_errors": [
            r".*send.*message.*",
            r".*llm.*",
            r".*chat.*completion.*",
            r".*streaming.*",
            r".*model.*discovery.*",
        ],
        "sync_errors": [
            r".*sync.*invalid.*",
            r".*sync.*error.*",
            r".*filesystem.*sync.*",
            r".*background.*sync.*",
        ],
        "database_errors": [r".*stale.*data.*", r".*rollback.*", r".*connection.*pool.*"],
        "oauth_errors": [r".*oauth.*", r".*gmail.*", r".*credentials.*"],
        "validation_pydantic_errors": [
            r".*pydantic.*",
            r".*conversation.*response.*",
            r".*validation.*error.*",
        ],
    }

    def __init__(self, suite_name: str | None = None):
        """
        Initialize the comprehensive error logger.

        Args:
            suite_name: Name of the test suite (for suite-level error patterns)
        """
        self.suite_name = suite_name
        self.active_contexts: list[str] = []

    def get_expected_error_types(self, test_function_name: str) -> list[str]:
        """
        Determine what error types are expected for a given test function.

        Args:
            test_function_name: Name of the test function

        Returns:
            List of error type names that are expected
        """
        error_types = set()

        # Add suite-level error types
        if self.suite_name and self.suite_name in self.SUITE_ERROR_PATTERNS:
            error_types.update(self.SUITE_ERROR_PATTERNS[self.suite_name])

        # Add function-level error types based on name patterns
        for error_type, patterns in self.FUNCTION_ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, test_function_name, re.IGNORECASE):
                    error_types.add(error_type)
                    break

        return list(error_types)

    @contextlib.contextmanager
    def wrap_test_execution(self, test_function_name: str):
        """
        Context manager that wraps test execution with appropriate error contexts.

        Args:
            test_function_name: Name of the test function being executed
        """
        error_types = self.get_expected_error_types(test_function_name)

        if not error_types:
            # No expected errors, just yield
            yield
            return

        logger.info(
            f"=== EXPECTED TEST OUTPUT: Starting {test_function_name} with expected error types: {', '.join(error_types)} ==="
        )

        # Create nested context managers for all expected error types
        contexts = []

        try:
            # Build nested context managers
            for error_type in error_types:
                if error_type == "authentication_errors":
                    contexts.append(expect_authentication_errors())
                elif error_type == "validation_errors":
                    contexts.append(expect_validation_errors())
                elif error_type == "not_found_errors":
                    contexts.append(expect_not_found_errors())
                elif error_type == "duplicate_errors":
                    contexts.append(expect_duplicate_errors())
                elif error_type == "llm_errors":
                    contexts.append(expect_llm_errors())
                elif error_type == "sync_errors":
                    contexts.append(expect_sync_errors())
                elif error_type == "database_errors":
                    contexts.append(expect_database_errors())
                elif error_type == "oauth_errors":
                    contexts.append(expect_oauth_errors())
                elif error_type == "validation_pydantic_errors":
                    contexts.append(expect_validation_pydantic_errors())
                elif error_type == "test_cleanup_auth_errors":
                    contexts.append(expect_test_cleanup_auth_errors())

            # Enter all contexts
            entered_contexts = []
            for context in contexts:
                entered_contexts.append(context.__enter__())

            yield

        except Exception:
            # Let the contexts handle the exception
            raise
        finally:
            # Exit all contexts in reverse order
            for context in reversed(contexts):
                try:
                    context.__exit__(None, None, None)
                except Exception:
                    pass  # Ignore context exit errors

            logger.info(
                f"=== EXPECTED TEST OUTPUT: Completed {test_function_name} - any errors above were expected ==="
            )

    def wrap_test_function(self, test_func: Callable) -> Callable:
        """
        Decorator that wraps a test function with comprehensive error logging.

        Args:
            test_func: The test function to wrap

        Returns:
            Wrapped test function with error logging
        """

        @wraps(test_func)
        async def async_wrapper(*args, **kwargs):
            with self.wrap_test_execution(test_func.__name__):
                return await test_func(*args, **kwargs)

        @wraps(test_func)
        def sync_wrapper(*args, **kwargs):
            with self.wrap_test_execution(test_func.__name__):
                return test_func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(test_func):
            return async_wrapper
        return sync_wrapper

    def wrap_test_suite(self, test_functions: list[Callable]) -> list[Callable]:
        """
        Wrap all test functions in a test suite with comprehensive error logging.

        Args:
            test_functions: List of test functions to wrap

        Returns:
            List of wrapped test functions
        """
        wrapped_functions = []
        for test_func in test_functions:
            wrapped_functions.append(self.wrap_test_function(test_func))
        return wrapped_functions


# Global error logger instances for different test suites
_error_loggers: dict[str, ComprehensiveErrorLogger] = {}


def get_error_logger(suite_name: str) -> ComprehensiveErrorLogger:
    """
    Get or create an error logger for a specific test suite.

    Args:
        suite_name: Name of the test suite

    Returns:
        ComprehensiveErrorLogger instance for the suite
    """
    if suite_name not in _error_loggers:
        _error_loggers[suite_name] = ComprehensiveErrorLogger(suite_name)
    return _error_loggers[suite_name]


def comprehensive_error_logging(suite_name: str):
    """
    Decorator for test functions that automatically applies comprehensive error logging.

    Args:
        suite_name: Name of the test suite
    """

    def decorator(test_func: Callable) -> Callable:
        error_logger = get_error_logger(suite_name)
        return error_logger.wrap_test_function(test_func)

    return decorator
