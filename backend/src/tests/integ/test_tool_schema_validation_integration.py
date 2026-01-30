"""
Integration tests for Tool Schema v1 enforcement via Executor: input and output validation.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


async def _load_test_plugin():
    from shu.plugins.loader import PluginLoader

    loader = PluginLoader()
    records = loader.discover()
    assert "test_schema" in records, f"test_schema plugin not discovered; found={list(records.keys())}"
    return loader.load(records["test_schema"])  # returns ToolPlugin


async def test_params_validation_missing_required(client, db, auth_headers):
    plugin = await _load_test_plugin()
    from shu.plugins.executor import EXECUTOR

    try:
        await EXECUTOR.execute(plugin=plugin, user_id="u1", user_email=None, agent_key=None, params={})
        raise AssertionError("Expected HTTPException for missing required param 'q'")
    except HTTPException as e:
        assert e.status_code == 422
        assert isinstance(e.detail, dict) and e.detail.get("error") == "validation_error"


async def test_params_and_output_validation_success(client, db, auth_headers):
    plugin = await _load_test_plugin()
    from shu.plugins.executor import EXECUTOR

    result = await EXECUTOR.execute(plugin=plugin, user_id="u1", user_email=None, agent_key=None, params={"q": "hello"})
    assert result.status == "success"
    assert result.data and result.data.get("echo") == "hello"


class ToolSchemaValidationSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_params_validation_missing_required,
            test_params_and_output_validation_success,
        ]

    def get_suite_name(self) -> str:
        return "Tool Schema v1 Validation"

    def get_suite_description(self) -> str:
        return "Validates executor input/output schema enforcement using a minimal test plugin"


if __name__ == "__main__":
    create_test_runner_script(ToolSchemaValidationSuite, globals())
