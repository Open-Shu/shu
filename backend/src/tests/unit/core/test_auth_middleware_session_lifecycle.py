"""Session-lifecycle tests for AuthenticationMiddleware.

These tests assert that the middleware closes its database session by the time
``dispatch()`` returns. They were written to verify the fix for a pool-leak
class of bug:

    async for db in get_db():       # WRONG — suspends the generator
        ...
        break                       #         session.close() deferred to GC

vs.

    async with get_async_session_local()() as db:   # CORRECT — close on exit
        ...

When the generator is left suspended by ``break``, ``session.close()`` does
not run synchronously — it is deferred to asyncio's asyncgen finalizer hook
and, under load or cancellation pressure, the ``_ConnectionFairy`` may be
GC'd before the finalizer runs, producing the
``non-checked-in connection found in GC`` warnings observed in production.
This test asserts the close happens synchronously, which is true if and only
if the middleware uses the ``async with`` pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shu.auth.jwt_manager import JWTManager
from shu.core.middleware import AuthenticationMiddleware


def _make_request(path: str, method: str, auth_header: str | None) -> Request:
    """Build a minimal Starlette Request, optionally with an Authorization header."""
    headers: list[tuple[bytes, bytes]] = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }
    return Request(scope)


async def _ok_handler(request: Request) -> Response:
    return JSONResponse(status_code=200, content={"ok": True})


@pytest.fixture
def session_tracker(monkeypatch):
    """Patch ``get_async_session_local`` to return a factory that tracks every
    session's create/close events.

    Both code paths route through this factory:

    - Old code: ``get_db()`` calls ``get_async_session_local()`` then
      ``async with session_local() as session:``.
    - New code: ``async with get_async_session_local()() as db:``.

    Either path will produce a TrackingSession; the difference is *when*
    close() is invoked relative to the middleware's dispatch returning.
    """
    stats = {"created": 0, "closed": 0, "open": []}

    stub_user = SimpleNamespace(
        id="test-user-id",
        email="test@example.com",
        name="Test User",
        role="admin",
        is_active=True,
        must_change_password=False,
        password_changed_at=None,
        # last_login set to today so _update_daily_login is a no-op (no commit needed)
        last_login=datetime.now(UTC),
    )

    class TrackingSession:
        def __init__(self) -> None:
            stats["created"] += 1
            stats["open"].append(self)
            self._closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            await self.close()
            return False

        async def execute(self, stmt, *args, **kwargs):
            result = MagicMock()
            result.scalar_one_or_none = lambda: stub_user
            return result

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

        async def close(self) -> None:
            if not self._closed:
                stats["closed"] += 1
                stats["open"].remove(self)
                self._closed = True

    def factory():
        return TrackingSession()

    # Patch where the middleware (and get_db) looks it up. Both the old and
    # the fixed middleware import `get_async_session_local` lazily inside
    # dispatch(), so a single patch covers both code paths.
    monkeypatch.setattr("shu.core.database.get_async_session_local", lambda: factory)

    return stats


@pytest.fixture
def valid_bearer_token() -> str:
    """A real, valid JWT signed with the test JWT_SECRET_KEY from conftest."""
    jwt_mgr = JWTManager()
    return jwt_mgr.create_access_token(
        {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "role": "admin",
        }
    )


@pytest.mark.asyncio
async def test_auth_middleware_closes_session_synchronously(session_tracker, valid_bearer_token):
    """The DB session must be closed by the time dispatch() returns.

    This is the regression guard for the ``async for db in get_db(): break``
    anti-pattern. With that pattern, the generator is suspended at break time
    and ``session.close()`` is deferred to GC. With the ``async with`` fix,
    close is synchronous.
    """
    middleware = AuthenticationMiddleware(app=AsyncMock())
    request = _make_request("/api/v1/some-protected", "GET", f"Bearer {valid_bearer_token}")

    response = await middleware.dispatch(request, _ok_handler)

    assert response.status_code == 200
    assert session_tracker["created"] >= 1, "middleware should have opened at least one session"
    assert session_tracker["closed"] == session_tracker["created"], (
        f"orphan session(s) detected after dispatch returned: "
        f"created={session_tracker['created']}, closed={session_tracker['closed']}, "
        f"open={len(session_tracker['open'])}. The middleware did not close its DB "
        f"session synchronously — this is the pre-fix async-for/break pattern."
    )


@pytest.mark.asyncio
async def test_auth_middleware_closes_session_on_early_return(session_tracker, monkeypatch):
    """Early returns (e.g. inactive user → 400) must also close the session.

    Equally important as the happy path: with ``async for db in get_db(): return ...``,
    the early return suspends the generator just like ``break`` does. The
    ``async with`` pattern handles early returns correctly via ``__aexit__``.
    """
    # Make the stub user inactive — middleware returns 400 mid-block.
    inactive_user = SimpleNamespace(
        id="test-user-id",
        email="test@example.com",
        name="Test User",
        role="admin",
        is_active=False,  # <-- forces early return
        must_change_password=False,
        password_changed_at=None,
        last_login=datetime.now(UTC),
    )

    def factory():
        # Reuse the TrackingSession class via a fresh factory that returns
        # the inactive user from execute().
        stats = session_tracker

        class InactiveTrackingSession:
            def __init__(self) -> None:
                stats["created"] += 1
                stats["open"].append(self)
                self._closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                await self.close()
                return False

            async def execute(self, stmt, *args, **kwargs):
                result = MagicMock()
                result.scalar_one_or_none = lambda: inactive_user
                return result

            async def commit(self) -> None:
                pass

            async def rollback(self) -> None:
                pass

            async def close(self) -> None:
                if not self._closed:
                    stats["closed"] += 1
                    stats["open"].remove(self)
                    self._closed = True

        return InactiveTrackingSession()

    monkeypatch.setattr("shu.core.database.get_async_session_local", lambda: factory)

    jwt_mgr = JWTManager()
    token = jwt_mgr.create_access_token(
        {"user_id": "test-user-id", "email": "test@example.com", "role": "admin"}
    )

    middleware = AuthenticationMiddleware(app=AsyncMock())
    request = _make_request("/api/v1/some-protected", "GET", f"Bearer {token}")

    response = await middleware.dispatch(request, _ok_handler)

    assert response.status_code == 400, "inactive user should produce 400"
    assert session_tracker["closed"] == session_tracker["created"], (
        f"orphan session on early-return path: created={session_tracker['created']}, "
        f"closed={session_tracker['closed']}"
    )
