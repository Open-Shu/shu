"""
Plugins loader: discovers local plugins under plugins/* directories with a manifest.
- Each plugin folder should provide a manifest.py with PLUGIN_MANIFEST dict:
  {"name": str, "version": str, "module": "plugins.pkg.plugin:PluginClass"}
"""
from __future__ import annotations
import importlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List

from .base import Plugin
from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)


@dataclass
class PluginRecord:
    name: str
    version: str
    entry: str  # dotted path "package.module:Class"
    capabilities: Optional[List[str]] = None
    required_identities: Optional[List[dict]] = None
    # Per-op auth specification (capability-driven auth declaration)
    op_auth: Optional[dict] = None
    # Human-friendly display title (optional)
    display_name: Optional[str] = None
    # Feeds metadata (optional)
    default_feed_op: Optional[str] = None
    allowed_feed_ops: Optional[List[str]] = None
    chat_callable_ops: Optional[List[str]] = None
    plugin_dir: Optional[Path] = None
    violations: Optional[List[str]] = None


class PluginLoader:
    def __init__(self, *, plugins_dir: Optional[Path] = None):
        # Resolve repo root from this file. Our layout is: <repo>/backend/src/shu/plugins/loader.py
        # So parents are: [0]=plugins, [1]=shu, [2]=src, [3]=backend, [4]=<repo>
        loader_path = Path(__file__).resolve()
        # Typical layouts:
        #  - Local dev: <repo>/backend/src/shu/plugins/loader.py
        #    -> parents[2] = <repo>/backend/src; candidate_parent = <repo>/backend
        #    -> repo_root = <repo>
        #  - In container: /app/src/shu/plugins/loader.py
        #    -> parents[2] = /app/src; candidate_parent = /app
        #    -> repo_root = /app
        src_dir = loader_path.parents[2]
        candidate_parent = src_dir.parent
        repo_root = candidate_parent.parent if candidate_parent.name == "backend" else candidate_parent
        # Prefer settings.PLUGINS_ROOT if provided
        if plugins_dir is None:
            try:
                settings = get_settings_instance()
                configured = Path(settings.plugins_root)
                # Resolve relative paths against repo root
                if not configured.is_absolute():
                    configured = (repo_root / configured).resolve()
                plugins_dir = configured
            except Exception:
                plugins_dir = None
        self.plugins_dir = plugins_dir or (repo_root / "plugins")
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        logger.info("Plugins loader using plugins_dir=%s", self.plugins_dir)

    def _static_scan_for_violations(self, plugin_dir: Path) -> List[str]:
        violations: List[str] = []
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
                except Exception:
                    continue
                for d in deny:
                    if d in txt:
                        violations.append(f"{p.name}: {d}")
        except Exception:
            pass
        return violations

    def discover(self) -> Dict[str, PluginRecord]:
        records: Dict[str, PluginRecord] = {}
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
                    name=name, version=version, entry=entry,
                    capabilities=capabilities,
                    required_identities=required_identities,
                    op_auth=dict(op_auth) if isinstance(op_auth, dict) else None,
                    display_name=display_name,
                    default_feed_op=default_feed_op,
                    allowed_feed_ops=list(allowed_feed_ops) if isinstance(allowed_feed_ops, (list, tuple)) else None,
                    chat_callable_ops=list(chat_callable_ops) if isinstance(chat_callable_ops, (list, tuple)) else None,
                    plugin_dir=child
                )
                rec.violations = self._static_scan_for_violations(child)
                records[name] = rec
            except Exception as e:  # noqa: BLE001
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
            setattr(plugin, "_capabilities", list(record.capabilities or []))
        except Exception:
            pass
        try:
            setattr(plugin, "_op_auth", dict(record.op_auth or {}))
        except Exception:
            pass
        # sanity check
        if getattr(plugin, "name", None) != record.name:
            logger.warning("Plugin name mismatch: manifest=%s class=%s", record.name, getattr(plugin, "name", None))
        return plugin
