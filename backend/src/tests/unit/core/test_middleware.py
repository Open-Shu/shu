"""Unit tests for MustChangePasswordMiddleware.

Tests verify that the must_change_password flag is enforced server-side,
blocking all API requests except the password-change flow endpoints.

Feature: add-password-change-SHU-565
Requirements: 4.2 (server-side enforcement)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient

from shu.core.middleware import MustChangePasswordMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(path: str, method: str = "GET", user: dict | None = None) -> Request:
    """Build a minimal Starlette Request with optional user on state."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "root_path": "",
    }
    request = Request(scope)
    if user is not None:
        request.state.user = user
    return request


async def _ok_handler(request: Request) -> Response:
    """Dummy call_next that always returns 200."""
    return JSONResponse(status_code=200, content={"ok": True})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def middleware() -> MustChangePasswordMiddleware:
    """Create a MustChangePasswordMiddleware instance."""
    # BaseHTTPMiddleware requires an app; we pass a no-op since we call
    # dispatch() directly with our own call_next.
    return MustChangePasswordMiddleware(app=AsyncMock())


@pytest.fixture
def user_must_change() -> dict:
    """User dict with must_change_password=True."""
    return {
        "user_id": "user-123",
        "email": "test@example.com",
        "name": "Test User",
        "role": "regular_user",
        "is_active": True,
        "must_change_password": True,
    }


@pytest.fixture
def user_normal() -> dict:
    """User dict with must_change_password=False."""
    return {
        "user_id": "user-456",
        "email": "normal@example.com",
        "name": "Normal User",
        "role": "regular_user",
        "is_active": True,
        "must_change_password": False,
    }


# ---------------------------------------------------------------------------
# Tests: Requests blocked when must_change_password is True
# ---------------------------------------------------------------------------


class TestMustChangePasswordBlocking:
    """Requests to non-allowed paths are blocked with 403."""

    @pytest.mark.asyncio
    async def test_blocks_generic_api_request(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """A generic API call is blocked when must_change_password is True."""
        request = _make_request("/api/v1/knowledge-bases", user=user_must_change)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_blocks_chat_endpoint(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """Chat endpoint is blocked when must_change_password is True."""
        request = _make_request("/api/v1/chat/conversations", method="POST", user=user_must_change)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_blocks_user_preferences(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """User preferences endpoint is blocked when must_change_password is True."""
        request = _make_request("/api/v1/user-preferences", user=user_must_change)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_403_response_contains_message(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """The 403 response includes a descriptive message."""
        request = _make_request("/api/v1/knowledge-bases", user=user_must_change)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 403
        body = response.body.decode()
        assert "Password change required" in body


# ---------------------------------------------------------------------------
# Tests: Allowed paths pass through when must_change_password is True
# ---------------------------------------------------------------------------


class TestMustChangePasswordAllowedPaths:
    """Allowed endpoints pass through even when must_change_password is True."""

    @pytest.mark.asyncio
    async def test_allows_change_password(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """PUT /auth/change-password is allowed."""
        request = _make_request(
            "/api/v1/auth/change-password", method="PUT", user=user_must_change
        )
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_allows_auth_me(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """GET /auth/me is allowed."""
        request = _make_request("/api/v1/auth/me", method="GET", user=user_must_change)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_allows_auth_refresh(
        self, middleware: MustChangePasswordMiddleware, user_must_change: dict
    ) -> None:
        """POST /auth/refresh is allowed."""
        request = _make_request(
            "/api/v1/auth/refresh", method="POST", user=user_must_change
        )
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Normal users (must_change_password=False) are not affected
# ---------------------------------------------------------------------------


class TestNormalUserPassThrough:
    """Users without must_change_password flag are unaffected."""

    @pytest.mark.asyncio
    async def test_normal_user_passes_through(
        self, middleware: MustChangePasswordMiddleware, user_normal: dict
    ) -> None:
        """A normal user can access any endpoint."""
        request = _make_request("/api/v1/knowledge-bases", user=user_normal)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_user_state_passes_through(
        self, middleware: MustChangePasswordMiddleware
    ) -> None:
        """Requests without user state (unauthenticated/public) pass through."""
        request = _make_request("/api/v1/auth/login/password")
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_user_none_passes_through(
        self, middleware: MustChangePasswordMiddleware
    ) -> None:
        """Requests with user=None pass through."""
        request = _make_request("/api/v1/knowledge-bases", user=None)
        # user=None means state.user is not set (our helper skips setting it)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_user_without_flag_key_passes_through(
        self, middleware: MustChangePasswordMiddleware
    ) -> None:
        """A user dict missing the must_change_password key passes through."""
        user_no_flag = {
            "user_id": "user-789",
            "email": "legacy@example.com",
            "role": "regular_user",
            "is_active": True,
        }
        request = _make_request("/api/v1/knowledge-bases", user=user_no_flag)
        response = await middleware.dispatch(request, _ok_handler)
        assert response.status_code == 200
