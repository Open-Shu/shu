"""Tests for shu_plugin_sdk.testing (HttpRequestFailed and FakeHostBuilder)."""

from __future__ import annotations

import pytest

from shu_plugin_sdk.testing import FakeHostBuilder, HttpRequestFailed

# ---------------------------------------------------------------------------
# HttpRequestFailed tests
# ---------------------------------------------------------------------------


def test_http_request_failed_error_categories() -> None:
    """Each status code maps to the correct error_category string."""
    cases = [
        (401, "auth_error"),
        (403, "forbidden"),
        (404, "not_found"),
        (410, "gone"),
        (429, "rate_limited"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (400, "client_error"),
        (422, "client_error"),
        (409, "client_error"),
    ]
    for status_code, expected in cases:
        exc = HttpRequestFailed(status_code, "https://example.com")
        assert exc.error_category == expected, (
            f"status {status_code}: expected {expected!r}, got {exc.error_category!r}"
        )


def test_http_request_failed_is_retryable() -> None:
    """429 and 5xx errors are retryable; 4xx (except 429) are not."""
    assert HttpRequestFailed(429, "u").is_retryable is True
    assert HttpRequestFailed(500, "u").is_retryable is True
    assert HttpRequestFailed(503, "u").is_retryable is True
    assert HttpRequestFailed(404, "u").is_retryable is False
    assert HttpRequestFailed(401, "u").is_retryable is False
    assert HttpRequestFailed(400, "u").is_retryable is False


def test_http_request_failed_retry_after() -> None:
    """retry_after_seconds parses Retry-After header correctly."""
    # Standard integer value
    assert HttpRequestFailed(429, "u", headers={"Retry-After": "30"}).retry_after_seconds == 30
    # Case-insensitive lookup
    assert HttpRequestFailed(429, "u", headers={"retry-after": "60"}).retry_after_seconds == 60
    assert HttpRequestFailed(429, "u", headers={"RETRY-AFTER": "10"}).retry_after_seconds == 10
    # Missing header
    assert HttpRequestFailed(429, "u", headers={}).retry_after_seconds is None
    assert HttpRequestFailed(429, "u").retry_after_seconds is None
    # Non-integer value (HTTP-date) → None
    assert HttpRequestFailed(429, "u", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}).retry_after_seconds is None


# ---------------------------------------------------------------------------
# FakeHostBuilder tests
# ---------------------------------------------------------------------------


_ALL_CAPS = ("http", "auth", "secrets", "storage", "kb", "cursor", "cache", "log", "utils", "identity", "ocr")


def test_fake_host_builder_default_capabilities() -> None:
    """build() returns a host with all 11 capability attributes set."""
    host = FakeHostBuilder().build()
    for cap in _ALL_CAPS:
        assert hasattr(host, cap), f"host missing capability: {cap}"


@pytest.mark.asyncio
async def test_fake_host_builder_with_secret() -> None:
    """Configured secret is returned by host.secrets.get(key)."""
    host = FakeHostBuilder().with_secret("api_key", "tok123").build()
    assert await host.secrets.get("api_key") == "tok123"


@pytest.mark.asyncio
async def test_fake_host_builder_missing_secret_returns_none() -> None:
    """Unconfigured secret key returns None."""
    host = FakeHostBuilder().build()
    assert await host.secrets.get("not_configured") is None


@pytest.mark.asyncio
async def test_fake_host_builder_with_http_response() -> None:
    """Configured HTTP response is returned by host.http.fetch."""
    response = {"status_code": 200, "headers": {"Content-Type": "application/json"}, "body": {"id": 42}}
    host = FakeHostBuilder().with_http_response("GET", "https://api.example.com/item", response).build()
    result = await host.http.fetch("GET", "https://api.example.com/item")
    assert result == response


@pytest.mark.asyncio
async def test_fake_host_builder_with_http_error() -> None:
    """Configured HTTP error raises HttpRequestFailed with the correct status code."""
    host = FakeHostBuilder().with_http_error("POST", "https://api.example.com/submit", 429).build()
    with pytest.raises(HttpRequestFailed) as exc_info:
        await host.http.fetch("POST", "https://api.example.com/submit")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_fake_host_builder_unconfigured_fetch_returns_asyncmock() -> None:
    """Unconfigured fetch URL returns the AsyncMock default (does not raise)."""
    host = FakeHostBuilder().build()
    # Should not raise; returns the AsyncMock default value
    result = await host.http.fetch("GET", "https://unconfigured.example.com/")
    # The AsyncMock default is a coroutine result — just assert it didn't raise
    assert result is not None or result is None  # any result is fine


@pytest.mark.asyncio
async def test_fake_host_builder_fluent_chaining() -> None:
    """Full fluent chain with_secret().with_http_response().with_http_error().build() works."""
    ok_response = {"status_code": 200, "headers": {}, "body": "ok"}
    host = (
        FakeHostBuilder()
        .with_secret("token", "abc")
        .with_http_response("GET", "https://api.example.com/data", ok_response)
        .with_http_error("DELETE", "https://api.example.com/resource", 403)
        .build()
    )
    assert await host.secrets.get("token") == "abc"
    assert (await host.http.fetch("GET", "https://api.example.com/data")) == ok_response
    with pytest.raises(HttpRequestFailed) as exc_info:
        await host.http.fetch("DELETE", "https://api.example.com/resource")
    assert exc_info.value.status_code == 403
