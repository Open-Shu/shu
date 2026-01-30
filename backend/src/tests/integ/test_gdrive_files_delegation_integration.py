"""
Integration tests for Google Drive gdrive_files plugin with domain-wide delegation.

These tests will:
- Verify we can mint a delegated access token using the configured service account
- Execute the gdrive_files plugin with auth_mode=domain_delegate against a known folder id

Requirements:
- .env must define GOOGLE_SERVICE_ACCOUNT_JSON pointing to a valid service account key file (or inline JSON)
- An impersonation subject email must be available via either TEST_GOOGLE_IMPERSONATE_EMAIL or GOOGLE_ADMIN_USER_EMAIL
- The subject user must have access to the provided folder id
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from integ.integration_test_runner import run_integration_tests

FOLDER_ID = os.getenv("TEST_GDRIVE_FOLDER_ID", "1vVDP-xy3lQxw_jr5yZHOtwytx63JuMbt")


async def _ensure_tool_enabled(db, name: str = "gdrive_files"):
    from sqlalchemy import select

    from shu.models.plugin_registry import PluginDefinition

    existing = (
        (
            await db.execute(
                select(PluginDefinition).where(PluginDefinition.name == name, PluginDefinition.version == "1")
            )
        )
        .scalars()
        .first()
    )
    if existing:
        existing.enabled = True
        await db.commit()
        await db.refresh(existing)
        return existing
    row = PluginDefinition(name=name, version="1", enabled=True)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_gdrive_delegation_token_exchange(client, db, auth_headers):
    """Obtain a delegated access token using host.auth with service account and subject.
    If Admin Console is not configured for domain-wide delegation, log and skip rather than failing the suite.
    """
    # Determine subject email
    from shu.core.config import get_settings_instance

    settings = get_settings_instance()
    subject = os.getenv("TEST_GOOGLE_IMPERSONATE_EMAIL") or (settings.google_admin_user_email or None)
    assert (
        subject
    ), "Impersonation subject not set. Set TEST_GOOGLE_IMPERSONATE_EMAIL or GOOGLE_ADMIN_USER_EMAIL in .env"

    # Build a host exposing only http+auth
    from shu.plugins.host.host_builder import make_host

    host = make_host(
        plugin_name="gdrive_files",
        user_id="u1",
        user_email="u1@example.com",
        capabilities=["http", "auth"],
    )  # type: ignore[arg-type]

    scopes = [
        "https://www.googleapis.com/auth/drive",
    ]

    try:
        token = await host.auth.google_service_account_token(scopes=scopes, subject=subject)
        assert isinstance(token, str) and len(token) > 20, "Delegated access token not returned"
    except RuntimeError as e:
        msg = str(e)
        if "unauthorized_client" in msg:
            print("[diagnostic] Domain-wide delegation not authorized for scope; skipping delegation token test.")
            return
        raise


async def test_gdrive_files_ingest_domain_delegate(client, db, auth_headers):
    """Execute the plugin end-to-end using domain delegation and the provided folder id.
    If domain-wide delegation is not authorized, log and skip.
    """
    # Arrange: ensure tool enabled and create a KB
    await _ensure_tool_enabled(db, "gdrive_files")

    kb_payload = {
        "name": f"Test GDrive KB {uuid.uuid4().hex[:8]}",
        "description": "KB for gdrive_files domain delegation test",
        "sync_enabled": True,
    }
    r = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    kb_id = (r.json().get("data") or {}).get("id")
    assert kb_id, "KB creation did not return id"

    from shu.core.config import get_settings_instance

    settings = get_settings_instance()
    subject = os.getenv("TEST_GOOGLE_IMPERSONATE_EMAIL") or (settings.google_admin_user_email or None)
    assert (
        subject
    ), "Impersonation subject not set. Set TEST_GOOGLE_IMPERSONATE_EMAIL or GOOGLE_ADMIN_USER_EMAIL in .env"

    # Act: execute plugin via API
    body: dict[str, Any] = {
        "params": {
            "op": "ingest",
            "kb_id": kb_id,
            # Unified param: try as Shared Drive, fallback to Folder
            "container_id": FOLDER_ID,
            # Explicitly set domain delegation mode and subject
            "auth_mode": "domain_delegate",
            "impersonate_email": subject,
            # Keep traversal reasonable; modify as needed
            "recursive": True,
            "include_shared": True,
            "page_size": 50,
        }
    }
    resp = await client.post("/api/v1/plugins/gdrive_files/execute", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json().get("data") or {}
    assert payload.get("status") in ("success", "error"), payload
    if payload.get("status") == "error":
        err_msg = ((payload.get("error") or {}).get("message") or "").lower()
        if "domain-wide delegation" in err_msg or "failed to obtain access token" in err_msg:
            print("[diagnostic] Domain-wide delegation ingest failed; skipping.")
            return
        raise AssertionError(f"Plugin returned error: {payload}")

    # Expect some processing to have occurred (even if no ingests due to filters)


async def test_gdrive_files_ingest_service_account(client, db, auth_headers):
    """Execute the plugin using direct service account (no impersonation) to mirror legacy behavior."""
    await _ensure_tool_enabled(db, "gdrive_files")
    kb_payload = {
        "name": f"Test GDrive KB {uuid.uuid4().hex[:8]}",
        "description": "KB for gdrive_files service account test",
        "sync_enabled": True,
    }
    r = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    kb_id = (r.json().get("data") or {}).get("id")
    assert kb_id, "KB creation did not return id"

    body: dict[str, Any] = {
        "params": {
            "op": "ingest",
            "kb_id": kb_id,
            # Unified param
            "container_id": FOLDER_ID,
            "auth_mode": "service_account",
            "recursive": True,
            "include_shared": True,
            "page_size": 50,
        }
    }
    resp = await client.post("/api/v1/plugins/gdrive_files/execute", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json().get("data") or {}
    assert payload.get("status") == "success", payload
    data = payload.get("data") or {}
    # Regression guard: ensure we actually discovered files
    assert isinstance(data.get("processed"), int) and data.get("processed", 0) >= 1, data


if __name__ == "__main__":
    asyncio.run(
        run_integration_tests(
            [
                test_gdrive_delegation_token_exchange,
                test_gdrive_files_ingest_domain_delegate,
                test_gdrive_files_ingest_service_account,
            ],
            enable_file_logging=True,
        )
    )
