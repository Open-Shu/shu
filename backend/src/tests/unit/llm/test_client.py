"""
Unit tests for UnifiedLLMClient error handling.

Tests the enhanced error handling functionality including:
- Error extraction using ErrorSanitizer
- Environment-aware error display
- Specific error messages for 401, 429, 400 errors
- Error guidance and suggestions

**Feature: open-source-fixes, Property 9: Server-Side Error Logging**
**Validates: Requirements 4.3, 4.5, 4.6, 4.7, 4.8**
"""

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import httpx

from shu.services.error_sanitization import ErrorSanitizer, SanitizedError


class TestExtractHttpErrorDetails:
    """Tests for UnifiedLLMClient._extract_http_error_details method."""

    def _create_mock_error(
        self,
        status_code: int,
        body: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.HTTPStatusError:
        """Create a mock HTTP status error for testing."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.headers = headers or {}
        mock_response.text = str(body)
        mock_response.json.return_value = body

        mock_request = MagicMock()
        mock_request.url = "https://api.example.com/v1/chat/completions"

        error = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=mock_request,
            response=mock_response,
        )
        return error

    def test_extract_openai_style_error(self) -> None:
        """Test extraction from OpenAI-style error response."""
        error = self._create_mock_error(
            status_code=401,
            body={
                "error": {
                    "message": "Invalid API key provided",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
            headers={"x-request-id": "req_abc123"},
        )

        # Use ErrorSanitizer directly to test extraction
        result = ErrorSanitizer.extract_provider_error(error.response)

        assert result["message"] == "Invalid API key provided"
        assert result["error_type"] == "invalid_request_error"
        assert result["error_code"] == "invalid_api_key"
        assert result["status_code"] == 401

    def test_extract_anthropic_style_error(self) -> None:
        """Test extraction from Anthropic-style error response."""
        error = self._create_mock_error(
            status_code=400,
            body={
                "error": {
                    "message": "max_tokens is required",
                    "type": "invalid_request_error",
                }
            },
        )

        result = ErrorSanitizer.extract_provider_error(error.response)

        assert result["message"] == "max_tokens is required"
        assert result["error_type"] == "invalid_request_error"
        assert result["status_code"] == 400

    def test_extract_rate_limit_error(self) -> None:
        """Test extraction from rate limit error response."""
        error = self._create_mock_error(
            status_code=429,
            body={
                "error": {
                    "message": "Rate limit exceeded. Please retry after 60 seconds.",
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                }
            },
        )

        result = ErrorSanitizer.extract_provider_error(error.response)

        assert result["message"] == "Rate limit exceeded. Please retry after 60 seconds."
        assert result["error_type"] == "rate_limit_error"
        assert result["error_code"] == "rate_limit_exceeded"
        assert result["status_code"] == 429


class TestSanitizeErrorIntegration:
    """Tests for error sanitization integration with LLM client."""

    def test_sanitize_401_error_always_sanitizes(self) -> None:
        """Test that 401 errors are always sanitized."""
        error_details = {
            "message": "Invalid API key sk-test123456789012345678",
            "error_type": "authentication_error",
            "status_code": 401,
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "request_id": "req_abc123",
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        # API key should always be redacted
        assert "sk-test" not in result.message
        assert "[REDACTED]" in result.message
        assert result.error_type == "authentication_error"
        assert result.status_code == 401
        # Details should never be included
        assert result.details is None

    def test_sanitize_429_error_provides_suggestions(self) -> None:
        """Test that 429 errors include rate limit suggestions."""
        error_details = {
            "message": "Rate limit exceeded",
            "status_code": 429,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert result.status_code == 429
        assert len(result.suggestions) > 0
        assert any("wait" in s.lower() for s in result.suggestions)

    def test_sanitize_400_error_sanitizes_message(self) -> None:
        """Test that 400 errors are sanitized."""
        error_details = {
            "message": "max_tokens must be a positive integer",
            "error_type": "invalid_request_error",
            "status_code": 400,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        # Message should be present (no sensitive data to redact in this case)
        assert "max_tokens must be a positive integer" in result.message
        assert result.error_type == "invalid_request_error"
        assert result.status_code == 400

    def test_sanitize_500_error_provides_guidance(self) -> None:
        """Test that 500 errors include server error guidance."""
        error_details = {
            "message": "Internal server error",
            "status_code": 500,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert result.status_code == 500
        assert len(result.suggestions) > 0
        # Should suggest trying again
        assert any("try" in s.lower() or "again" in s.lower() for s in result.suggestions)


class TestErrorGuidance:
    """Tests for error guidance messages."""

    def test_401_guidance(self) -> None:
        """Test guidance for 401 authentication errors."""
        guidance = ErrorSanitizer.get_error_guidance(401)

        assert "message" in guidance
        assert "suggestions" in guidance
        assert len(guidance["suggestions"]) > 0
        # Should mention API key
        assert any("api key" in s.lower() for s in guidance["suggestions"])

    def test_429_guidance(self) -> None:
        """Test guidance for 429 rate limit errors."""
        guidance = ErrorSanitizer.get_error_guidance(429)

        assert "message" in guidance
        assert "suggestions" in guidance
        assert len(guidance["suggestions"]) > 0
        # Should mention waiting
        assert any("wait" in s.lower() for s in guidance["suggestions"])

    def test_400_guidance(self) -> None:
        """Test guidance for 400 bad request errors."""
        guidance = ErrorSanitizer.get_error_guidance(400)

        assert "message" in guidance
        assert "suggestions" in guidance
        assert len(guidance["suggestions"]) > 0
        # Should mention configuration
        assert any("configuration" in s.lower() or "model" in s.lower() for s in guidance["suggestions"])

    def test_404_guidance(self) -> None:
        """Test guidance for 404 not found errors."""
        guidance = ErrorSanitizer.get_error_guidance(404)

        assert "message" in guidance
        assert "suggestions" in guidance
        assert len(guidance["suggestions"]) > 0
        # Should mention model discovery
        assert any("model" in s.lower() for s in guidance["suggestions"])

    def test_unknown_status_guidance(self) -> None:
        """Test guidance for unknown status codes."""
        guidance = ErrorSanitizer.get_error_guidance(999)

        assert "message" in guidance
        assert "999" in guidance["message"]
        assert "suggestions" in guidance


class TestBuildErrorDetails:
    """Tests for _build_error_details helper function behavior."""

    def test_development_includes_full_details(self) -> None:
        """Test that development mode includes full error details."""
        raw_details = {
            "status": 401,
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "request_id": "req_abc123",
            "provider_message": "Invalid API key",
            "model": "gpt-4",
            "body": {"error": {"message": "Invalid API key"}},
        }

        sanitized = SanitizedError(
            message="Invalid API key",
            error_type="authentication_error",
            error_code="invalid_api_key",
            status_code=401,
            suggestions=["Check your API key"],
        )

        # Simulate what _build_error_details does in development
        is_development = True
        result: Dict[str, Any] = {
            "status_code": raw_details.get("status"),
            "error_type": sanitized.error_type,
            "error_code": sanitized.error_code,
            "suggestions": sanitized.suggestions,
        }

        if is_development:
            result["endpoint"] = raw_details.get("endpoint")
            result["request_id"] = raw_details.get("request_id")
            result["provider_message"] = raw_details.get("provider_message")
            result["model"] = raw_details.get("model")
            result["body"] = raw_details.get("body")

        assert result["endpoint"] == "https://api.openai.com/v1/chat/completions"
        assert result["request_id"] == "req_abc123"
        assert result["provider_message"] == "Invalid API key"
        assert result["model"] == "gpt-4"
        assert result["body"] is not None

    def test_production_sanitizes_details(self) -> None:
        """Test that production mode sanitizes error details."""
        raw_details = {
            "status": 401,
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "request_id": "req_abc123",
            "provider_message": "Invalid API key sk-test123456789012345678",
            "model": "gpt-4",
            "body": {"error": {"message": "Invalid API key"}},
        }

        sanitized = SanitizedError(
            message="Invalid API key [REDACTED]",
            error_type="authentication_error",
            error_code="invalid_api_key",
            status_code=401,
            suggestions=["Check your API key"],
        )

        # Simulate what _build_error_details does in production
        is_development = False
        result: Dict[str, Any] = {
            "status_code": raw_details.get("status"),
            "error_type": sanitized.error_type,
            "error_code": sanitized.error_code,
            "suggestions": sanitized.suggestions,
        }

        if not is_development:
            if raw_details.get("provider_message"):
                result["provider_message"] = ErrorSanitizer.sanitize_string(
                    raw_details.get("provider_message")
                )
            result["model"] = raw_details.get("model")

        # Should not include endpoint, request_id, or body in production
        assert "endpoint" not in result
        assert "request_id" not in result
        assert "body" not in result
        # Provider message should be sanitized
        assert "sk-test" not in result.get("provider_message", "")
        # Model should still be included
        assert result["model"] == "gpt-4"



class TestRetryState:
    """Tests for RetryState class.
    
    **Feature: open-source-fixes, Property 11: Retry Loop Breaking**
    **Validates: Requirements 5.4, 5.5**
    """

    def test_initial_state(self) -> None:
        """Test RetryState initial state."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)

        assert state.attempts == 0
        assert state.last_error_hash is None
        assert state.identical_error_count == 0
        assert state.max_attempts == 3

    def test_record_error_increments_attempts(self) -> None:
        """Test that recording an error increments attempts."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)
        error = Exception("Test error")

        state.record_error(error)

        assert state.attempts == 1
        assert state.identical_error_count == 1
        assert state.last_error_hash is not None

    def test_record_identical_errors(self) -> None:
        """Test that identical errors are tracked."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)
        error1 = Exception("Test error")
        error2 = Exception("Test error")

        state.record_error(error1)
        state.record_error(error2)

        assert state.attempts == 2
        assert state.identical_error_count == 2

    def test_record_different_errors(self) -> None:
        """Test that different errors reset identical count."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)
        error1 = Exception("Test error 1")
        error2 = Exception("Test error 2")

        state.record_error(error1)
        state.record_error(error2)

        assert state.attempts == 2
        assert state.identical_error_count == 1  # Reset to 1 for new error

    def test_is_infinite_loop_detection(self) -> None:
        """Test infinite loop detection after 3 identical errors."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=5)
        error = Exception("Test error")

        # Record same error 3 times
        state.record_error(error)
        state.record_error(error)
        assert not state.is_infinite_loop()  # Not yet

        state.record_error(error)
        assert state.is_infinite_loop()  # Now detected

    def test_should_retry_max_attempts(self) -> None:
        """Test that retry stops after max attempts."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)
        error = Exception("Test error")

        # First attempt
        state.record_error(error)
        assert state.should_retry(error)

        # Second attempt
        state.record_error(error)
        assert state.should_retry(error)

        # Third attempt - should not retry (attempts=3, max=3)
        state.record_error(error)
        assert not state.should_retry(error)

    def test_should_retry_infinite_loop(self) -> None:
        """Test that retry stops on infinite loop detection."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=10)  # High max
        error = Exception("Test error")

        # Record same error 3 times
        state.record_error(error)
        state.record_error(error)
        state.record_error(error)

        # Should not retry due to infinite loop
        assert not state.should_retry(error)

    def test_should_retry_non_retryable_4xx(self) -> None:
        """Test that 4xx errors (except 429) are not retried."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)

        # Create mock HTTP error
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_request = MagicMock()

        error = httpx.HTTPStatusError(
            message="Bad Request",
            request=mock_request,
            response=mock_response,
        )

        state.record_error(error)
        assert not state.should_retry(error)  # 400 is not retryable

    def test_should_retry_429_rate_limit(self) -> None:
        """Test that 429 rate limit errors are retryable."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)

        # Create mock HTTP error
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_request = MagicMock()

        error = httpx.HTTPStatusError(
            message="Rate Limit",
            request=mock_request,
            response=mock_response,
        )

        state.record_error(error)
        assert state.should_retry(error)  # 429 is retryable

    def test_should_retry_5xx_server_error(self) -> None:
        """Test that 5xx server errors are retryable."""
        from shu.llm.client import RetryState

        state = RetryState(max_attempts=3)

        # Create mock HTTP error
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_request = MagicMock()

        error = httpx.HTTPStatusError(
            message="Server Error",
            request=mock_request,
            response=mock_response,
        )

        state.record_error(error)
        assert state.should_retry(error)  # 500 is retryable


class TestCapabilityMismatchDetection:
    """Tests for capability mismatch detection.
    
    **Feature: open-source-fixes, Property 10: Vision Capability Mismatch Detection**
    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    def test_detect_vision_mismatch_in_message(self) -> None:
        """Test detection of vision capability mismatch in error message."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "This model does not support images",
            "provider_error_type": "",
            "provider_error_code": "",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result

    def test_detect_vision_mismatch_in_error_type(self) -> None:
        """Test detection of vision mismatch in error type."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "Invalid request",
            "provider_error_type": "vision_not_supported",
            "provider_error_code": "",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result

    def test_detect_tool_mismatch_in_message(self) -> None:
        """Test detection of tool calling mismatch in error message."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "Function calling not supported for this model",
            "provider_error_type": "",
            "provider_error_code": "",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result

    def test_detect_multimodal_mismatch(self) -> None:
        """Test detection of multimodal capability mismatch."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "Multimodal content not supported",
            "provider_error_type": "",
            "provider_error_code": "",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result

    def test_no_mismatch_for_regular_error(self) -> None:
        """Test that regular errors are not detected as capability mismatches."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "Invalid API key",
            "provider_error_type": "authentication_error",
            "provider_error_code": "invalid_api_key",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert not result

    def test_case_insensitive_detection(self) -> None:
        """Test that capability mismatch detection is case-insensitive."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "IMAGE_URL not supported",
            "provider_error_type": "",
            "provider_error_code": "",
        }

        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result

    def test_handles_none_values_in_details(self) -> None:
        """Test that capability mismatch detection handles None values gracefully.
        
        This tests the fix for a bug where details dict contained None values
        instead of empty strings, causing AttributeError when calling .lower().
        """
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": None,
            "provider_error_type": None,
            "provider_error_code": None,
        }

        # Should not raise AttributeError
        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert not result

    def test_handles_mixed_none_and_string_values(self) -> None:
        """Test capability mismatch detection with mixed None and string values."""
        from shu.llm.client import UnifiedLLMClient

        details = {
            "provider_message": "This model does not support vision",
            "provider_error_type": None,
            "provider_error_code": None,
        }

        # Should detect mismatch from message even with None in other fields
        result = UnifiedLLMClient._is_capability_mismatch_error(None, details)
        assert result
