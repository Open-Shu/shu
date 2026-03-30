"""Unit tests for McpService.

Covers CRUD operations (create with encryption, update, delete with
feed-guard), sync (tool merging, health tracking), PluginRecord
generation, and PBAC-filtered listing.

All tests mock the AsyncSession, PBAC enforcement, and encryption
service to isolate the service logic from DB and crypto dependencies.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ConflictError, NotFoundError
from shu.models.mcp_server_connection import McpServerConnection
from shu.plugins.mcp_client import McpError, McpToolInfo
from shu.schemas.mcp_admin import (
    McpConnectionCreate,
    McpConnectionUpdate,
    McpIngestConfig,
    McpIngestFieldMapping,
    McpTimeoutsConfig,
    McpToolConfigUpdate,
)
from shu.services.mcp_service import McpService


def _make_connection(**overrides) -> MagicMock:
    """Build a mock McpServerConnection with sensible defaults."""
    defaults = {
        "id": "conn-1",
        "name": "test-server",
        "url": "https://example.com/mcp",
        "tool_configs": None,
        "discovered_tools": None,
        "timeouts": None,
        "response_size_limit_bytes": None,
        "enabled": True,
        "last_synced_at": None,
        "last_connected_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "server_info": None,
    }
    defaults.update(overrides)
    conn = MagicMock(spec=McpServerConnection)
    for k, v in defaults.items():
        setattr(conn, k, v)
    return conn


def _mock_db() -> AsyncMock:
    """Create a mock AsyncSession with common async methods."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()
    return db


def _patch_pbac():
    """Patch enforce_pbac and POLICY_CACHE to permit everything."""
    return (
        patch("shu.services.mcp_service.enforce_pbac", new_callable=AsyncMock),
        patch(
            "shu.services.mcp_service.POLICY_CACHE",
            **{"get_denied_resources": AsyncMock(return_value=set())},
        ),
    )


def _patch_secrets():
    """Patch plugin_secrets functions used by McpService for header storage."""
    return (
        patch("shu.services.mcp_service.set_secret", new_callable=AsyncMock),
        patch("shu.services.mcp_service.get_secret", new_callable=AsyncMock),
        patch("shu.services.mcp_service.list_secret_keys", new_callable=AsyncMock, return_value=[]),
        patch("shu.services.mcp_service.delete_secret", new_callable=AsyncMock),
    )


