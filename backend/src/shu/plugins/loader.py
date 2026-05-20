"""Plugins loader: discovers local plugins under plugins/* directories with a manifest.
- Each plugin folder should provide a manifest.py with PLUGIN_MANIFEST dict:
  {"name": str, "version": str, "module": "plugins.pkg.plugin:PluginClass"}.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from shu.core.logging import get_logger

from ..core.config import get_settings_instance
from .base import Plugin
from .schema import validate_legacy_schema, validate_per_op_schemas

logger = get_logger(__name__)


@dataclass
class PluginRecord:
    name: str
    version: str
    entry: str  # dotted path "package.module:Class"
    capabilities: list[str] | None = None
    required_identities: list[dict] | None = None
    # Per-op auth specification (capability-driven auth declaration)
    op_auth: dict | None = None
    # Human-friendly display title (optional)
    display_name: str | None = None
    # Feeds metadata (optional)
    default_feed_op: str | None = None
    allowed_feed_ops: list[str] | None = None
    chat_callable_ops: list[str] | None = None
    plugin_dir: Path | None = None
    violations: list[str] | None = None


class PluginLoader:
    def __init__(self, *, plugins_dir: Path | None = None) -> None:
        # ``plugins_dir`` is the direct path to the directory containing plugin
        # sub-packages (each with a manifest.py).  When not supplied we derive
        # it from ``settings.plugins_root`` which points to the *parent*
        # directory; we always append ``plugins/`` ourselves so the directory
        # name is guaranteed to match the ``plugins.`` prefix in manifests.
        if plugins_dir is None:
            plugins_dir = self._resolve_plugins_dir()
        self.plugins_dir = plugins_dir
        # Add plugins_dir's parent to sys.path so ``import plugins.*`` works.
        plugins_parent = str(self.plugins_dir.parent)
        if plugins_parent not in sys.path:
            sys.path.insert(0, plugins_parent)
        logger.info("Plugins loader using plugins_dir=%s", self.plugins_dir)

    @staticmethod
    def _resolve_plugins_dir() -> Path:
        """Derive the ``plugins/`` directory from settings or repo layout."""
        # Resolve repo root from this file's location.
        # Layout: <repo>/backend/src/shu/plugins/loader.py  (local dev)
        #         /app/src/shu/plugins/loader.py             (container)
        loader_path = Path(__file__).resolve()
        src_dir = loader_path.parents[2]
        candidate_parent = src_dir.parent
        repo_root = candidate_parent.parent if candidate_parent.name == "backend" else candidate_parent

        try:
            settings = get_settings_instance()
            configured = Path(settings.plugins_root)
            # settings.plugins_root is already resolved to an absolute path by
            # the field validator in config.py.  If it's still relative (e.g.
            # settings failed to resolve), resolve against repo_root.
            if not configured.is_absolute():
                configured = (repo_root / configured).resolve()
            return configured / "plugins"
        except Exception:
            return repo_root / "plugins"

    # Single source of truth for modules that plugins must not import.
    # urllib is broadly blocked; urllib.parse is explicitly allowed (safe
    # string manipulation needed for URL encoding).
    DISALLOWED_MODULES: tuple[str, ...] = (
        "requests",
        "httpx",
        "urllib3",
        "urllib",
        "importlib",
        # Host-internal imports are blocked; shu_plugin_sdk remains allowed.
        "shu",
    )
    ALLOWED_MODULES: tuple[str, ...] = ("urllib.parse",)

    def _static_scan_for_violations(self, plugin_dir: Path) -> list[str]:
        violations: set[str] = set()
        disallowed_modules = self.DISALLOWED_MODULES
        allowed_modules = self.ALLOWED_MODULES

        def disallowed_import(module: str) -> bool:
            """Return True when ``module`` is on the plugin deny-list.

            Modules matching an ``ALLOWED_MODULES`` entry (or children of
            one) are never flagged, even when their parent is denied.
            """
            if any(module == a or module.startswith(f"{a}.") for a in allowed_modules):
                return False
            return any(module == name or module.startswith(f"{name}.") for name in disallowed_modules)

        # Auto-generated regex fallback for files that fail to parse as AST.
        # This is purely informational — files with syntax errors cannot
        # execute, so violations here are not safety-critical.  The regex
        # does not consult ALLOWED_MODULES, so it may produce false
        # positives (e.g. flagging ``from urllib.parse``).
        fallback_patterns: list[tuple[str, str]] = []
        for mod in disallowed_modules:
            escaped = re.escape(mod)
            fallback_patterns.append((rf"\bimport\s+{escaped}(\b|\.)", f"import {mod}"))
            fallback_patterns.append((rf"\bfrom\s+{escaped}(\b|\.)", f"from {mod}"))
        fallback_patterns.append((r"\b__import__\s*\(", "__import__"))

        def scan_ast_imports(tree: ast.AST, filename: str) -> None:
            """Collect disallowed import violations from a parsed AST."""
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                        if disallowed_import(module):
                            violations.add(f"{filename}: import {module}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if not module:
                        continue
                    if disallowed_import(module):
                        violations.add(f"{filename}: from {module} import ...")
                        continue

                    # Catch "from urllib import request" by checking
                    # imported names against disallowed submodules.
                    for alias in node.names:
                        imported = f"{module}.{alias.name}"
                        if disallowed_import(imported):
                            violations.add(f"{filename}: from {module} import {alias.name}")
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    mod = node.args[0].value
                    if disallowed_import(mod):
                        violations.add(f"{filename}: __import__('{mod}')")

        for p in plugin_dir.rglob("*.py"):
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.error("Could not read text: %s", e)
                continue

            try:
                tree = ast.parse(txt)
            except SyntaxError:
                tree = None

            if tree is None:
                for pattern, label in fallback_patterns:
                    if re.search(pattern, txt):
                        violations.add(f"{p.name}: {label}")
                continue

            try:
                scan_ast_imports(tree, p.name)
            except Exception as e:
                logger.exception("Unexpected error scanning %s: %s", p.name, e)
                violations.add(f"{p.name}: scan error")
        return sorted(violations)

    def discover(self) -> dict[str, PluginRecord]:
        records: dict[str, PluginRecord] = {}
        if not self.plugins_dir.exists():
            return records
        for child in self.plugins_dir.iterdir():
            if not child.is_dir():
                continue
            manifest_py = child / "manifest.py"
            if not manifest_py.exists():
                continue
            try:
                spec_name = f"plugins.{child.name}.manifest"
                manifest = importlib.import_module(spec_name)
                m = getattr(manifest, "PLUGIN_MANIFEST", None)
                if not m:
                    continue
                name = m.get("name")
                version = m.get("version", "0")
                entry = m.get("module")
                capabilities = m.get("capabilities", []) or []
                required_identities = m.get("required_identities", []) or []
                op_auth = m.get("op_auth") or None
                display_name = m.get("display_name") or m.get("title")
                default_feed_op = m.get("default_feed_op")
                allowed_feed_ops = m.get("allowed_feed_ops") or []
                chat_callable_ops = m.get("chat_callable_ops") or []
                if not (name and entry):
                    continue
                if name.startswith("mcp-"):
                    logger.warning("Skipping plugin '%s': 'mcp-' prefix is reserved", name)
                    continue
                rec = PluginRecord(
                    name=name,
                    version=version,
                    entry=entry,
                    capabilities=capabilities,
                    required_identities=required_identities,
                    op_auth=dict(op_auth) if isinstance(op_auth, dict) else None,
                    display_name=display_name,
                    default_feed_op=default_feed_op,
                    allowed_feed_ops=list(allowed_feed_ops) if isinstance(allowed_feed_ops, (list, tuple)) else None,
                    chat_callable_ops=list(chat_callable_ops) if isinstance(chat_callable_ops, (list, tuple)) else None,
                    plugin_dir=child,
                )
                rec.violations = self._static_scan_for_violations(child)
                records[name] = rec
            except Exception as e:
                logger.exception("Failed loading manifest for %s: %s", child.name, e)
        return records

    def load(self, record: PluginRecord) -> Plugin:
        # Enforce basic static violations (HTTP clients) at load time
        if record.violations:
            raise ImportError(f"Plugin '{record.name}' uses disallowed imports: {record.violations}")
        module_path, class_name = record.entry.split(":", 1)
        # Ensure fresh import so uploads/updates are reflected during sync
        try:
            if module_path in sys.modules:
                mod = importlib.reload(sys.modules[module_path])
            else:
                mod = importlib.import_module(module_path)
        except Exception:
            # Fallback to standard import
            mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        plugin = cls()  # type: ignore[call-arg]
        # Validate op requirement: plugin must produce schemas for its declared ops.
        try:
            declared_ops = list(record.chat_callable_ops or []) + list(record.allowed_feed_ops or [])
            if callable(getattr(plugin, "get_schema_for_op", None)):
                validate_per_op_schemas(plugin, declared_ops)
            else:
                validate_legacy_schema(plugin)
        except ImportError:
            raise
        except Exception as e:
            raise ImportError(f"Plugin '{record.name}' schema validation failed: {e}")
        # Attach manifest-derived metadata for executor
        try:
            plugin._capabilities = list(record.capabilities or [])
        except Exception:
            pass
        try:
            plugin._op_auth = dict(record.op_auth or {})
        except Exception:
            pass
        # sanity check
        if getattr(plugin, "name", None) != record.name:
            logger.warning(
                "Plugin name mismatch: manifest=%s class=%s",
                record.name,
                getattr(plugin, "name", None),
            )
        return plugin
