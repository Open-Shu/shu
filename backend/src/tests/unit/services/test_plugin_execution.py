"""
Unit tests for plugin execution service.
"""

import dataclasses
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.cp_client import HEALTHY_DEFAULT
from shu.billing.entitlements import EntitlementDeniedError, EntitlementSet
from shu.plugins.registry import REGISTRY
from shu.services.plugin_execution import (
    _coerce_params,
    assert_plugin_entitlement,
    build_agent_tools,
    execute_plugin,
    is_mcp_plugin_name,
    mcp_servers_entitled,
    plugin_dispatch_allowed,
)


class TestParamCoercion:
    def test_coerce_params(self):
        schema = {
            "properties": {
                "limit": {"type": "integer"},
                "threshold": {"type": "number"},
                "verbose": {"type": "boolean"},
                "name": {"type": "string"},
            }
        }
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = schema

        params = {
            "limit": "48",
            "threshold": "0.5",
            "verbose": "true",
            "name": "test",
            "other": "ignore",
        }

        result = _coerce_params(mock_plugin, params, "some_op")

        assert result["limit"] == 48
        assert result["threshold"] == 0.5
        assert result["verbose"] is True
        assert result["name"] == "test"
        assert result["other"] == "ignore"

    def test_coerce_params_no_schema(self):
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = None
        mock_plugin.get_schema.return_value = None
        params = {"limit": "48"}
        result = _coerce_params(mock_plugin, params, "some_op")
        assert result == params

    def test_coerce_params_invalid_types(self):
        mock_plugin = MagicMock()
        mock_plugin.name = "test-plugin"
        mock_plugin.get_schema_for_op.return_value = {"properties": {"limit": {"type": "integer"}}}
        params = {"limit": "abc"}
        result = _coerce_params(mock_plugin, params, "some_op")
        assert result["limit"] == "abc"  # Should remain string if not coercible


# SHU-773: the agentic tool path is gated here (build_agent_tools) and
# defensively at dispatch (execute_plugin), since neither touches the
# chat_plugins router that the request-level gate guards.


def _state(**entitlements):
    """HEALTHY_DEFAULT with the entitlement set overridden."""
    return dataclasses.replace(HEALTHY_DEFAULT, entitlements=EntitlementSet(**entitlements))


@contextmanager
def _stub_manifest(names: list[str]):
    """Make build_agent_tools see `names` as resolvable, chat-callable plugins.

    Each name yields one tool. MCP plugins use the internal `mcp:` prefix; the
    resulting CallableTool.name is the wire form (`mcp-`).
    """
    manifest = {n: MagicMock(chat_callable_ops=["read"]) for n in names}
    with (
        patch.object(REGISTRY, "_manifest", manifest, create=True),
        patch(
            "shu.services.plugin_execution.get_allowed_plugin_names",
            new=AsyncMock(return_value=set(names)),
        ),
        patch.object(REGISTRY, "resolve", new=AsyncMock(return_value=MagicMock())),
        patch("shu.services.plugin_execution.resolve_op_schema", return_value={}),
        patch("shu.services.plugin_execution.extract_op_title", return_value="T"),
    ):
        yield


class TestBuildAgentToolsEntitlements:
    """build_agent_tools must honour plugins / mcp_servers entitlements."""

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_returns_all_tools(self, install_stub_cache):
        # No cache installed → (True, True) bypass → full list.
        with _stub_manifest(["github", "mcp:srv"]):
            tools = await build_agent_tools(AsyncMock(), "user-1")
        assert {t.name for t in tools} == {"github", "mcp-srv"}

    @pytest.mark.asyncio
    async def test_plugins_disabled_returns_empty(self, install_stub_cache):
        install_stub_cache(_state(plugins=False, mcp_servers=False))
        with _stub_manifest(["github", "mcp:srv"]):
            tools = await build_agent_tools(AsyncMock(), "user-1")
        assert tools == []

    @pytest.mark.asyncio
    async def test_mcp_disabled_filters_only_mcp_tools(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        with _stub_manifest(["github", "mcp:srv"]):
            tools = await build_agent_tools(AsyncMock(), "user-1")
        assert {t.name for t in tools} == {"github"}

    @pytest.mark.asyncio
    async def test_all_enabled_returns_all_tools(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=True))
        with _stub_manifest(["github", "mcp:srv"]):
            tools = await build_agent_tools(AsyncMock(), "user-1")
        assert {t.name for t in tools} == {"github", "mcp-srv"}


