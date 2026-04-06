"""Unit tests for API integration admin router.

Tests call endpoint functions directly with mocked dependencies,
verifying routing, response envelopes, and delegation to ApiIntegrationService.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.api.api_integration_admin import (
    _derive_status,
    _to_response,
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    sync_connection,
    update_connection,
    update_tool_config,
)
from shu.core.exceptions import ConflictError, NotFoundError
from shu.schemas.api_integration_admin import (
    ApiConnectionCreate,
    ApiConnectionStatus,
    ApiConnectionUpdate,
    ApiSyncResult,
)
from shu.schemas.integration_common import ToolConfigUpdate


def _mock_user(user_id: str = "user-1"):
    user = MagicMock()
    user.id = user_id
    return user


def _mock_connection(**overrides):
    """Build a mock ApiServerConnection with sensible defaults."""
    now = datetime.now(UTC)
    defaults = dict(
        id="conn-1",
        name="test-api",
        url="https://api.example.com/openapi.json",
        spec_type="openapi",
        base_url="https://api.example.com",
        tool_configs={"listUsers": {"type": "chat_callable", "enabled": True}},
        discovered_tools=[{"name": "listUsers", "description": "List users"}],
        timeouts=None,
        response_size_limit_bytes=None,
        enabled=True,
        last_synced_at=now,
        last_error=None,
        consecutive_failures=0,
        auth_config=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    conn = MagicMock()
    for k, v in defaults.items():
        setattr(conn, k, v)
    return conn


class TestCreateConnection:
    """POST /connections -- create a new API integration connection."""

    @pytest.mark.asyncio
    async def test_returns_201_with_connection(self):
        """Successful creation delegates to service and returns 201 Created."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionCreate(yaml_content="api_integration_version: 1\nname: my-api\nopenapi_definition: https://api.example.com/openapi.json")
        conn = _mock_connection(name="my-api")

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(return_value=conn)

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 201
        svc_cls.return_value.create_connection.assert_awaited_once_with(
            body.yaml_content, body.auth_credential, str(user.id)
        )

    @pytest.mark.asyncio
    async def test_returns_201_with_auth_credential(self):
        """Creation with auth_credential passes it through to service."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionCreate(
            yaml_content="api_integration_version: 1\nname: my-api\nopenapi_definition: https://api.example.com/openapi.json",
            auth_credential="sk-secret-key",
        )
        conn = _mock_connection(name="my-api", auth_config={"type": "header", "name": "Authorization"})

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(return_value=conn)

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 201
        svc_cls.return_value.create_connection.assert_awaited_once_with(
            body.yaml_content, "sk-secret-key", str(user.id)
        )

    @pytest.mark.asyncio
    async def test_conflict_returns_409(self):
        """Duplicate connection name is caught and returned as a 409 error response."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionCreate(yaml_content="api_integration_version: 1\nname: dup\nopenapi_definition: https://api.example.com/openapi.json")

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(
                side_effect=ConflictError("already exists")
            )

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self):
        """Generic exception is caught and returned as a 500 error response."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionCreate(yaml_content="api_integration_version: 1\nname: boom\nopenapi_definition: https://api.example.com/openapi.json")

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.create_connection = AsyncMock(
                side_effect=RuntimeError("kaboom")
            )

            response = await create_connection(body=body, db=db, user=user)

        assert response.status_code == 500


class TestListConnections:
    """GET /connections -- list all API integration connections."""

    @pytest.mark.asyncio
    async def test_returns_list_with_total(self):
        """Multiple connections are returned with a correct total count."""
        db = AsyncMock()
        user = _mock_user()
        conns = [_mock_connection(id="c1", name="a"), _mock_connection(id="c2", name="b")]

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
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

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.list_connections = AsyncMock(return_value=[])

            response = await list_connections(db=db, user=user)

        assert response.status_code == 200
        body = response.body
        assert b'"total":0' in body or b'"total": 0' in body

    @pytest.mark.asyncio
    async def test_shu_exception_returns_error(self):
        """ShuException from service is caught and returned with correct status code."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.list_connections = AsyncMock(
                side_effect=NotFoundError("no connections")
            )

            response = await list_connections(db=db, user=user)

        assert response.status_code == 404


class TestGetConnection:
    """GET /connections/{id} -- get a single connection."""

    @pytest.mark.asyncio
    async def test_returns_connection(self):
        """Existing connection is returned as 200 with correct delegation to service."""
        db = AsyncMock()
        user = _mock_user()
        conn = _mock_connection()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.get_connection = AsyncMock(return_value=conn)

            response = await get_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 200
        svc_cls.return_value.get_connection.assert_awaited_once_with("conn-1", str(user.id))

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        """Missing connection ID is caught and returned as a 404 error response."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.get_connection = AsyncMock(
                side_effect=NotFoundError("not found")
            )

            response = await get_connection(connection_id="missing", db=db, user=user)

        assert response.status_code == 404


class TestUpdateConnection:
    """PATCH /connections/{id} -- update a connection."""

    @pytest.mark.asyncio
    async def test_returns_updated_connection(self):
        """Successful update delegates to service and returns 200 with the updated connection."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionUpdate(enabled=False)
        conn = _mock_connection(enabled=False)

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.update_connection = AsyncMock(return_value=conn)

            response = await update_connection(
                connection_id="conn-1", body=body, db=db, user=user
            )

        assert response.status_code == 200
        svc_cls.return_value.update_connection.assert_awaited_once_with(
            "conn-1", body, str(user.id)
        )

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        """Updating a nonexistent connection returns 404."""
        db = AsyncMock()
        user = _mock_user()
        body = ApiConnectionUpdate(enabled=True)

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.update_connection = AsyncMock(
                side_effect=NotFoundError("not found")
            )

            response = await update_connection(
                connection_id="missing", body=body, db=db, user=user
            )

        assert response.status_code == 404