def _scalar_one_or_none(value):
    """Build an execute result whose .scalar_one_or_none() returns value."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestCreateConnection:
    """Verify header storage via plugin secrets on create."""

    @pytest.mark.asyncio
    async def test_headers_stored_as_secrets(self):
        """Auth headers are stored via set_secret with header: prefix and system scope."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, list_patch, del_patch:
            service = McpService(db)
            data = McpConnectionCreate(
                name="my-server",
                url="https://remote.example.com/mcp",
                headers={"Authorization": "Bearer secret123"},
            )
            await service.create_connection(data, user_id="admin")

        mock_set.assert_awaited_once_with(
            "mcp:my-server", "header:Authorization", value="Bearer secret123", user_id="admin", scope="system"
        )

    @pytest.mark.asyncio
    async def test_create_without_headers_skips_secrets(self):
        """No headers means no calls to set_secret."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, list_patch, del_patch:
            service = McpService(db)
            data = McpConnectionCreate(
                name="no-auth",
                url="https://remote.example.com/mcp",
            )
            await service.create_connection(data, user_id="admin")

        mock_set.assert_not_awaited()


class TestUpdateConnection:
    """Verify field updates, partial updates, and header re-storage."""

    @pytest.mark.asyncio
    async def test_fields_updated_correctly(self):
        """All provided fields are applied; headers are stored via secrets."""
        conn = _make_connection()
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, list_patch, del_patch:
            service = McpService(db)
            data = McpConnectionUpdate(
                url="https://new.example.com/mcp",
                enabled=False,
                response_size_limit_bytes=2048,
                headers={"X-Key": "val"},
                timeouts=McpTimeoutsConfig(connect_ms=2000, call_ms=5000, read_ms=5000),
            )
            result = await service.update_connection("conn-1", data, user_id="admin")

        assert result.url == "https://new.example.com/mcp"
        assert result.enabled is False
        assert result.response_size_limit_bytes == 2048
        assert result.timeouts == {"connect_ms": 2000, "call_ms": 5000, "read_ms": 5000}
        mock_set.assert_awaited_once_with(
            "mcp:test-server", "header:X-Key", value="val", user_id="admin", scope="system"
        )
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_partial_update_preserves_unchanged_fields(self):
        """Fields not in the update payload remain untouched; no secret calls."""
        conn = _make_connection(url="https://original.example.com/mcp", enabled=True)
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch as mock_set, get_patch, list_patch, del_patch:
            service = McpService(db)
            data = McpConnectionUpdate(enabled=False)
            result = await service.update_connection("conn-1", data, user_id="admin")

        assert result.url == "https://original.example.com/mcp"
        assert result.enabled is False
        mock_set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_name_field_not_accepted_on_update(self):
        """Connection names are immutable — McpConnectionUpdate has no name field."""
        schema = McpConnectionUpdate(url="https://example.com/mcp")
        assert not hasattr(schema, "name")


class TestDeleteConnection:
    """Verify deletion is blocked by active feeds (409) and succeeds otherwise."""

    @pytest.mark.asyncio
    async def test_blocked_when_feeds_exist(self):
        """Active feeds referencing the plugin name prevent deletion with a 409."""
        conn = _make_connection(name="my-mcp")
        db = _mock_db()

        get_conn_result = _scalar_one_or_none(conn)
        feed_result = MagicMock()
        feed_result.all.return_value = [("feed-1",), ("feed-2",)]
        db.execute = AsyncMock(side_effect=[get_conn_result, feed_result])

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, list_patch, del_patch:
            service = McpService(db)
            with pytest.raises(ConflictError) as exc_info:
                await service.delete_connection("conn-1", user_id="admin")

        assert "feed-1" in str(exc_info.value.details)
        assert "feed-2" in str(exc_info.value.details)
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_succeeds_when_no_feeds(self):
        """No active feeds allows the connection, its row, and its secrets to be deleted."""
        conn = _make_connection()
        db = _mock_db()

        get_conn_result = _scalar_one_or_none(conn)
        feed_result = MagicMock()
        feed_result.all.return_value = []
        defn_result = _scalar_one_or_none(None)
        expected = [get_conn_result, feed_result, defn_result]
        default = _scalar_one_or_none(None)
        call_count = 0

        async def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return expected[idx] if idx < len(expected) else default

        db.execute = AsyncMock(side_effect=_execute_side_effect)

        pbac_patch, cache_patch = _patch_pbac()
        set_patch, get_patch, list_patch, del_patch = _patch_secrets()
        with pbac_patch, cache_patch, set_patch, get_patch, list_patch, del_patch:
            service = McpService(db)
            await service.delete_connection("conn-1", user_id="admin")

        db.delete.assert_any_await(conn)
        db.commit.assert_awaited()


class TestSyncConnection:
    """Verify tool merging (preserve admin config, add new, remove stale) and health tracking."""

    def _setup_sync(self, conn, tools, init_result=None):
        """Set up mocks for a sync test. Returns (db, mock_client)."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        mock_client = AsyncMock()
        mock_client.connect.return_value = init_result or {"serverInfo": {"name": "test", "version": "1.0"}}
        mock_client.list_tools.return_value = tools
        mock_client.close = AsyncMock()

        return db, mock_client

    @pytest.mark.asyncio
    async def test_merges_new_tools_preserves_existing_config(self):
        """New tools default to chat_callable; existing tools keep their admin config (e.g. field_mapping)."""
        existing_configs = {
            "existing_tool": {
                "chat_callable": False,
                "feed_eligible": True,
                "enabled": True,
                "ingest": {"field_mapping": {"title": "t", "content": "c"}},
            },
        }
        conn = _make_connection(tool_configs=existing_configs)

        tools = [
            McpToolInfo(name="existing_tool", description="old tool"),
            McpToolInfo(name="new_tool", description="brand new"),
        ]
        db, mock_client = self._setup_sync(conn, tools)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, patch.object(McpService, "make_client", new_callable=AsyncMock, return_value=mock_client):
            service = McpService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert "existing_tool" in result.tools
        assert "new_tool" in result.tools
        assert "new_tool" in result.added
        assert "existing_tool" not in result.added

        # Admin config for existing tool is preserved, not overwritten
        assert conn.tool_configs["existing_tool"] == existing_configs["existing_tool"]
        assert conn.tool_configs["new_tool"] == {"chat_callable": True, "feed_eligible": False, "enabled": True}

    @pytest.mark.asyncio
    async def test_removes_tools_no_longer_on_server(self):
        """Tools present in tool_configs but absent from the server are reported as removed."""
        existing_configs = {
            "keep_tool": {"chat_callable": True, "feed_eligible": False, "enabled": True},
            "stale_tool": {"chat_callable": True, "feed_eligible": False, "enabled": True},
        }
        conn = _make_connection(tool_configs=existing_configs)

        tools = [McpToolInfo(name="keep_tool", description="still here")]
        db, mock_client = self._setup_sync(conn, tools)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, patch.object(McpService, "make_client", new_callable=AsyncMock, return_value=mock_client):
            service = McpService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert "stale_tool" in result.removed
        assert "stale_tool" not in conn.tool_configs

    @pytest.mark.asyncio
    async def test_success_resets_failures(self):
        """Successful sync resets consecutive_failures and clears last_error."""
        conn = _make_connection(consecutive_failures=3, last_error="previous error")

        tools = [McpToolInfo(name="tool_a", description="a")]
        db, mock_client = self._setup_sync(conn, tools)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, patch.object(McpService, "make_client", new_callable=AsyncMock, return_value=mock_client):
            service = McpService(db)
            await service.sync_connection("conn-1", user_id="admin")

        assert conn.consecutive_failures == 0
        assert conn.last_error is None
        assert conn.last_connected_at is not None

    @pytest.mark.asyncio
    async def test_failure_increments_consecutive_failures(self):
        """Failed connect increments consecutive_failures and records last_error."""
        conn = _make_connection(consecutive_failures=2, last_error=None)
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        mock_client = AsyncMock()
        mock_client.connect.side_effect = McpError("connection refused")
        mock_client.close = AsyncMock()

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch, patch.object(McpService, "make_client", new_callable=AsyncMock, return_value=mock_client):
            service = McpService(db)
            result = await service.sync_connection("conn-1", user_id="admin")

        assert conn.consecutive_failures == 3
        assert conn.last_error == "connection refused"
        assert result.errors == ["connection refused"]
        assert result.tools == []


