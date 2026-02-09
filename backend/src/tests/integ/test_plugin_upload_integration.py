"""
Integration tests for Plugin Package Upload & Installation.
"""

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data
from shu.core.config import Settings, get_settings_instance


def _build_sample_plugin_zip() -> bytes:
    """Create an in-memory .zip with a minimal plugin.
    Structure:
      my_test_plugin/
        manifest.py
        plugin.py
    """
    manifest_py = (
        "PLUGIN_MANIFEST = {\n"
        "    'name': 'my_test_plugin',\n"
        "    'version': '0.0.1',\n"
        "    'module': 'plugins.my_test_plugin.plugin:Plugin',\n"
        "}\n"
    )
    plugin_py = (
        "from typing import Any, Dict\n"
        "class Plugin:\n"
        "    name = 'my_test_plugin'\n"
        "    version = '0.0.1'\n"
        "    def get_schema(self):\n"
        "        return {'type': 'object', 'properties': {'op': {'type': 'string', 'enum': ['echo']}}}\n"
        "    def get_output_schema(self):\n"
        "        return {'type': 'object'}\n"
        "    async def execute(self, params: Dict[str, Any], context, host: Any):\n"
        "        op = (params or {}).get('op', 'echo')\n"
        "        if op == 'echo':\n"
        "            return {'status': 'success', 'data': {'echo': params, 'user_id': getattr(context, 'user_id', None)}}\n"
        "        return {'status': 'error', 'error': {'code': 'unsupported_op', 'message': 'unsupported_op', 'details': {'op': op}}}\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("my_test_plugin/manifest.py", manifest_py)
        zf.writestr("my_test_plugin/plugin.py", plugin_py)
    return buf.getvalue()


# ---- Tests ----
async def test_upload_and_execute_plugin(client, db, auth_headers):
    # Build zip
    content = _build_sample_plugin_zip()
    # Post multipart upload
    files = {
        "file": ("my_test_plugin.zip", content, "application/zip"),
        "force": (None, "true"),
    }
    resp = await client.post("/api/v1/plugins/upload", files=files, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data["plugin_name"] == "my_test_plugin"

    # The plugin should be installed under the configured plugins_root
    installed_path = Path(data["installed_path"])
    assert installed_path.name == "my_test_plugin"

    settings = get_settings_instance()
    plugins_root = Path(settings.plugins_root)
    if not plugins_root.is_absolute():
        repo_root = Settings._repo_root_from_this_file()
        plugins_root = (repo_root / plugins_root).resolve()

    assert installed_path.parent == plugins_root

    # Sync registry to DB
    resp2 = await client.post("/api/v1/plugins/admin/sync", headers=auth_headers)
    assert resp2.status_code == 200

    # List tools includes our plugin
    resp3 = await client.get("/api/v1/plugins", headers=auth_headers)
    assert resp3.status_code == 200
    tools = extract_data(resp3)
    names = [t["name"] for t in tools]
    assert "my_test_plugin" in names

    # Enable plugin
    resp4 = await client.patch(
        "/api/v1/plugins/admin/my_test_plugin/enable", json={"enabled": True}, headers=auth_headers
    )
    assert resp4.status_code == 200

    # Execute echo op
    resp5 = await client.post(
        "/api/v1/plugins/my_test_plugin/execute",
        json={"params": {"op": "echo", "foo": "bar"}},
        headers=auth_headers,
    )
    assert resp5.status_code == 200, resp5.text
    result = extract_data(resp5)
    assert result["status"] == "success"
    assert result["data"]["echo"]["foo"] == "bar"


class PluginUploadTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> list[Callable]:
        return [
            test_upload_and_execute_plugin,
        ]

    def get_suite_name(self) -> str:
        return "Plugin Upload & Install Integration Tests"

    def get_suite_description(self) -> str:
        return "Admin can upload a plugin package, install it, enable, and execute it."


if __name__ == "__main__":
    suite = PluginUploadTestSuite()
    raise SystemExit(suite.run())
