"""Unit tests for the plugin-feeds admin API.

Covers the SHU-773 (M2 follow-up) MCP gate: the feeds router is gated on
`plugins`, but an mcp: feed additionally requires `mcp_servers`, so a
plugins-on / mcp-off tenant can't create or run MCP feed work. Endpoint
functions are called directly with mocked deps (project convention).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.api.plugins_feeds import CreateScheduleRequest, admin_create_schedule, admin_run_schedule_now
from shu.billing.entitlements import EntitlementDeniedError
from tests.unit.api.conftest import entitlement_state


class TestFeedMcpEntitlementGate:
    @pytest.mark.asyncio
    async def test_create_mcp_feed_blocked_when_mcp_off(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=True, mcp_servers=False))
        body = CreateScheduleRequest(name="f", plugin_name="mcp:Github")

        with pytest.raises(EntitlementDeniedError) as exc:
            await admin_create_schedule(body, db=AsyncMock(), admin=MagicMock(id="u1"))
        assert exc.value.key == "mcp_servers"

    @pytest.mark.asyncio
    async def test_run_now_mcp_feed_blocked_when_mcp_off(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=True, mcp_servers=False))

        sched = MagicMock(enabled=True, plugin_name="mcp:Github")
        result = MagicMock()
        result.scalars.return_value.first.return_value = sched
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)

        with pytest.raises(EntitlementDeniedError) as exc:
            await admin_run_schedule_now("sched-1", db=db, admin=MagicMock(id="u1"))
        assert exc.value.key == "mcp_servers"
