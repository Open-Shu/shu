"""Unit tests for the model-configuration API router.

Covers the SHU-773 selective gate: management routes require
`model_config_management`, while the list/get reads stay open so the chat model
picker keeps working on tiers without the entitlement. The gate fires only
through FastAPI's Depends() resolution, so these use a real app + TestClient;
scaffolding lives in conftest.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from shu.api.model_configuration import router as model_config_router
from tests.unit.api.conftest import assert_entitlement_denied, entitlement_state, gated_app


class TestModelConfigurationEntitlementGate:
    def test_management_off_blocks_delete(self, install_stub_cache):
        install_stub_cache(entitlement_state(model_config_management=False))
        with TestClient(gated_app(model_config_router)) as client:
            assert assert_entitlement_denied(
                client.delete("/api/v1/model-configurations/some-id"), "model_config_management"
            )

    def test_list_stays_open_when_management_off(self, install_stub_cache):
        install_stub_cache(entitlement_state(model_config_management=False))
        with TestClient(gated_app(model_config_router)) as client:
            # GET list is ungated; auth is overridden, so the only thing that
            # could 403 is the entitlement gate — which isn't on this route.
            assert client.get("/api/v1/model-configurations").status_code != 403
