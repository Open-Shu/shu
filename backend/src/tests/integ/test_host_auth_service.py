from __future__ import annotations

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite


async def _enable_plugin(client, auth_headers, name: str):
    resp = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.patch(
        f"/api/v1/plugins/admin/{name}/enable",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def _get_admin_user_id(db) -> str:
    res = await db.execute(
        text("SELECT id FROM users WHERE email LIKE 'test-admin-%@example.com' ORDER BY created_at DESC LIMIT 1")
    )
    row = res.fetchone()
    assert row and row[0], "Admin user not found for test"
    return str(row[0])


async def test_compute_consent_scopes_empty_when_no_subscriptions(client, db, auth_headers):
    # Enable gmail + drive plugins
    await _enable_plugin(client, auth_headers, "gmail_digest")
    await _enable_plugin(client, auth_headers, "gdrive_files")

    # Call service directly with no subscriptions
    from shu.services.host_auth_service import HostAuthService

    user_id = await _get_admin_user_id(db)
    scopes: list[str] = await HostAuthService.compute_consent_scopes(db, user_id, "google")

    # Expect no scopes when there are no subscriptions
    assert scopes == []


async def test_compute_consent_scopes_honors_subscriptions(client, db, auth_headers):
    await _enable_plugin(client, auth_headers, "gmail_digest")
    await _enable_plugin(client, auth_headers, "gdrive_files")

    from shu.services.host_auth_service import HostAuthService

    user_id = await _get_admin_user_id(db)

    # Subscribe only gmail_digest via service (idempotent)
    await HostAuthService.validate_and_create_subscription(db, user_id, "google", "gmail_digest", None)
    await HostAuthService.validate_and_create_subscription(db, user_id, "google", "gmail_digest", None)

    # Consent scopes should include gmail but not drive
    scopes: list[str] = await HostAuthService.compute_consent_scopes(db, user_id, "google")
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://www.googleapis.com/auth/drive" not in scopes


class HostAuthServiceTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_compute_consent_scopes_empty_when_no_subscriptions,
            test_compute_consent_scopes_honors_subscriptions,
        ]

    def get_suite_name(self) -> str:
        return "HostAuthService"

    def get_suite_description(self) -> str:
        return "Service-level tests for consent scope computation and subscription helpers"


if __name__ == "__main__":
    suite = HostAuthServiceTestSuite()
    exit_code = suite.run()
    import sys as _sys

    _sys.exit(exit_code)
