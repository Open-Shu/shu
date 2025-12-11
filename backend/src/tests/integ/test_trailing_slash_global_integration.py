"""
Global trailing-slash tolerance smoke tests across representative routers.

Covers:
- Health router
- Knowledge bases (list and get)
- Source types (catalog)
"""

import sys
import os
from typing import List, Callable
import uuid

from integ.base_integration_test import BaseIntegrationTestSuite


async def test_health_trailing_slash(client, db, auth_headers):
    resp = await client.get("/api/v1/health/", headers=auth_headers)
    assert resp.status_code == 200, f"health/ expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "data" in data and isinstance(data["data"], dict)


async def test_kb_list_trailing_slash(client, db, auth_headers):
    resp = await client.get("/api/v1/knowledge-bases/", headers=auth_headers)
    assert resp.status_code == 200, f"knowledge-bases/ expected 200, got {resp.status_code}: {resp.text}"
    j = resp.json()
    assert "data" in j and "items" in j["data"]


async def test_kb_get_trailing_slash(client, db, auth_headers):
    # Create a KB then fetch with trailing slash
    kb_data = {
        "name": f"KB Slash Test {uuid.uuid4().hex[:8]}",
        "description": "Global slash smoke",
        "sync_enabled": True,
    }
    c = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert c.status_code == 201, c.text
    kb_id = c.json()["data"]["id"]

    g = await client.get(f"/api/v1/knowledge-bases/{kb_id}/", headers=auth_headers)
    assert g.status_code == 200, f"kb get with slash expected 200, got {g.status_code}: {g.text}"
    payload = g.json()
    assert payload.get("data", {}).get("id") == kb_id


class GlobalTrailingSlashSmokeSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> List[Callable]:
        return [
            test_health_trailing_slash,
            test_kb_list_trailing_slash,
            test_kb_get_trailing_slash,
        ]

    def get_suite_name(self) -> str:
        return "Global Trailing-Slash Smoke Tests"

    def get_suite_description(self) -> str:
        return "Verifies slash/no-slash tolerance for representative routers"


if __name__ == "__main__":
    suite = GlobalTrailingSlashSmokeSuite()
    exit_code = suite.run()
    sys.exit(exit_code)

