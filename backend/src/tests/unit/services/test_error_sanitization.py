"""
Unit tests for ErrorSanitizer service.

Tests error sanitization functionality including API key removal,
URL sanitization, and environment-aware error handling.

**Feature: open-source-fixes, Property 7: Provider Error Field Extraction**
**Feature: open-source-fixes, Property 8: Error Sanitization in Production**
**Validates: Requirements 4.1, 4.2, 4.4**
"""

from unittest.mock import MagicMock

from shu.services.error_sanitization import ErrorSanitizer, SanitizedError


class TestSanitizedError:
    """Tests for SanitizedError dataclass."""

    def test_to_dict_minimal(self) -> None:
        """Test to_dict with only required fields."""
        error = SanitizedError(message="Test error")
        result = error.to_dict()

        assert result == {"message": "Test error"}

    def test_to_dict_full(self) -> None:
        """Test to_dict with all fields populated."""
        error = SanitizedError(
            message="Test error",
            error_type="invalid_request",
            error_code="400",
            status_code=400,
            suggestions=["Check your input", "Try again"],
            details={"endpoint": "/api/test"},
        )
        result = error.to_dict()

        assert result == {
            "message": "Test error",
            "error_type": "invalid_request",
            "error_code": "400",
            "status_code": 400,
            "suggestions": ["Check your input", "Try again"],
            "details": {"endpoint": "/api/test"},
        }

    def test_to_dict_excludes_none_values(self) -> None:
        """Test that to_dict excludes None values."""
        error = SanitizedError(
            message="Test error",
            error_type="test_type",
            error_code=None,
            status_code=500,
        )
        result = error.to_dict()

        assert "error_code" not in result
        assert result["error_type"] == "test_type"


class TestSanitizeString:
    """Tests for ErrorSanitizer.sanitize_string method."""

    def test_sanitize_openai_api_key(self) -> None:
        """Test that OpenAI API keys are redacted."""
        text = "Error with API key sk-1234567890abcdefghijklmnop"
        result = ErrorSanitizer.sanitize_string(text)

        assert "sk-1234567890" not in result
        assert "[REDACTED]" in result

    def test_sanitize_openai_project_api_key(self) -> None:
        """Test that OpenAI project API keys are redacted."""
        text = "Error with API key sk-proj-abc123def456ghi789jkl012mno"
        result = ErrorSanitizer.sanitize_string(text)

        assert "sk-proj-" not in result
        assert "[REDACTED]" in result

    def test_sanitize_anthropic_api_key(self) -> None:
        """Test that Anthropic API keys are redacted."""
        text = "Error with API key sk-ant-abc123def456ghi789jkl012mno"
        result = ErrorSanitizer.sanitize_string(text)

        assert "sk-ant-" not in result
        assert "[REDACTED]" in result

    def test_sanitize_bearer_token(self) -> None:
        """Test that Bearer tokens are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
        result = ErrorSanitizer.sanitize_string(text)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_sanitize_url_to_domain_only(self) -> None:
        """Test that URLs are sanitized to show only domain."""
        text = "Error connecting to https://api.openai.com/v1/chat/completions"
        result = ErrorSanitizer.sanitize_string(text)

        assert "/v1/chat/completions" not in result
        assert "api.openai.com" in result
        assert "..." in result

    def test_sanitize_request_id(self) -> None:
        """Test that request IDs are redacted."""
        text = "request_id: req_abc123def456"
        result = ErrorSanitizer.sanitize_string(text)

        assert "req_abc123def456" not in result
        assert "[request_id_redacted]" in result.lower() or "[REDACTED]" in result

    def test_sanitize_empty_string(self) -> None:
        """Test that empty strings are handled."""
        result = ErrorSanitizer.sanitize_string("")
        assert result == ""

    def test_sanitize_none_returns_none(self) -> None:
        """Test that None input returns empty/falsy."""
        result = ErrorSanitizer.sanitize_string(None)  # type: ignore
        assert not result

    def test_sanitize_preserves_safe_text(self) -> None:
        """Test that safe text is preserved."""
        text = "Model not found: gpt-4"
        result = ErrorSanitizer.sanitize_string(text)

        assert result == "Model not found: gpt-4"


class TestSanitizeDict:
    """Tests for ErrorSanitizer.sanitize_dict method."""

    def test_sanitize_api_key_field(self) -> None:
        """Test that api_key fields are fully redacted."""
        data = {"api_key": "sk-1234567890abcdefghijklmnop", "model": "gpt-4"}
        result = ErrorSanitizer.sanitize_dict(data)

        assert result["api_key"] == "[REDACTED]"
        assert result["model"] == "gpt-4"

    def test_sanitize_authorization_field(self) -> None:
        """Test that authorization fields are fully redacted."""
        data = {"authorization": "Bearer token123", "status": "error"}
        result = ErrorSanitizer.sanitize_dict(data)

        assert result["authorization"] == "[REDACTED]"
        assert result["status"] == "error"

    def test_sanitize_nested_dict(self) -> None:
        """Test that nested dictionaries are sanitized."""
        data = {
            "error": {
                "message": "Invalid API key sk-test123456789012345678",
                "api_key": "sk-secret",
            }
        }
        result = ErrorSanitizer.sanitize_dict(data)

        assert result["error"]["api_key"] == "[REDACTED]"
        assert "sk-test" not in result["error"]["message"]

    def test_sanitize_list_in_dict(self) -> None:
        """Test that lists within dictionaries are sanitized."""
        data = {
            "errors": [
                {"message": "Error with key sk-abc123456789012345678"},
                {"message": "Another error"},
            ]
        }
        result = ErrorSanitizer.sanitize_dict(data)

        assert "sk-abc" not in str(result)
        assert "Another error" in str(result)


class TestExtractProviderError:
    """Tests for ErrorSanitizer.extract_provider_error method."""

    def test_extract_openai_style_error(self) -> None:
        """Test extraction from OpenAI-style error response."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = (
            '{"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}}'
        )
        mock_response.json.return_value = {
            "error": {
                "message": "Invalid API key",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            }
        }

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "Invalid API key"
        assert result["error_type"] == "invalid_request_error"
        assert result["error_code"] == "invalid_api_key"
        assert result["status_code"] == 401

    def test_extract_anthropic_style_error(self) -> None:
        """Test extraction from Anthropic-style error response."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": {"message": "max_tokens is required", "type": "invalid_request_error"}}'
        mock_response.json.return_value = {
            "error": {
                "message": "max_tokens is required",
                "type": "invalid_request_error",
            }
        }

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "max_tokens is required"
        assert result["error_type"] == "invalid_request_error"
        assert result["status_code"] == 400

    def test_extract_top_level_message(self) -> None:
        """Test extraction when message is at top level."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal server error", "type": "server_error"}'
        mock_response.json.return_value = {
            "message": "Internal server error",
            "type": "server_error",
        }

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "Internal server error"
        assert result["error_type"] == "server_error"

    def test_extract_error_array(self) -> None:
        """Test extraction from error array format."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": [{"message": "First error", "type": "validation_error"}]}'
        mock_response.json.return_value = {"error": [{"message": "First error", "type": "validation_error"}]}

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "First error"
        assert result["error_type"] == "validation_error"

    def test_extract_string_error(self) -> None:
        """Test extraction when error is a string."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "Something went wrong"}'
        mock_response.json.return_value = {"error": "Something went wrong"}

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "Something went wrong"

    def test_extract_non_json_response(self) -> None:
        """Test extraction from non-JSON response."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.json.side_effect = ValueError("Not JSON")

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] == "Internal Server Error"
        assert result["status_code"] == 500

    def test_extract_empty_response(self) -> None:
        """Test extraction from empty response."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = ""

        result = ErrorSanitizer.extract_provider_error(mock_response)

        assert result["message"] is None
        assert result["status_code"] == 500


