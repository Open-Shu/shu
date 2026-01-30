"""
Integration tests for plugin import guards: static scan and runtime deny-hook.
"""

from __future__ import annotations

import logging

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


async def test_loader_static_scan_blocks_shu_import(client, db, auth_headers):
    from shu.plugins.loader import PluginLoader

    loader = PluginLoader()
    records = loader.discover()
    assert "bad_import" in records, f"bad_import plugin not discovered; found={list(records.keys())}"
    rec = records["bad_import"]

    # Should have violations due to static scan catching 'from shu'
    assert rec.violations and any("shu" in v for v in rec.violations), rec.violations

    # Loading should fail with ImportError
    try:
        loader.load(rec)
        raise AssertionError("Expected ImportError when loading plugin with disallowed imports")
    except ImportError:
        pass


async def test_runtime_import_hook_blocks_dynamic_src_shu(client, db, auth_headers):
    from shu.plugins.executor import EXECUTOR
    from shu.plugins.loader import PluginLoader

    loader = PluginLoader()
    records = loader.discover()
    assert "runtime_bad_import" in records, f"runtime_bad_import plugin not discovered; found={list(records.keys())}"

    plugin = loader.load(records["runtime_bad_import"])  # No static violation; dynamic import during execute

    result = await EXECUTOR.execute(plugin=plugin, user_id="u1", user_email=None, agent_key=None, params={})
    assert result.status == "error", result
    assert result.error and "denied" in (result.error.get("message") or ""), result.error


class ImportGuardsSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_loader_static_scan_blocks_shu_import,
            test_runtime_import_hook_blocks_dynamic_src_shu,
        ]

    def get_suite_name(self) -> str:
        return "Import Guards"

    def get_suite_description(self) -> str:
        return "Validates static scan and runtime deny-hook for disallowed imports in plugins"


if __name__ == "__main__":
    create_test_runner_script(ImportGuardsSuite, globals())
