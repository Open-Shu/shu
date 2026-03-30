"""Unit tests for PluginRegistry MCP integration.

Tests verify that resolve() handles MCP plugins by bypassing PluginLoader,
and that sync() creates/purges PluginDefinition rows for MCP plugins.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.plugins.loader import PluginRecord
from shu.plugins.registry import PluginRegistry


def _mock_connection(name="test-server", enabled=True):
    conn = MagicMock()
    conn.name = name
    conn.enabled = enabled
    conn.url = "https://example.com/mcp"
    conn.tool_configs = {"search": {"type": "chat_callable", "enabled": True}}
    conn.discovered_tools = [{"name": "search"}]
    conn.timeouts = None
    conn.response_size_limit_bytes = None
    conn.server_info = {"version": "1.0"}
    return conn


def _mock_record(name="mcp:test-server"):
    record = MagicMock()
    record.name = name
    return record


def _scalar_result(value):
    """Build an execute result whose .scalar_one_or_none() and .scalar() return value."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    return result


class TestResolveMcp:
    """Verify resolve() handles mcp: plugins via McpPluginAdapter."""

    @pytest.mark.asyncio
    async def test_resolve_mcp_returns_adapter(self):
        """An enabled MCP connection in the manifest produces an McpPluginAdapter instance."""
        registry = PluginRegistry()
        conn = _mock_connection()
        registry._manifest["mcp:test-server"] = _mock_record()

        session = AsyncMock()
        session.execute.return_value = _scalar_result(conn)

        mock_adapter = MagicMock()
        mock_client = AsyncMock()

        with patch("shu.services.mcp_service.McpService.make_client", new_callable=AsyncMock, return_value=mock_client), \
             patch("shu.services.mcp_service.McpPluginAdapter", return_value=mock_adapter) as adapter_cls:

            result = await registry.resolve("mcp:test-server", session)

        assert result is mock_adapter
        adapter_cls.assert_called_once_with(conn, mock_client)
        assert registry._cache["mcp:test-server"] is mock_adapter

    @pytest.mark.asyncio
    async def test_resolve_mcp_disabled_returns_none(self):
        """A disabled MCP connection returns None even if in the manifest."""
        registry = PluginRegistry()
        registry._manifest["mcp:disabled"] = _mock_record("mcp:disabled")

        session = AsyncMock()
        session.execute.return_value = _scalar_result(None)

        result = await registry.resolve("mcp:disabled", session)

        assert result is None
        assert "mcp:disabled" not in registry._cache

    @pytest.mark.asyncio
    async def test_resolve_mcp_cached_returns_cache_if_enabled(self):
        """A cached MCP adapter is returned directly if the connection is still enabled."""
        registry = PluginRegistry()
        cached_adapter = MagicMock()
        registry._cache["mcp:cached"] = cached_adapter

        session = AsyncMock()
        # _is_mcp_enabled queries McpServerConnection.enabled
        session.execute.return_value = _scalar_result(True)

        result = await registry.resolve("mcp:cached", session)

        assert result is cached_adapter

    @pytest.mark.asyncio
    async def test_resolve_mcp_cached_evicts_if_disabled(self):
        """A cached MCP adapter is evicted and None returned if the connection was disabled."""
        registry = PluginRegistry()
        registry._cache["mcp:gone"] = MagicMock()

        session = AsyncMock()
        session.execute.return_value = _scalar_result(False)

        result = await registry.resolve("mcp:gone", session)

        assert result is None
        assert "mcp:gone" not in registry._cache

    @pytest.mark.asyncio
    async def test_resolve_mcp_not_in_manifest_returns_none(self):
        """An MCP plugin name not in the manifest returns None."""
        registry = PluginRegistry()
        registry._manifest = {"native_plugin": _mock_record("native_plugin")}

        session = AsyncMock()

        result = await registry.resolve("mcp:unknown", session)

        assert result is None


