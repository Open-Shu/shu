"""Unit tests for the chat-plugins API router.

Covers the SHU-773 entitlement gate: a chat-only tenant can chat but cannot
invoke any plugin op from chat. The gate fires only through FastAPI's Depends()
resolution, so this uses a real app + TestClient; scaffolding lives in conftest.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from shu.api.chat_plugins import router as chat_plugins_router
from tests.unit.api.conftest import assert_entitlement_denied, entitlement_state, gated_app


class TestChatPluginsEntitlementGate:
    def test_plugins_off_returns_403(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=False))
        with TestClient(gated_app(chat_plugins_router)) as client:
            assert assert_entitlement_denied(client.get("/api/v1/chat/plugins/"), "plugins")


class TestChatPluginExecuteMcpGate:
    """SHU-773 (H1): execute bypasses build_agent_tools' filter, so the mcp_servers
    gate runs at dispatch — a plugins-on / mcp-off tenant cannot execute an mcp plugin.
    """

    def test_mcp_execute_blocked_when_mcp_off(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=True, mcp_servers=False))
        with TestClient(gated_app(chat_plugins_router)) as client:
            resp = client.post(
                "/api/v1/chat/plugins/execute",
                json={"name": "mcp:Github", "op": "read", "params": {}},
            )
            assert assert_entitlement_denied(resp, "mcp_servers")