class TestGeneratePluginRecord:
    """Verify PluginRecord generation from tool_configs (ops, naming, disabled filtering)."""

    def test_correct_chat_callable_and_feed_ops(self):
        """Enabled chat_callable and ingest tools are split into the correct op lists."""
        conn = _make_connection(
            name="my-mcp",
            server_info={"name": "remote", "version": "2.1"},
            tool_configs={
                "search": {"chat_callable": True, "feed_eligible": False, "enabled": True},
                "fetch_docs": {"chat_callable": False, "feed_eligible": True, "enabled": True},
                "disabled_tool": {"chat_callable": True, "feed_eligible": False, "enabled": False},
                "another_ingest": {"chat_callable": False, "feed_eligible": True, "enabled": True},
            },
        )

        service = McpService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.name == "mcp:my-mcp"
        assert record.version == "2.1"
        assert record.chat_callable_ops == ["search"]
        assert sorted(record.allowed_feed_ops) == ["another_ingest", "fetch_docs"]
        assert record.default_feed_op == "fetch_docs"

    def test_no_enabled_tools_returns_none_ops(self):
        """All tools disabled produces None for all op lists."""
        conn = _make_connection(
            name="empty",
            tool_configs={"only": {"chat_callable": True, "feed_eligible": False, "enabled": False}},
        )

        service = McpService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops is None
        assert record.allowed_feed_ops is None
        assert record.default_feed_op is None

    def test_no_tool_configs_returns_none_ops(self):
        """Null tool_configs produces None for all op lists."""
        conn = _make_connection(name="blank", tool_configs=None)

        service = McpService(AsyncMock())
        record = service.generate_plugin_record(conn)

        assert record.chat_callable_ops is None
        assert record.allowed_feed_ops is None


