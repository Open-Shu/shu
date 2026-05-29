"""Unit tests for the shared plugin-execution runner.

Covers the SHU-773 (H2) entitlement preflight: scheduled/queued executions never
pass through build_agent_tools' upstream filter, so a downgraded tenant's existing
feeds must be skipped here rather than kept running — including MCP feeds after
mcp_servers is revoked.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.cp_client import HEALTHY_DEFAULT
from shu.billing.entitlements import EntitlementSet
from shu.models.plugin_execution import PluginExecutionStatus
from shu.plugins.registry import REGISTRY
from shu.services.plugin_execution_runner import execute_plugin_record


def _state(**entitlements):
    return dataclasses.replace(HEALTHY_DEFAULT, entitlements=EntitlementSet(**entitlements))


def _rec(plugin_name: str):
    # schedule_id=None skips the feed lookup so the entitlement preflight is the
    # first gate reached; SimpleNamespace is enough — the early returns only set
    # attributes on the record and read plugin_name.
    return SimpleNamespace(plugin_name=plugin_name, schedule_id=None)


class TestExecutePluginRecordEntitlementPreflight:
    @pytest.mark.asyncio
    async def test_plugins_off_skips_before_dispatch(self, install_stub_cache):
        install_stub_cache(_state(plugins=False, mcp_servers=False))
        rec = _rec("github")
        with patch.object(REGISTRY, "resolve", new=AsyncMock()) as resolve:
            result = await execute_plugin_record(AsyncMock(), rec, MagicMock())
        assert result.status == PluginExecutionStatus.FAILED
        assert result.error == "entitlement_revoked"
        assert result.skipped is True
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mcp_record_blocked_when_mcp_off(self, install_stub_cache):
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        rec = _rec("mcp:Github")
        with patch.object(REGISTRY, "resolve", new=AsyncMock()) as resolve:
            result = await execute_plugin_record(AsyncMock(), rec, MagicMock())
        assert result.error == "entitlement_revoked"
        resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_native_record_passes_gate_when_mcp_off(self, install_stub_cache):
        # plugins on, mcp off: a native feed must still run. Resolve→None proves
        # we got past the entitlement gate (reaching the plugin-not-found path).
        install_stub_cache(_state(plugins=True, mcp_servers=False))
        rec = _rec("github")
        with patch.object(REGISTRY, "resolve", new=AsyncMock(return_value=None)) as resolve:
            result = await execute_plugin_record(AsyncMock(), rec, MagicMock())
        assert result.error == "plugin_not_found"
        resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_self_hosted_bypass_passes_gate(self, install_stub_cache):
        # No cache installed → enforcement disabled; even an mcp record proceeds.
        rec = _rec("mcp:Github")
        with patch.object(REGISTRY, "resolve", new=AsyncMock(return_value=None)) as resolve:
            result = await execute_plugin_record(AsyncMock(), rec, MagicMock())
        assert result.error == "plugin_not_found"
        resolve.assert_awaited_once()
