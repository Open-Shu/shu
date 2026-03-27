"""Unit tests for MCP admin API router.

Tests call endpoint functions directly with mocked dependencies,
verifying routing, response envelopes, and delegation to McpService.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.mcp_admin import (
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    sync_connection,
    update_connection,
    update_tool_config,
)
from shu.core.exceptions import ConflictError, NotFoundError
from shu.schemas.mcp_admin import (
    McpConnectionCreate,
    McpConnectionUpdate,
    McpSyncResult,
    McpToolConfigUpdate,
    McpToolType,
)


def _mock_user(user_id: str = "user-1"):
    user = MagicMock()
    user.id = user_id
    return user


def _mock_connection(**overrides):
    """Build a mock McpServerConnection with sensible defaults."""
    now = datetime.now(UTC)
    defaults = dict(
        id="conn-1",
        name="test-server",
        url="https://mcp.example.com/sse",
        tool_configs={"search": {"type": "chat_callable", "enabled": True}},
        discovered_tools=[{"name": "search", "description": "Search pages"}],
        timeouts=None,
        response_size_limit_bytes=None,
        enabled=True,
        last_synced_at=now,
        last_connected_at=now,
        last_error=None,
        consecutive_failures=0,
        server_info={"serverInfo": {"name": "test"}},
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    conn = MagicMock()
    for k, v in defaults.items():
        setattr(conn, k, v)
    return conn


class TestCreateConnection:
    """POST /connections — create a new MCP server connection."""

    @pytest.mark.asyncio
    async def test_returns_201_with_connection(self):
        """Successful creation delegates to McpService and returns 201 Created."""
        db = AsyncMock()
        user = _mock_user()
        body = McpConnectionCreate(name="wiki", url="https://mcp.example.com/sse")
        conn = _mock_connection(name="wiki")

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(return_value=conn)

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 201
        svc_cls.return_value.create_connection.assert_awaited_once_with(body, str(user.id))

    @pytest.mark.asyncio
    async def test_conflict_returns_409(self):
        """Duplicate connection name is caught and returned as a 409 error response."""
        db = AsyncMock()
        user = _mock_user()
        body = McpConnectionCreate(name="dup", url="https://mcp.example.com/sse")

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(
                side_effect=ConflictError("already exists")
            )

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 409


class TestListConnections:
    """GET /connections — list all visible connections."""

    @pytest.mark.asyncio
    async def test_returns_list_with_total(self):
        """Multiple connections are returned with a correct total count."""
        db = AsyncMock()
        user = _mock_user()
        conns = [_mock_connection(id="c1", name="a"), _mock_connection(id="c2", name="b")]

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.list_connections = AsyncMock(return_value=conns)

            response = await list_connections(db=db, user=user)

        assert response.status_code == 200
        body = response.body
        assert b'"total":2' in body or b'"total": 2' in body

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """No connections returns 200 with an empty items list."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.list_connections = AsyncMock(return_value=[])

            response = await list_connections(db=db, user=user)

        assert response.status_code == 200


