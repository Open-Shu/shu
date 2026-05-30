"""Plugins registry: resolve enabled plugins from DB and load the plugin instance.
Caches loaded plugins in-process.

TODO(multi-tenant MCP): ``_manifest`` and ``_cache`` are keyed by plugin
name with no tenant key. Cached ``mcp:*`` adapters hold tenant-specific
URL + auth headers, so enabling MT MCP requires re-keying both to
``(tenant_id, plugin_name)`` for ``mcp:*`` entries. Safe today because
``mcp_service._reject_in_multi_tenant`` blocks all MCP writes in MT.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.plugin_registry import PluginDefinition  # v0 registry model reused for v1 enablement checks
from .base import Plugin
from .loader import PluginLoader, PluginRecord

logger = get_logger(__name__)


class PluginRegistry:
    def __init__(self) -> None:
        self._loader = PluginLoader()
        self._manifest: dict[str, PluginRecord] = {}
        self._cache: dict[str, Plugin] = {}

    def refresh(self) -> None:
        self._manifest = self._loader.discover()
        self._cache.clear()

    async def full_refresh(self, session: AsyncSession) -> None:
        """Async refresh: discover filesystem plugins and MCP connections."""
        self.refresh()
        await self._refresh_mcp(session)

    async def _refresh_mcp(self, session: AsyncSession) -> None:
        """Load MCP plugin records from the database and merge into the manifest."""
        from ..services.mcp_service import McpService

        try:
            service = McpService(session)
            records = await service.generate_all_plugin_records()
        except Exception:
            # Roll back so a failed MCP query doesn't leave the shared session in
            # an aborted transaction — otherwise every subsequent statement in
            # sync() fails with InFailedSQLTransactionError, masking this as the
            # real cause. exc_info=True so the actual failure lands in the log,
            # not a detail-less "Failed to refresh MCP plugin records".
            logger.warning("Failed to refresh MCP plugin records", exc_info=True)
            await session.rollback()
            return
        for record in records:
            self._manifest[record.name] = record

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

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    async def sync(self, session: AsyncSession) -> dict:  # noqa: PLR0915
        """Auto-register discovered plugins into PluginDefinition.
        - Creates a row if missing (enabled=False by default)
        - Updates output_schema if provided by plugin (input_schema is resolved live)
        - Purges DB rows for plugins no longer present on disk
        - Does not flip enabled state automatically.
        """
        created = 0
        updated = 0
        purged = 0
        await self.full_refresh(session)
        discovered_names = set(self._manifest.keys())
        # Upsert discovered plugins. Each plugin is isolated in its own try/except:
        # on failure we log the real error (with traceback) and roll back so the
        # next plugin starts on a clean transaction. Without this, one failed write
        # left the shared session aborted and every later plugin's SELECT raised
        # InFailedSQLTransactionError — surfacing a generic "current transaction is
        # aborted" while the originating error was swallowed.
        for name, record in self._manifest.items():
            try:
                res = await session.execute(select(PluginDefinition).where(PluginDefinition.name == name))
                row = res.scalars().first()
                if name.startswith("mcp:"):
                    c, u = await self._sync_mcp_definition(name, record, row, session)
                    created += c
                    updated += u
                elif not row:
                    # load plugin to fetch schema
                    try:
                        plugin = self._loader.load(record)
                    except Exception as e:
                        logger.warning("Skipping plugin '%s' during sync: %s", name, e)
                        continue
                    row = PluginDefinition(name=name, version=getattr(record, "version", "1"), enabled=False)
                    out_schema = None
                    try:
                        get_out = getattr(plugin, "get_output_schema", None)
                        out_schema = get_out() if callable(get_out) else None
                    except Exception:
                        out_schema = None
                    if out_schema:
                        row.output_schema = out_schema
                    session.add(row)
                    await session.commit()
                    created += 1
                else:
                    # update output_schema if available
                    plugin = self._loader.load(record)
                    out_schema = None
                    try:
                        get_out = getattr(plugin, "get_output_schema", None)
                        out_schema = get_out() if callable(get_out) else None
                    except Exception:
                        out_schema = None
                    if out_schema and row.output_schema != out_schema:
                        row.output_schema = out_schema
                        await session.commit()
                        updated += 1
            except Exception:
                # One plugin's failure must not poison the shared session for the
                # rest. Log the real cause and roll back so the loop continues on a
                # clean transaction instead of cascading aborted-transaction errors.
                logger.warning("Failed to sync plugin '%s'", name, exc_info=True)
                await session.rollback()
                continue
        # Purge DB rows not present on disk or MCP anymore
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
                        logger.warning("Failed to purge stale plugin '%s'", r.name, exc_info=True)
                        await session.rollback()
        except Exception:
            logger.warning("Plugin purge scan failed", exc_info=True)

        return {
            "created": created,
            "updated": updated,
            "purged": purged,
            "discovered": len(self._manifest),
        }

    async def _sync_mcp_definition(
        self, name: str, record: PluginRecord, row: PluginDefinition | None, session: AsyncSession
    ) -> tuple[int, int]:
        """Create or update a PluginDefinition row for an MCP plugin.

        Queries the McpServerConnection and uses McpPluginAdapter.get_schema()
        so schema logic lives in one place.

        Returns (created, updated) counts.
        """
        from ..services.mcp_service import McpService

        connection_name = name.removeprefix("mcp:")
        svc = McpService(session)
        schema = await svc.get_connection_schema(connection_name)

        if row:
            if schema and row.input_schema != schema:
                row.input_schema = schema
                await session.commit()
                return 0, 1
            return 0, 0

        connection_enabled = await svc.is_connection_enabled(connection_name)
        version = getattr(record, "version", "1.0")[:50]
        row = PluginDefinition(
            name=name,
            version=version,
            enabled=connection_enabled,
            input_schema=schema,
        )
        session.add(row)
        await session.commit()
        return 1, 0

    async def resolve(self, name: str, session: AsyncSession) -> Plugin | None:
        from ..services.mcp_service import McpService

        # If cached, verify enablement from DB before returning to honor runtime toggles
        if name in self._cache:
            if name.startswith("mcp:"):
                enabled = await McpService(session).is_connection_enabled(name.removeprefix("mcp:"))
            else:
                try:
                    res = await session.execute(select(PluginDefinition.enabled).where(PluginDefinition.name == name))
                    enabled = bool(res.scalar() or False)
                except Exception:
                    enabled = False
            if not enabled:
                self._cache.pop(name, None)
                logger.info("Plugin '%s' evicted from cache due to disable toggle", name)
                return None
            return self._cache[name]

        if not self._manifest:
            await self.full_refresh(session)
        record = self._manifest.get(name)
        if not record:
            logger.warning("Plugin '%s' not found in plugin manifest(s)", name)
            return None

        if name.startswith("mcp:"):
            plugin = await self._resolve_mcp(name, session)
        else:
            # Check DB enablement for native plugins
            res = await session.execute(select(PluginDefinition).where(PluginDefinition.name == name))
            row = res.scalars().first()
            if not row or not row.enabled:
                logger.warning("Plugin '%s' is disabled or not registered in DB", name)
                return None
            plugin = self._loader.load(record)

        if plugin is not None:
            self._cache[name] = plugin
        return plugin

    async def _resolve_mcp(self, name: str, session: AsyncSession) -> Plugin | None:
        """Resolve an MCP plugin by delegating adapter creation to McpService."""
        from ..services.mcp_service import McpService

        try:
            adapter = await McpService(session).resolve_adapter(name.removeprefix("mcp:"))
            if adapter is not None:
                record = self._manifest.get(name)
                if record:
                    adapter._capabilities = list(record.capabilities or [])
            return adapter
        except Exception:
            logger.warning("Failed to resolve MCP plugin '%s'", name)
            return None


REGISTRY = PluginRegistry()
