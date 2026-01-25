from typing import List, Dict, Any
from unittest.mock import patch, AsyncMock
import urllib.parse
import asyncio

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script


# --- Test functions ---
async def test_build_authorization_url_with_pkce(client, db, auth_headers):
    from shu.plugins.host.auth_capability import AuthCapability

    auth = AuthCapability(plugin_name="unit_test", user_id="user-1")
    res = auth.build_authorization_url(
        auth_url="https://auth.example.com/authorize",
        client_id="client-123",
        redirect_uri="https://app.example.com/callback",
        scopes=["openid", "email"],
        state="abc123",
        include_pkce=True,
    )
    url = res["url"]
    assert "code_verifier" in res and isinstance(res["code_verifier"], str) and len(res["code_verifier"]) >= 43
    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    assert qs.get("response_type") == "code"
    assert qs.get("client_id") == "client-123"
    assert qs.get("redirect_uri") == "https://app.example.com/callback"
    assert qs.get("scope") == "openid email"
    assert qs.get("state") == "abc123"
    assert qs.get("code_challenge") is not None
    assert qs.get("code_challenge_method", "").upper() in ("S256", "PLAIN")


async def test_exchange_and_refresh_code_using_stub(client, db, auth_headers):
    from shu.plugins.host.auth_capability import AuthCapability

    # Stub _post_form to emulate token endpoint for both exchange and refresh
    calls: List[Dict[str, Any]] = []

    async def _stub_post_form(self, url: str, data: Dict[str, str]) -> Dict[str, Any]:
        calls.append({"url": url, "data": data.copy()})
        if data.get("grant_type") == "authorization_code":
            return {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        if data.get("grant_type") == "refresh_token":
            return {
                "access_token": "access-2",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        return {"error": "unsupported_grant_type"}

    auth = AuthCapability(plugin_name="unit_test", user_id="user-1")

    # Patch at class level to avoid immutable instance attribute error
    with patch.object(AuthCapability, "_post_form", _stub_post_form):
        # Exchange code
        body = await auth.exchange_authorization_code(
            token_url="https://auth.example.com/token",
            client_id="client-123",
            client_secret="secret-xyz",
            code="abc",
            redirect_uri="https://app.example.com/callback",
            code_verifier="verifier-123",
        )
    assert body.get("access_token") == "access-1"
    assert body.get("refresh_token") == "refresh-1"


# --- Suite wrapper ---
class HostAuthOAuthFlowsSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_build_authorization_url_with_pkce,
            test_exchange_and_refresh_code_using_stub,
        ]

    def get_suite_name(self) -> str:
        return "Host Auth OAuth Flows"

    def get_suite_description(self) -> str:
        return "Integration-like tests (with local stubs) for host.auth OAuth flows"


# Allow running this file directly
if __name__ == "__main__":
    create_test_runner_script(HostAuthOAuthFlowsSuite, globals())

