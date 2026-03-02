"""Unit tests for PluginLoader._static_scan_for_violations.

Verifies the AST-based import guard correctly blocks disallowed modules
(requests, httpx, urllib3, urllib.request, shu.*) while allowing
shu_plugin_sdk imports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shu.plugins.loader import PluginLoader


@pytest.fixture
def loader() -> PluginLoader:
    """Return a PluginLoader with a dummy plugins_dir (not used by scan)."""
    return PluginLoader(plugins_dir=Path("/tmp/unused"))


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Return a temporary directory acting as a plugin package."""
    return tmp_path


def _write_plugin_file(plugin_dir: Path, code: str, filename: str = "plugin.py") -> None:
    """Write a Python file into the plugin directory."""
    (plugin_dir / filename).write_text(code, encoding="utf-8")


def _assert_single_violation(
    loader: PluginLoader,
    plugin_dir: Path,
    code: str,
    needle: str,
    *,
    filename: str = "plugin.py",
) -> None:
    """Write ``code`` and assert exactly one violation containing ``needle``."""
    _write_plugin_file(plugin_dir, code, filename=filename)
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 1, f"Should block '{needle}' in '{code}'"
    assert needle in violations[0]


@pytest.mark.parametrize(
    "code",
    [
        "from shu_plugin_sdk import PluginResult\n",
        "import shu_plugin_sdk\n",
        "from shu_plugin_sdk.testing import patch_retry_sleep\n",
    ],
    ids=[
        "from-shu-plugin-sdk",
        "import-shu-plugin-sdk",
        "from-shu-plugin-sdk-submodule",
    ],
)
def test_allows_shu_plugin_sdk_imports(
    loader: PluginLoader,
    plugin_dir: Path,
    code: str,
) -> None:
    """shu_plugin_sdk imports must not be flagged."""
    _write_plugin_file(plugin_dir, code)
    assert loader._static_scan_for_violations(plugin_dir) == []


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("import shu\n", "shu"),
        ("from shu import core\n", "shu"),
        ("from shu.core import config\n", "shu"),
        ("from shu.plugins.loader import PluginLoader\n", "shu"),
        ("import shu.core\n", "shu"),
        ("import requests\n", "requests"),
        ("from httpx import AsyncClient\n", "httpx"),
        ("import urllib3\n", "urllib3"),
        ("from urllib.request import urlopen\n", "urllib.request"),
        ("from urllib import request\n", "from urllib import request"),
    ],
    ids=[
        "import-shu",
        "from-shu-import",
        "from-shu-submodule",
        "from-shu-loader",
        "import-shu-submodule",
        "import-requests",
        "from-httpx-import",
        "import-urllib3",
        "from-urllib-request-import",
        "from-urllib-import-request",
    ],
)
def test_blocks_disallowed_imports(
    loader: PluginLoader,
    plugin_dir: Path,
    code: str,
    needle: str,
) -> None:
    """Disallowed imports are detected via AST scan."""
    _assert_single_violation(loader, plugin_dir, code, needle)


def test_clean_plugin_no_violations(loader: PluginLoader, plugin_dir: Path) -> None:
    """A plugin using only allowed imports produces no violations."""
    _write_plugin_file(
        plugin_dir,
        "from shu_plugin_sdk import PluginResult\nimport json\nimport os\n",
    )
    assert loader._static_scan_for_violations(plugin_dir) == []


def test_multiple_violations_in_one_file(loader: PluginLoader, plugin_dir: Path) -> None:
    """Multiple disallowed imports in the same file all get reported."""
    _write_plugin_file(
        plugin_dir,
        "import requests\nimport httpx\nfrom shu.core import config\nfrom shu_plugin_sdk import PluginResult\n",
    )
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 3


def test_violations_across_multiple_files(loader: PluginLoader, plugin_dir: Path) -> None:
    """Violations in different files are all reported."""
    _assert_single_violation(
        loader,
        plugin_dir,
        "import requests\n",
        "requests",
        filename="a.py",
    )
    _write_plugin_file(plugin_dir, "import shu\n", filename="b.py")
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 2


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("import shu\n((\n", "shu"),
        ("from shu.core import config\n((\n", "shu"),
        ("from urllib import request\n((\n", "from urllib import request"),
    ],
    ids=[
        "fallback-import-shu",
        "fallback-from-shu-core",
        "fallback-from-urllib-import-request",
    ],
)
def test_fallback_regex_catches_disallowed_imports(
    loader: PluginLoader,
    plugin_dir: Path,
    code: str,
    needle: str,
) -> None:
    """Files with syntax errors still get checked via regex fallback."""
    _assert_single_violation(loader, plugin_dir, code, needle)


def test_fallback_regex_allows_shu_plugin_sdk_in_bad_syntax(
    loader: PluginLoader,
    plugin_dir: Path,
) -> None:
    """Regex fallback must not flag shu_plugin_sdk in unparseable files."""
    _write_plugin_file(plugin_dir, "from shu_plugin_sdk import X\n((\n")
    violations = loader._static_scan_for_violations(plugin_dir)
    assert violations == []