class TestSyncMcpDefinitions:
    """Verify sync() creates/updates/purges PluginDefinition rows for MCP plugins."""

    @pytest.mark.asyncio
    async def test_creates_plugin_definition_for_new_mcp_plugin(self):
        """A new MCP plugin in the manifest gets a PluginDefinition row (enabled=False)."""
        registry = PluginRegistry()
        mcp_record = PluginRecord(
            name="mcp:wiki",
            version="2.0",
            entry="shu.plugins.mcp_adapter:McpPluginAdapter",
            capabilities=["http", "kb"],
        )
        registry._manifest = {"mcp:wiki": mcp_record}

        session = AsyncMock()
        # First execute: check if PluginDefinition exists for mcp:wiki → None
        scalars_first = MagicMock()
        scalars_first.first.return_value = None
        exec_result_none = MagicMock()
        exec_result_none.scalars.return_value = scalars_first
        # Second execute: get_connection_schema → McpServerConnection query → None
        exec_result_conn = MagicMock()
        exec_result_conn.scalar_one_or_none.return_value = None
        # Third execute: is_connection_enabled → McpServerConnection.enabled → None (not found)
        exec_result_enabled = MagicMock()
        exec_result_enabled.scalar.return_value = None
        # Fourth execute: purge query returns empty
        scalars_all = MagicMock()
        scalars_all.all.return_value = []
        exec_result_all = MagicMock()
        exec_result_all.scalars.return_value = scalars_all

        session.execute = AsyncMock(side_effect=[exec_result_none, exec_result_conn, exec_result_enabled, exec_result_all])
        session.add = MagicMock()

        with patch.object(registry, "refresh"), \
             patch.object(registry, "_refresh_mcp", new_callable=AsyncMock):
            registry._manifest = {"mcp:wiki": mcp_record}
            result = await registry.sync(session)

        session.add.assert_called_once()
        added_row = session.add.call_args[0][0]
        assert added_row.name == "mcp:wiki"
        assert added_row.version == "2.0"
        assert added_row.enabled is False
        assert result["created"] == 1

    @pytest.mark.asyncio
    async def test_purges_mcp_plugin_definition_when_connection_removed(self):
        """An MCP PluginDefinition row is purged when its connection no longer exists."""
        registry = PluginRegistry()
        registry._manifest = {}  # No MCP connections

        session = AsyncMock()
        # Purge query returns a stale MCP row
        stale_row = MagicMock()
        stale_row.name = "mcp:removed"
        scalars_all = MagicMock()
        scalars_all.all.return_value = [stale_row]
        exec_result_all = MagicMock()
        exec_result_all.scalars.return_value = scalars_all
        session.execute = AsyncMock(return_value=exec_result_all)

        with patch.object(registry, "refresh"), \
             patch.object(registry, "_refresh_mcp", new_callable=AsyncMock):
            result = await registry.sync(session)

        session.delete.assert_awaited_once_with(stale_row)
        assert result["purged"] == 1

    @pytest.mark.asyncio
    async def test_existing_mcp_definition_not_recreated(self):
        """An existing PluginDefinition row for an MCP plugin is not duplicated."""
        registry = PluginRegistry()
        mcp_record = PluginRecord(
            name="mcp:existing",
            version="1.0",
            entry="shu.plugins.mcp_adapter:McpPluginAdapter",
            capabilities=["http", "kb"],
        )
        registry._manifest = {"mcp:existing": mcp_record}

        existing_row = MagicMock()
        existing_row.name = "mcp:existing"
        existing_row.version = "1.0"
        existing_row.input_schema = None

        session = AsyncMock()
        # First execute: PluginDefinition exists
        scalars_first = MagicMock()
        scalars_first.first.return_value = existing_row
        exec_result_exists = MagicMock()
        exec_result_exists.scalars.return_value = scalars_first
        # Second execute: McpServerConnection query → None
        exec_result_conn = MagicMock()
        exec_result_conn.scalar_one_or_none.return_value = None
        # Third execute: purge query returns the same row (in discovered_names, so not purged)
        scalars_all = MagicMock()
        scalars_all.all.return_value = [existing_row]
        exec_result_all = MagicMock()
        exec_result_all.scalars.return_value = scalars_all

        session.execute = AsyncMock(side_effect=[exec_result_exists, exec_result_conn, exec_result_all])
        session.add = MagicMock()

        with patch.object(registry, "refresh"), \
             patch.object(registry, "_refresh_mcp", new_callable=AsyncMock):
            registry._manifest = {"mcp:existing": mcp_record}
            result = await registry.sync(session)

        session.add.assert_not_called()
        assert result["created"] == 0
