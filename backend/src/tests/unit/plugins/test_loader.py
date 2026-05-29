"""Unit tests for PluginLoader._static_scan_for_violations and discover.

Verifies the AST-based import guard correctly blocks disallowed modules
(requests, httpx, urllib3, urllib, importlib, shu.*) while allowing
shu_plugin_sdk and explicitly allowlisted modules like urllib.parse.

Also covers the manifest-name reservation guards added for MCP and the
internal-tool framework (SHU-816): the ``mcp-`` prefix, the exact name
``int``, and any name containing ``:``. The ``int-`` prefix is NOT
reserved — see ``test_discover_allows_int_hyphen_names``.
"""

from __future__ import annotations

import types
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
        "from urllib.parse import quote\n",
        "import urllib.parse\n",
    ],
    ids=[
        "from-shu-plugin-sdk",
        "import-shu-plugin-sdk",
        "from-shu-plugin-sdk-submodule",
        "from-urllib-parse-import",
        "import-urllib-parse",
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
        ("import urllib\n", "urllib"),
        ("from urllib.request import urlopen\n", "urllib"),
        ("from urllib import request\n", "urllib"),
        ("from urllib import *\n", "urllib"),
        ('__import__("urllib", fromlist=["request"])\n', "urllib"),
        ("import importlib\n", "importlib"),
        ("from importlib import import_module\n", "importlib"),
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
        "import-urllib",
        "from-urllib-request-import",
        "from-urllib-import-request",
        "from-urllib-star",
        "dunder-import-urllib-fromlist",
        "import-importlib",
        "from-importlib-import",
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
        ("from urllib import request\n((\n", "urllib"),
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


def test_fallback_regex_false_positive_urllib_parse(
    loader: PluginLoader,
    plugin_dir: Path,
) -> None:
    """Regex fallback flags urllib.parse in broken files (known false positive).

    The fallback regex does not consult ALLOWED_MODULES, so ``urllib.parse``
    triggers a violation in syntax-error files.  This is harmless because
    broken files cannot execute.
    """
    _write_plugin_file(plugin_dir, "from urllib.parse import quote\n((\n")
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 1
    assert "urllib" in violations[0]


def test_fallback_regex_catches_dunder_import_in_bad_syntax(
    loader: PluginLoader,
    plugin_dir: Path,
) -> None:
    """Regex fallback catches __import__ in syntax-error files."""
    _write_plugin_file(plugin_dir, "__import__('shu')\n((\n")
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 1
    assert "__import__" in violations[0]


def test_fallback_regex_catches_dunder_import_with_fromlist_in_bad_syntax(
    loader: PluginLoader,
    plugin_dir: Path,
) -> None:
    """Regex fallback catches __import__ with fromlist in syntax-error files."""
    _write_plugin_file(
        plugin_dir, '__import__("urllib", fromlist=["request"])\n((\n'
    )
    violations = loader._static_scan_for_violations(plugin_dir)
    assert len(violations) == 1
    assert "__import__" in violations[0]


def test_blocks_dunder_import(loader: PluginLoader, plugin_dir: Path) -> None:
    """__import__('requests') is caught without any import statement."""
    _assert_single_violation(loader, plugin_dir, "__import__('requests')\n", "requests")


# ----------------------------------------------------------------------
# Manifest-name reservation guards: mcp-, int, names containing ':' (SHU-816)
# ----------------------------------------------------------------------


def _make_discovery_loader(tmp_path: Path, manifests: dict[str, dict], monkeypatch) -> PluginLoader:
    """Build a PluginLoader pointed at tmp_path with fake manifest dirs.

    ``manifests`` maps a directory name to the ``PLUGIN_MANIFEST`` dict
    that the loader should see when it imports that plugin's manifest.
    """
    # Create the plugin directories + empty manifest.py files. The actual
    # import is patched below; the files just need to exist for
    # `(child / "manifest.py").exists()` to return True.
    for dirname in manifests:
        plugin_dir = tmp_path / dirname
        plugin_dir.mkdir()
        (plugin_dir / "manifest.py").write_text("")

    def fake_import_module(spec_name: str):
        # spec_name is "plugins.<dirname>.manifest"
        dirname = spec_name.split(".")[1]
        module = types.ModuleType(spec_name)
        module.PLUGIN_MANIFEST = manifests[dirname]
        return module

    monkeypatch.setattr("shu.plugins.loader.importlib.import_module", fake_import_module)
    return PluginLoader(plugins_dir=tmp_path)


def test_discover_skips_mcp_prefixed_plugins(tmp_path: Path, monkeypatch, caplog) -> None:
    """The mcp- prefix is reserved for MCP-derived plugins (pre-existing guard)."""
    loader = _make_discovery_loader(
        tmp_path,
        {
            "mcpfoo_dir": {"name": "mcp-foo", "module": "x"},
            "normal_dir": {"name": "normal-plugin", "module": "x"},
        },
        monkeypatch,
    )

    import logging

    with caplog.at_level(logging.WARNING):
        records = loader.discover()

    assert "normal-plugin" in records
    assert "mcp-foo" not in records
    assert any("'mcp-' prefix is reserved" in r.message for r in caplog.records)


def test_discover_skips_any_plugin_name_containing_colon(tmp_path: Path, monkeypatch, caplog) -> None:
    """Plugin names with `:` are rejected — they break tool-call wire format (SHU-816)."""
    loader = _make_discovery_loader(
        tmp_path,
        {
            "intbar_dir": {"name": "int:bar", "module": "x"},
            "mcpfoo_dir2": {"name": "mcp:server", "module": "x"},
            "weird_dir": {"name": "my:thing", "module": "x"},
            "normal_dir": {"name": "normal-plugin", "module": "x"},
        },
        monkeypatch,
    )

    import logging

    with caplog.at_level(logging.WARNING):
        records = loader.discover()

    assert "normal-plugin" in records
    # All three colon-containing names are skipped — the rule is "any colon
    # anywhere", not specifically prefix-based.
    assert "int:bar" not in records
    assert "mcp:server" not in records
    assert "my:thing" not in records
    assert sum(1 for r in caplog.records if "must not contain ':'" in r.message) >= 3


def test_discover_allows_int_hyphen_names(tmp_path: Path, monkeypatch) -> None:
    """`int-foo` is NOT reserved — only the exact `int` name + colon-containing names are rejected (SHU-816)."""
    loader = _make_discovery_loader(
        tmp_path,
        {"intfoo_dir": {"name": "int-foo", "module": "x"}},
        monkeypatch,
    )

    records = loader.discover()
    assert "int-foo" in records


def test_discover_skips_exact_int_plugin_name(tmp_path: Path, monkeypatch, caplog) -> None:
    """Exact name `int` collides with the InternalToolRouter wire namespace (SHU-816)."""
    loader = _make_discovery_loader(
        tmp_path,
        {
            "int_dir": {"name": "int", "module": "x"},
            "normal_dir": {"name": "normal-plugin", "module": "x"},
        },
        monkeypatch,
    )

    import logging

    with caplog.at_level(logging.WARNING):
        records = loader.discover()

    assert "normal-plugin" in records
    # The bare `int` name is reserved — it's the virtual plugin all
    # internal tools dispatch through.
    assert "int" not in records
    assert any("'int' name is reserved" in r.message for r in caplog.records)


def test_discover_allows_names_that_only_contain_int_substring(tmp_path: Path, monkeypatch) -> None:
    """Reservation is prefix-based — a plugin whose name contains 'int' elsewhere is fine."""
    loader = _make_discovery_loader(
        tmp_path,
        {
            "midint_dir": {"name": "print-helper", "module": "x"},
            "endint_dir": {"name": "data-int", "module": "x"},
            "underint_dir": {"name": "my_int_tool", "module": "x"},
        },
        monkeypatch,
    )

    records = loader.discover()

    assert "print-helper" in records
    assert "data-int" in records
    assert "my_int_tool" in records
