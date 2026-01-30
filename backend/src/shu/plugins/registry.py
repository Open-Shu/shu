"""Plugins registry: resolve enabled plugins from DB and load the plugin instance.
Caches loaded plugins in-process.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.plugin_registry import PluginDefinition  # v0 registry model reused for v1 enablement checks
from .base import Plugin
from .loader import PluginLoader, PluginRecord

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self):
        self._loader = PluginLoader()
        self._manifest: dict[str, PluginRecord] = {}
        self._cache: dict[str, Plugin] = {}

    def refresh(self) -> None:
        self._manifest = self._loader.discover()
        self._cache.clear()

    def get_manifest(self, refresh_if_empty: bool = True) -> dict[str, PluginRecord]:
        """Return current manifest; optionally refresh if empty.
        Do not raise on errors; return empty dict on failures.
        """
        try:
            if refresh_if_empty and not self._manifest:
                self.refresh()
            return dict(self._manifest or {})
        except Exception:
            return {}

    async def sync(self, session: AsyncSession) -> dict:
        """Auto-register discovered plugins into PluginDefinition.
        - Creates a row if missing (enabled=False by default)
        - Updates input_schema/output_schema if provided by plugin
        - Purges DB rows for plugins no longer present on disk
        - Does not flip enabled state automatically
        """
        created = 0
        updated = 0
        purged = 0
        if not self._manifest:
            self.refresh()
        discovered_names = set(self._manifest.keys())
        # Upsert discovered plugins
        for name, record in self._manifest.items():
            res = await session.execute(select(PluginDefinition).where(PluginDefinition.name == name))
            row = res.scalars().first()
            if not row:
                # load plugin to fetch schema
                try:
                    plugin = self._loader.load(record)
                except Exception as e:
                    logger.warning("Skipping plugin '%s' during sync: %s", name, e)
                    continue
                row = PluginDefinition(name=name, version=getattr(record, "version", "1"), enabled=False)
                try:
                    in_schema = None
                    out_schema = None
                    try:
                        in_schema = plugin.get_schema()
                    except Exception:
                        in_schema = None
                    try:
                        get_out = getattr(plugin, "get_output_schema", None)
                        out_schema = get_out() if callable(get_out) else None
                    except Exception:
                        out_schema = None
                    if in_schema:
                        row.input_schema = in_schema
                    if out_schema:
                        row.output_schema = out_schema
                finally:
                    session.add(row)
                    await session.commit()
                    created += 1
            else:
                # update schema if available
                try:
                    plugin = self._loader.load(record)
                    in_schema = None
                    out_schema = None
                    try:
                        in_schema = plugin.get_schema()
                    except Exception:
                        in_schema = None
                    try:
                        get_out = getattr(plugin, "get_output_schema", None)
                        out_schema = get_out() if callable(get_out) else None
                    except Exception:
                        out_schema = None
                    changed = False
                    if in_schema and row.input_schema != in_schema:
                        row.input_schema = in_schema
                        changed = True
                    if out_schema and row.output_schema != out_schema:
                        row.output_schema = out_schema
                        changed = True
                    if changed:
                        await session.commit()
                        updated += 1
                except Exception:
                    # non-fatal, continue
                    pass
        # Purge DB rows not present on disk anymore
        try:
            res = await session.execute(select(PluginDefinition))
            all_rows = res.scalars().all()
            for r in all_rows:
                if r.name not in discovered_names:
                    try:
                        await session.delete(r)
                        await session.commit()
                        purged += 1
                    except Exception:
                        # best-effort purge
                        await session.rollback()
        except Exception:
            pass
        return {
            "created": created,
            "updated": updated,
            "purged": purged,
            "discovered": len(self._manifest),
        }

    async def resolve(self, name: str, session: AsyncSession) -> Plugin | None:
        # If cached, verify enablement from DB before returning to honor runtime toggles
        if name in self._cache:
            try:
                res = await session.execute(select(PluginDefinition.enabled).where(PluginDefinition.name == name))
                enabled = bool(res.scalar() or False)
                if not enabled:
                    # Evict stale cache entry when disabled
                    self._cache.pop(name, None)
                    logger.info("Plugin '%s' evicted from cache due to disable toggle", name)
                    return None
            except Exception:
                # On DB error, fall through to normal resolution path which re-checks enablement
                self._cache.pop(name, None)
            else:
                return self._cache[name]
        if not self._manifest:
            self.refresh()
        record = self._manifest.get(name)
        if not record:
            logger.warning("Plugin '%s' not found in plugin manifest(s)", name)
            return None
        # Check DB enablement (by name; version can be matched later if needed)
        res = await session.execute(select(PluginDefinition).where(PluginDefinition.name == name))
        row = res.scalars().first()
        if not row or not row.enabled:
            logger.warning("Plugin '%s' is disabled or not registered in DB", name)
            return None
        plugin = self._loader.load(record)
        self._cache[name] = plugin
        return plugin


REGISTRY = PluginRegistry()
