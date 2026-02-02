"""Tests for HttpRequestFailed exception semantic properties."""

import pytest

# Import directly from exceptions module to avoid circular import via host __init__
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "exceptions",
    os.path.join(os.path.dirname(__file__), "..", "shu", "plugins", "host", "exceptions.py")
)
_exceptions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_exceptions)
HttpRequestFailed = _exceptions.HttpRequestFailed


class TestErrorCategory:
    def test_401_is_auth_error(self):
        e = HttpRequestFailed(401, "https://api.example.com", {})
        assert e.error_category == "auth_error"

    def test_403_is_forbidden(self):
        e = HttpRequestFailed(403, "https://api.example.com", {})
        assert e.error_category == "forbidden"

    def test_404_is_not_found(self):
        e = HttpRequestFailed(404, "https://api.example.com", {})
        assert e.error_category == "not_found"

    def test_410_is_gone(self):
        e = HttpRequestFailed(410, "https://api.example.com", {})
        assert e.error_category == "gone"

    def test_429_is_rate_limited(self):
        e = HttpRequestFailed(429, "https://api.example.com", {})
        assert e.error_category == "rate_limited"

    def test_500_is_server_error(self):
        e = HttpRequestFailed(500, "https://api.example.com", {})
        assert e.error_category == "server_error"

    def test_503_is_server_error(self):
        e = HttpRequestFailed(503, "https://api.example.com", {})
        assert e.error_category == "server_error"

    def test_400_is_client_error(self):
        e = HttpRequestFailed(400, "https://api.example.com", {})
        assert e.error_category == "client_error"


class TestIsRetryable:
    def test_401_is_not_retryable(self):
        e = HttpRequestFailed(401, "https://api.example.com", {})
        assert e.is_retryable is False

    def test_429_is_retryable(self):
        e = HttpRequestFailed(429, "https://api.example.com", {})
        assert e.is_retryable is True

    def test_500_is_retryable(self):
        e = HttpRequestFailed(500, "https://api.example.com", {})
        assert e.is_retryable is True

    def test_503_is_retryable(self):
        e = HttpRequestFailed(503, "https://api.example.com", {})
        assert e.is_retryable is True


class TestRetryAfterSeconds:
    def test_parses_integer_header(self):
        e = HttpRequestFailed(429, "https://api.example.com", {}, {"Retry-After": "30"})
        assert e.retry_after_seconds == 30

    def test_parses_lowercase_header(self):
        e = HttpRequestFailed(429, "https://api.example.com", {}, {"retry-after": "60"})
        assert e.retry_after_seconds == 60

    def test_returns_none_without_header(self):
        e = HttpRequestFailed(429, "https://api.example.com", {}, {})
        assert e.retry_after_seconds is None

    def test_returns_none_for_invalid_value(self):
        e = HttpRequestFailed(429, "https://api.example.com", {}, {"Retry-After": "invalid"})
        assert e.retry_after_seconds is None


class TestProviderMessage:
    def test_extracts_microsoft_graph_error(self):
        body = {"error": {"code": "BadRequest", "message": "The filter is invalid"}}
        e = HttpRequestFailed(400, "https://graph.microsoft.com", body)
        assert e.provider_message == "The filter is invalid"

    def test_extracts_oauth_error_description(self):
        body = {"error_description": "Token expired"}
        e = HttpRequestFailed(401, "https://auth.example.com", body)
        assert e.provider_message == "Token expired"

    def test_extracts_simple_message(self):
        body = {"message": "Internal server error"}
        e = HttpRequestFailed(500, "https://api.example.com", body)
        assert e.provider_message == "Internal server error"

    def test_handles_string_body(self):
        e = HttpRequestFailed(500, "https://api.example.com", "Something went wrong")
        assert e.provider_message == "Something went wrong"

    def test_handles_none_body(self):
        e = HttpRequestFailed(500, "https://api.example.com", None)
        assert e.provider_message == ""


class TestProviderErrorCode:
    def test_extracts_microsoft_graph_code(self):
        body = {"error": {"code": "BadRequest", "message": "The filter is invalid"}}
        e = HttpRequestFailed(400, "https://graph.microsoft.com", body)
        assert e.provider_error_code == "BadRequest"

    def test_extracts_google_status(self):
        body = {"error": {"status": "PERMISSION_DENIED", "message": "Access denied"}}
        e = HttpRequestFailed(403, "https://googleapis.com", body)
        assert e.provider_error_code == "PERMISSION_DENIED"

    def test_extracts_top_level_code(self):
        body = {"code": "RATE_LIMIT_EXCEEDED"}
        e = HttpRequestFailed(429, "https://api.example.com", body)
        assert e.provider_error_code == "RATE_LIMIT_EXCEEDED"

    def test_returns_none_without_code(self):
        body = {"message": "Error"}
        e = HttpRequestFailed(500, "https://api.example.com", body)
        assert e.provider_error_code is None

    def test_returns_none_for_non_dict_body(self):
        e = HttpRequestFailed(500, "https://api.example.com", "Error string")
        assert e.provider_error_code is None