class TestUpdateToolConfig:
    """Verify per-tool config updates: merge into existing, bootstrap from discovered, reject unknown."""

    @pytest.mark.asyncio
    async def test_updates_existing_tool_config(self):
        """Updating a tool already in tool_configs replaces its entry."""
        conn = _make_connection(
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[{"name": "search", "description": "Search"}],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch:
            service = McpService(db)
            data = McpToolConfigUpdate(
                chat_callable=True,
                feed_eligible=True,
                enabled=True,
                ingest=McpIngestConfig(
                    field_mapping=McpIngestFieldMapping(
                        title="title", content="body", source_id="id"
                    ),
                ),
            )
            result = await service.update_tool_config("conn-1", "search", data, "admin")

        assert result.tool_configs["search"]["feed_eligible"] is True
        assert result.tool_configs["search"]["ingest"] is not None
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_bootstraps_config_for_discovered_but_unconfigured_tool(self):
        """A tool present in discovered_tools but not yet in tool_configs gets created."""
        conn = _make_connection(
            tool_configs={"other": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[
                {"name": "other", "description": "Other"},
                {"name": "new_tool", "description": "New"},
            ],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch:
            service = McpService(db)
            data = McpToolConfigUpdate(chat_callable=True, feed_eligible=False, enabled=False)
            result = await service.update_tool_config("conn-1", "new_tool", data, "admin")

        assert "new_tool" in result.tool_configs
        assert result.tool_configs["new_tool"]["enabled"] is False
        # Original tool untouched
        assert result.tool_configs["other"] == {"chat_callable": True, "feed_eligible": False, "enabled": True}

    @pytest.mark.asyncio
    async def test_raises_not_found_for_unknown_tool(self):
        """A tool name not in tool_configs or discovered_tools raises NotFoundError."""
        conn = _make_connection(
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[{"name": "search", "description": "Search"}],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch:
            service = McpService(db)
            data = McpToolConfigUpdate(chat_callable=True, enabled=True)
            with pytest.raises(NotFoundError, match="nonexistent"):
                await service.update_tool_config("conn-1", "nonexistent", data, "admin")


class TestGenerateAllPluginRecords:
    """Verify bulk record generation from enabled connections."""

    @pytest.mark.asyncio
    async def test_returns_records_for_enabled_connections(self):
        """Each enabled connection with tool_configs produces a PluginRecord."""
        conn_a = _make_connection(
            name="alpha",
            server_info={"version": "1.0"},
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
        )
        conn_b = _make_connection(
            name="beta",
            server_info={"version": "2.0"},
            tool_configs={"ingest_docs": {"chat_callable": False, "feed_eligible": True, "enabled": True}},
        )

        db = _mock_db()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [conn_a, conn_b]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute.return_value = execute_result

        service = McpService(db)
        records = await service.generate_all_plugin_records()

        assert len(records) == 2
        names = {r.name for r in records}
        assert names == {"mcp:alpha", "mcp:beta"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_connections(self):
        """No enabled connections returns an empty list."""
        db = _mock_db()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute.return_value = execute_result

        service = McpService(db)
        records = await service.generate_all_plugin_records()

        assert records == []


class TestListConnections:
    """Verify PBAC-filtered listing returns only authorized connections."""

    @pytest.mark.asyncio
    async def test_filters_by_pbac(self):
        """Connections denied by POLICY_CACHE are excluded from the result."""
        conn_allowed = _make_connection(id="c1", name="allowed")
        conn_denied = _make_connection(id="c2", name="denied")

        db = _mock_db()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [conn_allowed, conn_denied]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute.return_value = execute_result

        denied_set = {"mcp:denied"}

        pbac_patch, _ = _patch_pbac()
        with pbac_patch, patch(
            "shu.services.mcp_service.POLICY_CACHE",
            **{"get_denied_resources": AsyncMock(return_value=denied_set)},
        ):
            service = McpService(db)
            result = await service.list_connections(user_id="user-1")

        assert len(result) == 1
        assert result[0].name == "allowed"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_connections(self):
        """Empty table returns empty list without calling PBAC."""
        db = _mock_db()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        db.execute.return_value = execute_result

        pbac_patch, cache_patch = _patch_pbac()
        with pbac_patch, cache_patch:
            service = McpService(db)
            result = await service.list_connections(user_id="user-1")

        assert result == []


def _scalar_result(value):
    """Build an execute result whose .scalar() returns value."""
    result = MagicMock()
    result.scalar.return_value = value
    return result


class TestIsConnectionEnabled:
    """Verify is_connection_enabled checks the enabled column."""

    @pytest.mark.asyncio
    async def test_returns_true_when_enabled(self):
        """An enabled connection returns True."""
        db = _mock_db()
        db.execute.return_value = _scalar_result(True)

        service = McpService(db)
        assert await service.is_connection_enabled("my-server") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self):
        """A disabled connection returns False."""
        db = _mock_db()
        db.execute.return_value = _scalar_result(False)

        service = McpService(db)
        assert await service.is_connection_enabled("my-server") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        """A non-existent connection returns False."""
        db = _mock_db()
        db.execute.return_value = _scalar_result(None)

        service = McpService(db)
        assert await service.is_connection_enabled("missing") is False


class TestResolveAdapter:
    """Verify resolve_adapter loads connection and builds McpPluginAdapter."""

    @pytest.mark.asyncio
    async def test_returns_adapter_for_enabled_connection(self):
        """An enabled connection produces an McpPluginAdapter instance."""
        conn = _make_connection(name="wiki", enabled=True)
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        mock_client = AsyncMock()

        with patch.object(McpService, "make_client", new_callable=AsyncMock, return_value=mock_client):
            service = McpService(db)
            result = await service.resolve_adapter("wiki")

        assert result is not None
        assert result._connection is conn
        assert result._client is mock_client

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """A missing or disabled connection returns None."""
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(None)

        service = McpService(db)
        result = await service.resolve_adapter("missing")

        assert result is None


class TestGetConnectionSchema:
    """Verify get_connection_schema builds schema from discovered tools."""

    @pytest.mark.asyncio
    async def test_returns_schema_from_discovered_tools(self):
        """A connection with discovered tools returns a valid schema."""
        conn = _make_connection(
            name="wiki",
            tool_configs={"search": {"chat_callable": True, "feed_eligible": False, "enabled": True}},
            discovered_tools=[
                {
                    "name": "search",
                    "description": "Search the wiki",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"],
                    },
                }
            ],
        )
        db = _mock_db()
        db.execute.return_value = _scalar_one_or_none(conn)

        service = McpService(db)
        schema = await service.get_connection_schema("wiki")

        assert schema is not None
        assert schema["type"] == "object"
        assert "op" in schema["properties"]
        assert "search" in schema["properties"]["op"]["enum"]
        assert schema["properties"]["q"]["type"] == "string"
