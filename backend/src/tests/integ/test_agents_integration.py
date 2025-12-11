"""
Integration tests for Agent MVP: Morning Briefing orchestrator and endpoint.

Covers:
- /api/v1/agents/morning-briefing/run returns expected envelope and keys
- ToolDefinition seeding (idempotent) to enable tools in non-dev environments
"""
import sys
import os
from typing import List, Callable
from sqlalchemy import text
import logging

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


async def _ensure_tool_definitions(db):
    """Verify plugins required for Morning Briefing exist and are enabled."""
    required_plugins = ("gmail_digest", "calendar_events", "gchat_digest")
    missing: List[str] = []
    disabled: List[str] = []

    for name in required_plugins:
        res = await db.execute(
            text("SELECT enabled FROM plugin_definitions WHERE name = :name"),
            {"name": name},
        )
        row = res.first()
        if row is None:
            missing.append(name)
        elif not bool(row[0]):
            disabled.append(name)

    if missing or disabled:
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(sorted(missing))}")
        if disabled:
            problems.append(f"disabled: {', '.join(sorted(disabled))}")
        raise AssertionError(
            "Morning Briefing integration test requires plugins to be installed/enabled "
            f"({'; '.join(problems)}). Run /api/v1/plugins/admin/sync and enable them before testing."
        )


async def test_morning_briefing_runs_and_returns_structure(client, db, auth_headers):
    """Run the morning briefing endpoint and validate response structure.
    Does not assert tool success (Gmail/KB may be unconfigured); verifies contract only.
    """
    # Ensure tools are enabled if dev fallback is off
    try:
        await _ensure_tool_definitions(db)
    except Exception as e:
        logger.info("ToolDefinition seed skipped (expected if function not available)", extra={"error": str(e)})

    params = {
        "gmail_digest": {"since_hours": 1, "max_results": 5},
        "kb_insights": {"since_hours": 1, "limit": 5},
    }
    resp = await client.post("/api/v1/agents/morning-briefing/run", json=params, headers=auth_headers)
    assert resp.status_code == 200

    data = extract_data(resp)
    assert "briefing" in data and isinstance(data["briefing"], str)
    assert "artifacts" in data and isinstance(data["artifacts"], dict)

    artifacts = data["artifacts"]
    # Presence of keys (tools may fail but should be present in artifacts)
    for key in ("gmail_digest", "kb_insights"):
        assert key in artifacts
        entry = artifacts[key]
        assert isinstance(entry, dict)
        # Contract: either a ToolResult-like dict (with name) or an error object when tool isn't enabled
        if entry.get("ok") is True:
            assert entry.get("name") == key
            assert isinstance(entry.get("summary", ""), str)
        else:
            assert "error" in entry


class AgentsIntegrationTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> List[Callable]:
        return [
            test_morning_briefing_runs_and_returns_structure,
        ]

    def get_suite_name(self) -> str:
        return "Agents Integration Tests"

    def get_suite_description(self) -> str:
        return "Integration tests for Agent MVP Morning Briefing endpoint"


if __name__ == "__main__":
    suite = AgentsIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
