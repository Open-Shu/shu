"""Plugins loader: discovers local plugins under plugins/* directories with a manifest.
- Each plugin folder should provide a manifest.py with PLUGIN_MANIFEST dict:
  {"name": str, "version": str, "module": "plugins.pkg.plugin:PluginClass"}.
"""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.config import get_settings_instance
from .base import Plugin

logger = logging.getLogger(__name__)


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

    def _static_scan_for_violations(self, plugin_dir: Path) -> list[str]:
        violations: list[str] = []
        # Deny direct HTTP clients and host-internal imports from plugins
        deny = (
            "import requests",
            "import httpx",
            "from httpx",
            "import urllib3",
            "urllib.request",
            # Block host-internal imports from plugins
            "import shu",
            "from shu",
        )
        try:
            for p in plugin_dir.rglob("*.py"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except Exception as e:
                    logger.error("Could not read text: %s", e)
                    continue
                for d in deny:
                    if d in txt:
                        violations.append(f"{p.name}: {d}")
        except Exception:
            pass
        return violations

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
        # Validate op requirement: schema must declare properties.op.enum with at least one value
        try:
            in_schema = None
            if hasattr(plugin, "get_schema"):
                in_schema = plugin.get_schema()
            props = (in_schema or {}).get("properties") if isinstance(in_schema, dict) else None
            op_def = (props or {}).get("op") if isinstance(props, dict) else None
            enum_vals = (op_def or {}).get("enum") if isinstance(op_def, dict) else None
            if not (isinstance(enum_vals, (list, tuple)) and len(enum_vals) >= 1):
                raise ImportError(f"Plugin '{record.name}' missing op enum in input schema")
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
