"""
Unit tests for chat API error sanitization.

Tests the _sanitize_chat_error_message function to ensure it properly
preserves rate limit, timeout, and service unavailable errors while
sanitizing all other errors (including config and DB errors).
"""

from shu.api.chat import _sanitize_chat_error_message


class TestChatErrorSanitization:
    """Test suite for chat error message sanitization."""

    def test_sanitize_empty_error(self) -> None:
        """Test sanitization of empty error message."""
        result = _sanitize_chat_error_message("")
        assert result == "The request failed. You may want to try another model."

    # Rate limit errors - PRESERVE
    def test_preserve_rate_limit_error_lowercase(self) -> None:
        """Test that rate limit errors are preserved (lowercase)."""
        error = "Provider rate limit exceeded (3500 RPM). Retry after 30s."
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_rate_limit_error_uppercase(self) -> None:
        """Test that rate limit errors are preserved (uppercase)."""
        error = "RATE LIMIT EXCEEDED. Please try again later."
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_too_many_requests_error(self) -> None:
        """Test that 'too many requests' errors are preserved."""
        error = "Too many requests. Please wait before trying again."
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_rate_limit_with_retry_after(self) -> None:
        """Test that rate limit errors with retry_after are preserved."""
        error = "Provider token rate limit exceeded (90000 TPM). Retry after 45s."
        result = _sanitize_chat_error_message(error)
        assert result == error

    # Timeout errors - PRESERVE
    def test_preserve_timeout_error_lowercase(self) -> None:
        """Test that timeout errors are preserved (lowercase)."""
        error = "Request timeout after 30 seconds"
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_timeout_error_uppercase(self) -> None:
        """Test that timeout errors are preserved (uppercase)."""
        error = "REQUEST TIMEOUT"
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_timed_out_error(self) -> None:
        """Test that 'timed out' errors are preserved."""
        error = "The request timed out after 60 seconds"
        result = _sanitize_chat_error_message(error)
        assert result == error

    # Service unavailable errors - PRESERVE
    def test_preserve_service_unavailable_error(self) -> None:
        """Test that service unavailable errors are preserved."""
        error = "Service unavailable. Please try again later."
        result = _sanitize_chat_error_message(error)
        assert result == error

    def test_preserve_temporarily_unavailable_error(self) -> None:
        """Test that temporarily unavailable errors are preserved."""
        error = "The service is temporarily unavailable due to maintenance."
        result = _sanitize_chat_error_message(error)
        assert result == error

    # All other errors - SANITIZE
    def test_sanitize_api_key_error(self) -> None:
        """Test that API key errors are sanitized."""
        error = "Invalid API key provided: sk-proj-abc123..."
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_authentication_error(self) -> None:
        """Test that authentication errors are sanitized."""
        error = "Authentication failed: Invalid credentials"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_malformed_request_error(self) -> None:
        """Test that malformed request errors are sanitized."""
        error = "Invalid request: Missing required field 'model'"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_provider_error(self) -> None:
        """Test that generic provider errors are sanitized."""
        error = "OpenAI API error: Service error"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_model_not_found_error(self) -> None:
        """Test that model not found errors are sanitized."""
        error = "Model 'gpt-5' not found. Please check the model name."
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_config_error(self) -> None:
        """Test that config errors are sanitized (per user requirement)."""
        error = "Model configuration 'test-config' is not active"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_database_error(self) -> None:
        """Test that database errors are sanitized (per user requirement)."""
        error = "Database connection error: Could not connect to PostgreSQL"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    def test_sanitize_provider_inactive_error(self) -> None:
        """Test that provider inactive errors are sanitized (per user requirement)."""
        error = "Provider is inactive and cannot be used"
        result = _sanitize_chat_error_message(error)
        assert result == "The request failed. You may want to try another model."

    # Case insensitivity tests
    def test_case_insensitive_rate_limit_detection(self) -> None:
        """Test that rate limit detection is case-insensitive."""
        test_cases = [
            "rate limit exceeded",
            "Rate Limit Exceeded",
            "RATE LIMIT EXCEEDED",
        ]
        for error in test_cases:
            result = _sanitize_chat_error_message(error)
            assert result == error, f"Failed to preserve: {error}"

    def test_case_insensitive_timeout_detection(self) -> None:
        """Test that timeout detection is case-insensitive."""
        test_cases = [
            "timeout occurred",
            "Timeout Occurred",
            "TIMEOUT OCCURRED",
        ]
        for error in test_cases:
            result = _sanitize_chat_error_message(error)
            assert result == error, f"Failed to preserve: {error}"

    def test_case_insensitive_service_unavailable_detection(self) -> None:
        """Test that service unavailable detection is case-insensitive."""
        test_cases = [
            "service unavailable",
            "Service Unavailable",
            "SERVICE UNAVAILABLE",
        ]
        for error in test_cases:
            result = _sanitize_chat_error_message(error)
            assert result == error, f"Failed to preserve: {error}"
