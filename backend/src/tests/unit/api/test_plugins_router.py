"""Unit tests for the plugins API aggregator router.

Covers the SHU-773 entitlement gate wired onto plugins_router plus the
self-hosted bypass. The gate fires only through FastAPI's Depends() resolution,
so these use a real app + TestClient (same justification as the SHU-703 gate
test in test_knowledge_bases.py); shared scaffolding lives in conftest.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from shu.api.plugins_router import router as plugins_router
from tests.unit.api.conftest import assert_entitlement_denied, entitlement_state, gated_app


class TestPluginsRouterEntitlementGate:
    def test_plugins_off_returns_403(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=False))
        with TestClient(gated_app(plugins_router)) as client:
            assert assert_entitlement_denied(client.get("/api/v1/plugins"), "plugins")

    def test_self_hosted_bypass_lets_request_through(self, install_stub_cache):
        # No cache installed → gate is a no-op; the request reaches the handler,
        # which then 500s on the mocked DB. A non-403 proves the gate passed —
        # raise_server_exceptions=False turns the handler crash into a response
        # rather than re-raising it out of the client.
        with TestClient(gated_app(plugins_router), raise_server_exceptions=False) as client:
            assert client.get("/api/v1/plugins").status_code != 403


class TestPluginsPublicExecuteMcpGate:
    """SHU-773 (H1): the direct /plugins/{name}/execute route calls EXECUTOR
    directly, so the mcp_servers gate has to run at dispatch — otherwise a
    plugins-on / mcp-off tenant could execute an mcp plugin through it. The gate
    runs before any registry/DB work, so no plugin stubbing is needed.
    """

    def test_mcp_execute_blocked_when_mcp_off(self, install_stub_cache):
        install_stub_cache(entitlement_state(plugins=True, mcp_servers=False))
        with TestClient(gated_app(plugins_router)) as client:
            resp = client.post("/api/v1/plugins/mcp:Github/execute", json={"params": {}})
            assert assert_entitlement_denied(resp, "mcp_servers")