class TestSanitizeError:
    """Tests for ErrorSanitizer.sanitize_error method."""

    def test_sanitize_error_always_sanitizes(self) -> None:
        """Test that errors are always sanitized regardless of environment."""
        error_details = {
            "message": "Invalid API key sk-test123456789012345678",
            "error_type": "authentication_error",
            "status_code": 401,
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "request_id": "req_abc123",
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        # Message should always be sanitized
        assert "sk-test" not in result.message
        assert "[REDACTED]" in result.message
        assert result.error_type == "authentication_error"
        assert result.status_code == 401
        # Details should never be included for security
        assert result.details is None

    def test_sanitize_error_no_details_dict(self) -> None:
        """Test that details dict is never included for security."""
        error_details = {
            "message": "Provider error with details",
            "status_code": 500,
            "endpoint": "https://api.example.com/v1/chat",
            "request_id": "req_abc123",
            "raw_body": '{"error": "Internal error"}',
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        # No details should be included
        assert result.details is None
        assert result.status_code == 500

    def test_sanitize_error_provides_suggestions(self) -> None:
        """Test that appropriate suggestions are provided."""
        error_details = {
            "message": "Authentication failed",
            "status_code": 401,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert len(result.suggestions) > 0
        assert any("API key" in s for s in result.suggestions)

    def test_sanitize_error_rate_limit(self) -> None:
        """Test suggestions for rate limit errors."""
        error_details = {
            "message": "Rate limit exceeded",
            "status_code": 429,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert result.status_code == 429
        assert any("wait" in s.lower() for s in result.suggestions)

    def test_sanitize_error_not_found(self) -> None:
        """Test suggestions for not found errors."""
        error_details = {
            "message": "Model not found",
            "status_code": 404,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert result.status_code == 404
        assert any("model" in s.lower() for s in result.suggestions)

    def test_sanitize_error_uses_default_message_when_none(self) -> None:
        """Test that default message is used when provider message is None."""
        error_details = {
            "message": None,
            "status_code": 401,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        assert "authentication" in result.message.lower() or "API key" in result.message

    def test_sanitize_error_handles_provider_message_key(self) -> None:
        """Test that provider_message key is also handled."""
        error_details = {
            "provider_message": "Custom provider error with key sk-abc123456789012345678",
            "status_code": 500,
        }

        result = ErrorSanitizer.sanitize_error(error_details)

        # Message should be sanitized
        assert "sk-abc123456789012345678" not in result.message
        assert "[REDACTED]" in result.message or "Custom provider error" in result.message


class TestGetErrorGuidance:
    """Tests for ErrorSanitizer.get_error_guidance method."""

    def test_get_guidance_401(self) -> None:
        """Test guidance for 401 errors."""
        guidance = ErrorSanitizer.get_error_guidance(401)

        assert "message" in guidance
        assert "suggestions" in guidance
        assert len(guidance["suggestions"]) > 0

    def test_get_guidance_unknown_status(self) -> None:
        """Test guidance for unknown status codes."""
        guidance = ErrorSanitizer.get_error_guidance(999)

        assert "message" in guidance
        assert "999" in guidance["message"]
        assert "suggestions" in guidance