class TestExecutePluginDefensiveCheck:
    """execute_plugin raises before any dispatch when the entitlement is off."""

    @pytest.mark.asyncio
    async def test_plugins_disabled_raises(self, install_stub_cache):
        install_stub_cache(_state(plugins=False))
        with pytest.raises(EntitlementDeniedError):
            await execute_plugin(AsyncMock(), "github", "read", {}, "owner-1")

    @pytest.mark.asyncio
    async def test_mcp_name_with_mcp_disabled_raises(self, install_stub_cache):
        # plugins on, mcp off: the wire-form mcp- name unsanitizes to mcp: and
        # trips the second assertion.
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        with pytest.raises(EntitlementDeniedError):
            await execute_plugin(AsyncMock(), "mcp-srv", "read", {}, "owner-1")


# SHU-773 (H1/H2): the shared plugin-entitlement gate used by the REST dispatch
# routes (plugins_public, chat_plugins) and the scheduler/queue runner — none of
# which pass through build_agent_tools' upstream filter.


class TestIsMcpPluginName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("mcp:Github", True), ("mcp-Github", True), ("github", False), ("gmail", False)],
    )
    def test_classifies_internal_and_wire_forms(self, name, expected):
        assert is_mcp_plugin_name(name) is expected


class TestAssertPluginEntitlement:
    """Hard gate (raises) for the REST execute routes."""

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_allows_mcp(self, install_stub_cache):
        # No cache installed → no enforcement; an mcp plugin must not raise.
        await assert_plugin_entitlement("mcp:srv")

    @pytest.mark.asyncio
    async def test_plugins_off_raises_for_native(self, install_stub_cache):
        install_stub_cache(_state(plugins=False, mcp_servers=False))
        with pytest.raises(EntitlementDeniedError) as exc:
            await assert_plugin_entitlement("github")
        assert exc.value.key == "plugins"

    @pytest.mark.asyncio
    async def test_plugins_on_mcp_off_allows_native_blocks_mcp(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        await assert_plugin_entitlement("github")  # native: no raise
        with pytest.raises(EntitlementDeniedError) as exc:
            await assert_plugin_entitlement("mcp:srv")
        assert exc.value.key == "mcp_servers"

    @pytest.mark.asyncio
    async def test_wire_form_mcp_name_blocked(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        with pytest.raises(EntitlementDeniedError) as exc:
            await assert_plugin_entitlement("mcp-srv")
        assert exc.value.key == "mcp_servers"

    @pytest.mark.asyncio
    async def test_all_enabled_allows_both(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=True))
        await assert_plugin_entitlement("github")
        await assert_plugin_entitlement("mcp:srv")


class TestPluginDispatchAllowed:
    """Soft gate (returns bool) for the scheduler/queue runner."""

    @pytest.mark.asyncio
    async def test_self_hosted_allows(self, install_stub_cache):
        assert await plugin_dispatch_allowed("mcp:srv") is True

    @pytest.mark.asyncio
    async def test_plugins_off_blocks_everything(self, install_stub_cache):
        install_stub_cache(_state(plugins=False, mcp_servers=False))
        assert await plugin_dispatch_allowed("github") is False
        assert await plugin_dispatch_allowed("mcp:srv") is False

    @pytest.mark.asyncio
    async def test_mcp_off_blocks_only_mcp(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        assert await plugin_dispatch_allowed("github") is True
        assert await plugin_dispatch_allowed("mcp:srv") is False

    @pytest.mark.asyncio
    async def test_all_enabled_allows(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=True))
        assert await plugin_dispatch_allowed("mcp:srv") is True


class TestMcpServersEntitled:
    @pytest.mark.asyncio
    async def test_self_hosted_true(self, install_stub_cache):
        assert await mcp_servers_entitled() is True

    @pytest.mark.asyncio
    async def test_reflects_entitlement(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        assert await mcp_servers_entitled() is False
        install_stub_cache(_state(plugins=True, mcp_servers=True))
        assert await mcp_servers_entitled() is True