class TestDeleteConnection:
    """DELETE /connections/{id} -- delete a connection."""

    @pytest.mark.asyncio
    async def test_returns_204(self):
        """Successful deletion delegates to service and returns 204 No Content."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.delete_connection = AsyncMock(return_value=None)

            response = await delete_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 204
        svc_cls.return_value.delete_connection.assert_awaited_once_with("conn-1", str(user.id))

    @pytest.mark.asyncio
    async def test_conflict_when_feeds_exist_returns_409(self):
        """Deletion blocked by active feeds is caught and returned as a 409 error response."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.delete_connection = AsyncMock(
                side_effect=ConflictError("feeds exist", details={"feed_ids": ["f1"]})
            )

            response = await delete_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self):
        """Generic exception during deletion returns 500."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.delete_connection = AsyncMock(
                side_effect=RuntimeError("db crashed")
            )

            response = await delete_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 500


class TestSyncConnection:
    """POST /connections/{id}/sync -- trigger OpenAPI spec sync."""

    @pytest.mark.asyncio
    async def test_returns_sync_result(self):
        """Successful sync delegates to service and returns 200 with sync results."""
        db = AsyncMock()
        user = _mock_user()
        result = ApiSyncResult(tools=["listUsers", "getUser"], added=["getUser"], stale=[], errors=[])

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.sync_connection = AsyncMock(return_value=result)

            response = await sync_connection(connection_id="conn-1", db=db, user=user)

        assert response.status_code == 200
        svc_cls.return_value.sync_connection.assert_awaited_once_with("conn-1", str(user.id))

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        """Syncing a nonexistent connection returns 404."""
        db = AsyncMock()
        user = _mock_user()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.sync_connection = AsyncMock(
                side_effect=NotFoundError("not found")
            )

            response = await sync_connection(connection_id="missing", db=db, user=user)

        assert response.status_code == 404


class TestUpdateToolConfig:
    """PATCH /connections/{id}/tools/{tool_name} -- update per-tool config."""

    @pytest.mark.asyncio
    async def test_returns_updated_connection(self):
        """Successful tool config update delegates to service and returns 200 with full connection."""
        db = AsyncMock()
        user = _mock_user()
        body = ToolConfigUpdate(chat_callable=True, enabled=True)
        conn = _mock_connection()

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
            svc_cls.return_value.update_tool_config = AsyncMock(return_value=conn)

            response = await update_tool_config(
                connection_id="conn-1",
                tool_name="listUsers",
                body=body,
                db=db,
                user=user,
            )

        assert response.status_code == 200
        svc_cls.return_value.update_tool_config.assert_awaited_once_with(
            "conn-1", "listUsers", body, str(user.id)
        )

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_404(self):
        """Unknown tool name is caught and returned as a 404 error response."""
        db = AsyncMock()
        user = _mock_user()
        body = ToolConfigUpdate(chat_callable=True, enabled=True)

        with patch("shu.api.api_integration_admin.ApiIntegrationService") as svc_cls:
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
        conn = _mock_connection(enabled=False, consecutive_failures=0)
        assert _derive_status(conn) == ApiConnectionStatus.DISCONNECTED

    def test_high_failures_is_degraded(self):
        """Consecutive failures at or above the threshold report DEGRADED."""
        conn = _mock_connection(consecutive_failures=5)
        assert _derive_status(conn) == ApiConnectionStatus.DEGRADED

    def test_error_without_prior_sync(self):
        """A connection that has errored but never synced reports ERROR."""
        conn = _mock_connection(last_error="timeout", last_synced_at=None, consecutive_failures=1)
        assert _derive_status(conn) == ApiConnectionStatus.ERROR

    def test_connected_with_last_sync(self):
        """A connection with a recorded last_synced_at and no degradation reports CONNECTED."""
        conn = _mock_connection(last_synced_at=datetime.now(UTC), consecutive_failures=0)
        assert _derive_status(conn) == ApiConnectionStatus.CONNECTED

    def test_fresh_connection_is_disconnected(self):
        """A newly created connection with no history reports DISCONNECTED."""
        conn = _mock_connection(
            last_synced_at=None, last_error=None, consecutive_failures=0
        )
        assert _derive_status(conn) == ApiConnectionStatus.DISCONNECTED


class TestToResponse:
    """Test the _to_response helper conversion."""

    def test_has_auth_true_when_auth_config_present(self):
        """Connection with auth_config produces has_auth=True in response."""
        conn = _mock_connection(auth_config={"type": "header", "name": "Authorization"})
        resp = _to_response(conn)
        assert resp.has_auth is True

    def test_has_auth_false_when_no_auth_config(self):
        """Connection without auth_config produces has_auth=False in response."""
        conn = _mock_connection(auth_config=None)
        resp = _to_response(conn)
        assert resp.has_auth is False

    def test_tool_count_matches_discovered_tools(self):
        """Tool count is derived from the length of discovered_tools."""
        tools = [{"name": "a", "description": "A"}, {"name": "b", "description": "B"}]
        conn = _mock_connection(discovered_tools=tools)
        resp = _to_response(conn)
        assert resp.tool_count == 2

    def test_empty_discovered_tools(self):
        """None discovered_tools defaults to empty list with zero tool count."""
        conn = _mock_connection(discovered_tools=None)
        resp = _to_response(conn)
        assert resp.tool_count == 0
        assert resp.discovered_tools == []
