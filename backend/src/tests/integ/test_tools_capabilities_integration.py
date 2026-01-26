"""
Integration tests for Tools HostCapabilities: http allowlist, auth (JWT bearer helper), and kb upsert.

Implementation aligns with docs/policies/TESTING.md and uses the custom integration runner.
"""
import asyncio
import json
import logging
import uuid
from unittest.mock import patch, AsyncMock

from typing import Any, Dict

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script


logger = logging.getLogger(__name__)


# --- Test functions ---
async def test_http_egress_allowlist_blocks_disallowed_domain(client, db, auth_headers):
    """host.http should deny requests to domains not in SHU_HTTP_EGRESS_ALLOWLIST."""
    logger.info("=== EXPECTED TEST OUTPUT: An EgressDenied warning is expected below for disallowed domain ===")

    from shu.core.config import get_settings_instance
    from shu.plugins.host.http_capability import HttpCapability
    from shu.plugins.host.exceptions import EgressDenied

    # Restrict allowlist to OAuth endpoint only
    settings = get_settings_instance()
    settings.http_egress_allowlist = ["oauth2.googleapis.com"]

    http = HttpCapability(plugin_name="unit_test", user_id="test-user")

    # Disallowed host should raise EgressDenied
    try:
        await http.fetch("GET", "https://example.com/")
        raise AssertionError("Expected EgressDenied for disallowed domain, but request succeeded")
    except EgressDenied:
        logger.info("=== EXPECTED TEST OUTPUT: EgressDenied occurred as expected for disallowed domain ===")


async def test_auth_google_service_account_token_with_stub_exchange(client, db, auth_headers):
    """host.auth.google_service_account_token should produce an access token when the exchange succeeds.

    This test avoids external dependencies by:
    - Stubbing the jwt module import used inside jwt_bearer_assertion
    - Stubbing the HTTP token exchange
    """
    import sys

    from shu.core.config import get_settings_instance
    from shu.plugins.host.auth_capability import AuthCapability

    # Provide a fake jwt module to satisfy import inside jwt_bearer_assertion without PyJWT installed
    class _FakeJWTModule:
        def encode(self, payload, key, algorithm=None, headers=None):  # accept headers kwarg like PyJWT
            return "stub-assertion"

    sys.modules.setdefault("jwt", _FakeJWTModule())

    # Minimal service account info; private key won't be used by our fake encoder
    sa_info = {
        "type": "service_account",
        "client_email": "svc@example.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n",
        "private_key_id": "test-key-id",
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    settings = get_settings_instance()
    # Inject service account JSON directly and domain guard
    settings.google_service_account_json = json.dumps(sa_info)
    settings.google_domain = "example.com"
    # Allowlist OAuth domain (the call is stubbed, but this keeps policy coherent)
    settings.http_egress_allowlist = ["oauth2.googleapis.com"]

    auth = AuthCapability(plugin_name="unit_test", user_id="test-user")

    # Stub the network exchange to avoid external calls
    async def _stub_post_form(self, url: str, data: Dict[str, str]) -> Dict[str, Any]:
        assert "assertion" in data
        return {"access_token": "stub-token", "expires_in": 3600}

    # Patch at class level to avoid immutable instance attribute error
    with patch.object(AuthCapability, "_post_form", _stub_post_form):
        token = await auth.google_service_account_token(scopes=["https://www.googleapis.com/auth/gmail.readonly"], subject="user@example.com")
    assert token == "stub-token"


async def test_kb_upsert_happy_path(client, db, auth_headers):
    """host.kb.upsert_knowledge_object should persist content and produce chunks."""
    from sqlalchemy import text

    from shu.plugins.host.kb_capability import KbCapability

    # 1) Create a Knowledge Base via API
    kb_data = {
        "name": f"Test Tools KB {uuid.uuid4().hex[:8]}",
        "description": "KB for tools capability tests",
        "sync_enabled": True,
    }
    resp = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    kb_id = resp.json()["data"]["id"]

    # 2) Upsert a KO into the KB using a supported SourceType (filesystem)
    ko_external_id = f"test-ko-{uuid.uuid4().hex[:8]}"
    ko = {
        "external_id": ko_external_id,
        "title": "Test KO",
        "type": "txt",
        "content": "Hello from KO integration test.",
        "source": {"source_type": "filesystem"},
        "attributes": {"source_url": "https://local.test/ko"},
    }

    kb_cap = KbCapability(plugin_name="unit_test", user_id="test-user")
    ko_id = await kb_cap.upsert_knowledge_object(kb_id, ko)
    assert isinstance(ko_id, str) and len(ko_id) > 0

    # 3) Verify Document was created and chunked
    result = await db.execute(
        text(
            """
            SELECT d.id, d.title, d.content, d.chunk_count
            FROM documents d
            WHERE d.knowledge_base_id = :kb_id AND d.source_id = :source_id
            """
        ),
        {"kb_id": kb_id, "source_id": ko_external_id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[1] == "Test KO"
    assert row[3] is not None and int(row[3]) >= 1


# --- Suite wrapper ---
class ToolsCapabilitiesTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_http_egress_allowlist_blocks_disallowed_domain,
            test_auth_google_service_account_token_with_stub_exchange,
            test_kb_upsert_happy_path,
        ]

    def get_suite_name(self) -> str:
        return "Tools Capabilities"

    def get_suite_description(self) -> str:
        return "Integration tests for host.http allowlist, host.auth JWT helper, and host.kb upsert"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(ToolsCapabilitiesTestSuite, globals())