class TestGetConnection:
    """GET /connections/{id} — get a single connection."""

    @pytest.mark.asyncio
    async def test_returns_connection(self):
        """Existing connection is returned as 200 with correct delegation to service."""
        db = AsyncMock()
        user = _mock_user()
        conn = _mock_connection()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.get_connection = AsyncMock(return_value=conn)

            response = await get_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 200
        svc_cls.return_value.get_connection.assert_awaited_once_with("conn-1", str(user.id))

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        """Missing connection ID is caught and returned as a 404 error response."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.get_connection = AsyncMock(
                side_effect=NotFoundError("not found")
            )

            response = await get_connection(connection_id="missing", db=db, user=user)

        assert response.status_code == 404


class TestUpdateConnection:
    """PATCH /connections/{id} — update a connection."""

    @pytest.mark.asyncio
    async def test_returns_updated_connection(self):
        """Successful update delegates to service and returns 200 with the updated connection."""
        db = AsyncMock()
        user = _mock_user()
        body = McpConnectionUpdate(name="renamed")
        conn = _mock_connection(name="renamed")

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.update_connection = AsyncMock(return_value=conn)

            response = await update_connection(
                connection_id="conn-1", body=body, db=db, user=user
            )

        assert response.status_code == 200
        svc_cls.return_value.update_connection.assert_awaited_once_with(
            "conn-1", body, str(user.id)
        )


class TestDeleteConnection:
    """DELETE /connections/{id} — delete a connection."""

    @pytest.mark.asyncio
    async def test_returns_204(self):
        """Successful deletion delegates to service and returns 204 No Content."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.delete_connection = AsyncMock(return_value=None)

            response = await delete_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 204
        svc_cls.return_value.delete_connection.assert_awaited_once_with("conn-1", str(user.id))

    @pytest.mark.asyncio
    async def test_conflict_when_feeds_exist_returns_409(self):
        """Deletion blocked by active feeds is caught and returned as a 409 error response."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.delete_connection = AsyncMock(
                side_effect=ConflictError("feeds exist", details={"feed_ids": ["f1"]})
            )

            response = await delete_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 409


class TestSyncConnection:
    """POST /connections/{id}/sync — trigger tool discovery."""

    @pytest.mark.asyncio
    async def test_returns_sync_result(self):
        """Successful sync delegates to service and returns 200 with tool discovery results."""
        db = AsyncMock()
        user = _mock_user()
        result = McpSyncResult(tools=["search", "fetch"], added=["fetch"], removed=[])

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.sync_connection = AsyncMock(return_value=result)

            response = await sync_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 200
        svc_cls.return_value.sync_connection.assert_awaited_once_with("conn-1", str(user.id))


class TestUpdateToolConfig:
    """PATCH /connections/{id}/tools/{tool_name} — update per-tool config."""

    @pytest.mark.asyncio
    async def test_returns_updated_connection(self):
        """Successful tool config update delegates to service and returns 200 with full connection."""
        db = AsyncMock()
        user = _mock_user()
        body = McpToolConfigUpdate(type=McpToolType.CHAT_CALLABLE, enabled=True)
        conn = _mock_connection()

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.update_tool_config = AsyncMock(return_value=conn)

            response = await update_tool_config(
                connection_id="conn-1",
                tool_name="search",
                body=body,
                db=db,
                user=user,
            )

        assert response.status_code == 200
        svc_cls.return_value.update_tool_config.assert_awaited_once_with(
            "conn-1", "search", body, str(user.id)
        )

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_404(self):
        """Unknown tool name is caught and returned as a 404 error response."""
        db = AsyncMock()
        user = _mock_user()
        body = McpToolConfigUpdate(type=McpToolType.CHAT_CALLABLE, enabled=True)

        with patch("shu.api.mcp_admin.McpService") as svc_cls:
            svc_cls.return_value.update_tool_config = AsyncMock(
                side_effect=NotFoundError("tool not found")
            )

            response = await update_tool_config(
                connection_id="conn-1",
                tool_name="nonexistent",
                body=body,
                db=db,
                user=user,
            )

        assert response.status_code == 404


class TestDeriveStatus:
    """Test the _derive_status helper for all status states."""

    def test_disabled_connection_is_disconnected(self):
        """A disabled connection always reports DISCONNECTED regardless of other fields."""
        from shu.api.mcp_admin import _derive_status
        from shu.schemas.mcp_admin import McpConnectionStatus

        conn = _mock_connection(enabled=False, consecutive_failures=0)
        assert _derive_status(conn) == McpConnectionStatus.DISCONNECTED

    def test_high_failures_is_degraded(self):
        """Consecutive failures at or above the threshold report DEGRADED."""
        from shu.api.mcp_admin import _derive_status
        from shu.schemas.mcp_admin import McpConnectionStatus

        conn = _mock_connection(consecutive_failures=5)
        assert _derive_status(conn) == McpConnectionStatus.DEGRADED

    def test_error_without_prior_success(self):
        """A connection that has errored but never successfully connected reports ERROR."""
        from shu.api.mcp_admin import _derive_status
        from shu.schemas.mcp_admin import McpConnectionStatus

        conn = _mock_connection(last_error="timeout", last_connected_at=None, consecutive_failures=1)
        assert _derive_status(conn) == McpConnectionStatus.ERROR

    def test_connected_with_last_success(self):
        """A connection with a recorded last_connected_at and no degradation reports CONNECTED."""
        from shu.api.mcp_admin import _derive_status
        from shu.schemas.mcp_admin import McpConnectionStatus

        conn = _mock_connection(last_connected_at=datetime.now(UTC), consecutive_failures=0)
        assert _derive_status(conn) == McpConnectionStatus.CONNECTED

    def test_fresh_connection_is_disconnected(self):
        """A newly created connection with no history reports DISCONNECTED."""
        from shu.api.mcp_admin import _derive_status
        from shu.schemas.mcp_admin import McpConnectionStatus

        conn = _mock_connection(
            last_connected_at=None, last_error=None, consecutive_failures=0
        )
        assert _derive_status(conn) == McpConnectionStatus.DISCONNECTED
