"""Service for API integration connection management.

Handles CRUD operations, OpenAPI spec sync, tool merging, and auth credential
storage (via plugin secrets) for API server connections.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.exceptions import ConflictError, NotFoundError, ValidationError
from shu.core.logging import get_logger
from shu.models.api_server_connection import ApiServerConnection
from shu.models.plugin_feed import PluginFeed
from shu.models.plugin_registry import PluginDefinition
from shu.plugins.api_adapter import ApiPluginAdapter
from shu.plugins.loader import PluginRecord
from shu.plugins.openapi_parser import fetch_and_parse
from shu.schemas.api_integration_admin import (
    ApiConnectionUpdate,
    ApiIntegrationDefinition,
    ApiSyncResult,
)
from shu.schemas.integration_common import ToolConfigUpdate
from shu.services.plugin_secrets import delete_secret, get_secret, set_secret
from shu.services.policy_engine import POLICY_CACHE, enforce_pbac

logger = get_logger(__name__)

DEGRADED_THRESHOLD = 5


class ApiIntegrationService:
    """Business logic for API integration connection management."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_connection(
        self, yaml_content: str, auth_credential: str | None, user_id: str
    ) -> ApiServerConnection:
        """Create a new API integration connection from YAML content.

        Parses and validates the YAML definition, checks name uniqueness,
        enforces PBAC, and persists the connection record.
        Does not fetch the OpenAPI spec; call sync_connection for that.
        """
        parsed = self._parse_yaml(yaml_content)
        definition = self._validate_definition(parsed)

        await enforce_pbac(
            user_id,
            "plugin.create",
            f"plugin:api:{definition.name}",
            self.db,
            message="Not authorized to manage API integrations",
        )

        await self._check_name_unique(definition.name)

        auth_config = None
        if definition.auth:
            auth_config = {
                "type": definition.auth.type.value,
                "name": definition.auth.name,
                "prefix": definition.auth.prefix or "",
            }

        connection = ApiServerConnection(
            name=definition.name,
            url=definition.openapi_definition,
            spec_type="openapi",
            import_source=parsed,
            auth_config=auth_config,
        )

        self.db.add(connection)
        await self.db.commit()
        await self.db.refresh(connection)

        if auth_credential:
            await self._store_auth_credential(connection.name, auth_credential, user_id)

        logger.info("api.connection_created [%s] %s", connection.name, connection.url)
        return connection

    async def list_connections(self, user_id: str) -> list[ApiServerConnection]:
        """Return API integration connections the user has read access to."""
        result = await self.db.execute(select(ApiServerConnection).order_by(ApiServerConnection.name))
        connections = list(result.scalars().all())
        if not connections:
            return []

        resource_ids = [f"api:{c.name}" for c in connections]
        denied = await POLICY_CACHE.get_denied_resources(
            user_id,
            "plugin.read",
            "plugin",
            resource_ids,
            self.db,
        )
        return [c for c in connections if f"api:{c.name}" not in denied]

    async def get_connection(self, connection_id: str, user_id: str) -> ApiServerConnection:
        """Return a single API integration connection or raise 404."""
        return await self._get_connection_or_404(connection_id, user_id, "plugin.read")

    async def update_connection(
        self, connection_id: str, data: ApiConnectionUpdate, user_id: str
    ) -> ApiServerConnection:
        """Update an existing API integration connection."""
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")
        provided = data.model_fields_set

        if "timeouts" in provided:
            connection.timeouts = data.timeouts.model_dump() if data.timeouts else None

        if "response_size_limit_bytes" in provided:
            connection.response_size_limit_bytes = data.response_size_limit_bytes

        if "enabled" in provided and data.enabled is not None:
            connection.enabled = data.enabled
            await self._sync_plugin_enabled(f"api:{connection.name}", data.enabled)

        await self.db.commit()
        await self.db.refresh(connection)

        await self._invalidate_registry(connection.name)
        logger.info("api.connection_updated [%s] %s", connection.name, connection.url)
        return connection

    async def delete_connection(self, connection_id: str, user_id: str) -> None:
        """Delete an API integration connection.

        Blocks deletion if active feeds reference this connection's plugin name.
        """
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.delete")

        plugin_name = f"api:{connection.name}"
        result = await self.db.execute(select(PluginFeed.id).where(PluginFeed.plugin_name == plugin_name))
        feed_ids = [row[0] for row in result.all()]
        if feed_ids:
            raise ConflictError(
                f"Cannot delete API connection '{connection.name}': " f"{len(feed_ids)} active feed(s) reference it",
                details={"feed_ids": feed_ids},
            )

        connection_name = connection.name
        connection_url = connection.url

        await self._purge_auth_credential(connection_name)

        res = await self.db.execute(select(PluginDefinition).where(PluginDefinition.name == plugin_name))
        defn = res.scalars().first()
        if defn:
            await self.db.delete(defn)

        await self.db.delete(connection)
        await self.db.commit()

        await self._invalidate_registry(connection_name)
        logger.info("api.connection_deleted [%s] %s", connection_name, connection_url)

    async def update_tool_config(
        self,
        connection_id: str,
        tool_name: str,
        data: ToolConfigUpdate,
        user_id: str,
    ) -> ApiServerConnection:
        """Update the configuration for a single tool on a connection."""
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")

        configs = dict(connection.tool_configs or {})
        if tool_name not in configs:
            discovered_names = [t.get("name") for t in (connection.discovered_tools or [])]
            if tool_name not in discovered_names:
                raise NotFoundError(f"Tool '{tool_name}' not found on connection '{connection.name}'")
            configs[tool_name] = {"chat_callable": True, "feed_eligible": False, "enabled": True}

        configs[tool_name] = data.model_dump(exclude_none=False)
        connection.tool_configs = configs

        await self.db.commit()
        await self.db.refresh(connection)

        await self._invalidate_registry(connection.name)
        logger.info(
            "api.tool_config_updated [%s] tool=%s chat=%s feed=%s",
            connection.name,
            tool_name,
            data.chat_callable,
            data.feed_eligible,
        )
        return connection

    async def sync_connection(self, connection_id: str, user_id: str) -> ApiSyncResult:
        """Fetch an OpenAPI spec, discover tools, and update the connection record."""
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")

        start = time.monotonic()
        try:
            parse_result = await fetch_and_parse(connection.url)
        except Exception as exc:
            self._record_failure(connection, str(exc))
            await self.db.commit()
            logger.info(
                "api.sync_failed [%s] %s failures=%d error=%s",
                connection.name,
                connection.url,
                connection.consecutive_failures,
                exc,
            )
            return ApiSyncResult(tools=[], errors=[str(exc)])

        if parse_result.errors and not parse_result.discovered_tools:
            self._record_failure(connection, parse_result.errors[0])
            await self.db.commit()
            logger.info(
                "api.sync_failed [%s] %s failures=%d errors=%s",
                connection.name,
                connection.url,
                connection.consecutive_failures,
                parse_result.errors,
            )
            return ApiSyncResult(tools=[], errors=parse_result.errors)

        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_success(connection)

        result = self._merge_discovered_tools(connection, parse_result.discovered_tools)

        if parse_result.base_url:
            connection.base_url = parse_result.base_url

        if parse_result.errors:
            result.errors = parse_result.errors

        await self.db.commit()
        await self.db.refresh(connection)

        await self._invalidate_registry(connection.name)
        logger.info(
            "api.sync_complete [%s] %dms tools=%d added=%s stale=%s",
            connection.name,
            latency_ms,
            len(result.tools),
            result.added,
            result.stale,
        )
        return result

    def _merge_discovered_tools(
        self,
        connection: ApiServerConnection,
        discovered_tools: list[dict[str, Any]],
    ) -> ApiSyncResult:
        """Merge discovered tools into the connection record.

        Preserves admin configs for known tools. Defaults new tools to
        chat_callable. Marks stale tools (present before but absent now)
        with stale=true instead of removing them.
        """
        existing_configs = dict(connection.tool_configs or {})
        new_names = {t["name"] for t in discovered_tools}
        is_first_sync = connection.last_synced_at is None and not existing_configs

        merged_configs: dict[str, Any] = {}
        added: list[str] = []
        stale: list[str] = []

        for tool in discovered_tools:
            name = tool["name"]
            if name in existing_configs:
                cfg = dict(existing_configs[name])
                cfg.pop("stale", None)
                merged_configs[name] = cfg
            else:
                cfg: dict[str, Any] = {"chat_callable": False, "feed_eligible": False, "enabled": True}
                if is_first_sync:
                    cfg = self._apply_ingest_defaults(connection, name, cfg)
                merged_configs[name] = cfg
                added.append(name)

        for name, cfg in existing_configs.items():
            if name not in new_names:
                stale_cfg = dict(cfg)
                stale_cfg["stale"] = True
                merged_configs[name] = stale_cfg
                stale.append(name)

        connection.discovered_tools = discovered_tools
        connection.tool_configs = merged_configs
        connection.last_synced_at = datetime.now(UTC)

        return ApiSyncResult(
            tools=sorted(new_names),
            added=added,
            stale=stale,
        )

    def _apply_ingest_defaults(
        self, connection: ApiServerConnection, tool_name: str, cfg: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply ingest_defaults from import_source to a tool config on first sync."""
        import_source = connection.import_source or {}
        ingest_defaults = import_source.get("ingest_defaults")
        if not ingest_defaults or tool_name not in ingest_defaults:
            return cfg
        cfg["ingest"] = ingest_defaults[tool_name]
        cfg["feed_eligible"] = True
        return cfg

    def generate_plugin_record(self, connection: ApiServerConnection) -> PluginRecord:
        """Build a PluginRecord from the connection's tool_configs."""
        configs = connection.tool_configs or {}
        chat_ops = []
        feed_ops = []

        for name, cfg in configs.items():
            if not cfg.get("enabled", True):
                continue
            if cfg.get("stale", False):
                continue
            if cfg.get("chat_callable", False):
                chat_ops.append(name)
            if cfg.get("feed_eligible", False):
                feed_ops.append(name)

        return PluginRecord(
            name=f"api:{connection.name}",
            version="1.0",
            entry="shu.plugins.api_adapter:ApiPluginAdapter",
            capabilities=["http", "kb"],
            display_name=f"{connection.name} (API)",
            default_feed_op=feed_ops[0] if feed_ops else None,
            allowed_feed_ops=feed_ops or None,
            chat_callable_ops=chat_ops or None,
        )

    async def generate_all_plugin_records(self) -> list[PluginRecord]:
        """Query all enabled API connections and build a PluginRecord for each."""
        result = await self.db.execute(select(ApiServerConnection).where(ApiServerConnection.enabled.is_(True)))
        records = []
        for conn in result.scalars().all():
            try:
                records.append(self.generate_plugin_record(conn))
            except Exception:
                logger.warning("Failed to generate PluginRecord for API connection '%s'", conn.name)
        return records

    async def is_connection_enabled(self, connection_name: str) -> bool:
        """Check if an API connection is enabled by its connection name."""
        result = await self.db.execute(
            select(ApiServerConnection.enabled).where(ApiServerConnection.name == connection_name)
        )
        return bool(result.scalar())

    async def resolve_adapter(self, connection_name: str) -> ApiPluginAdapter | None:
        """Load an enabled connection and return an ApiPluginAdapter instance, or None."""
        result = await self.db.execute(
            select(ApiServerConnection).where(
                ApiServerConnection.name == connection_name,
                ApiServerConnection.enabled.is_(True),
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            return None

        credential = await self._load_auth_credential(connection_name)
        return ApiPluginAdapter(connection, credential=credential)

    def _record_success(self, connection: ApiServerConnection) -> None:
        """Update health tracking on successful sync."""
        connection.consecutive_failures = 0
        connection.last_error = None

    def _record_failure(self, connection: ApiServerConnection, error: str) -> None:
        """Update health tracking on failed sync."""
        previous_failures = connection.consecutive_failures or 0
        connection.consecutive_failures = previous_failures + 1
        connection.last_error = error[:500]

        if previous_failures < DEGRADED_THRESHOLD <= connection.consecutive_failures:
            logger.info(
                "api.connection_degraded [%s] %s failures=%d",
                connection.name,
                connection.url,
                connection.consecutive_failures,
            )

    async def _get_connection_or_404(
        self,
        connection_id: str,
        user_id: str,
        action: str,
    ) -> ApiServerConnection:
        """Load a connection by ID, enforce PBAC, or raise NotFoundError."""
        result = await self.db.execute(select(ApiServerConnection).where(ApiServerConnection.id == connection_id))
        connection = result.scalar_one_or_none()
        if not connection:
            raise NotFoundError(f"API connection '{connection_id}' not found")
        await enforce_pbac(
            user_id,
            action,
            f"plugin:api:{connection.name}",
            self.db,
            message=f"API connection '{connection_id}' not found",
        )
        return connection

    async def _check_name_unique(self, name: str, exclude_id: str | None = None) -> None:
        """Raise ConflictError if the name is already taken."""
        stmt = select(ApiServerConnection.id).where(ApiServerConnection.name == name)
        if exclude_id:
            stmt = stmt.where(ApiServerConnection.id != exclude_id)
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictError(f"API connection with name '{name}' already exists")

    async def _invalidate_registry(self, connection_name: str) -> None:
        """Evict cached adapter and sync the plugin registry after a mutation."""
        from ..plugins.registry import REGISTRY

        REGISTRY._cache.pop(f"api:{connection_name}", None)
        await REGISTRY.sync(self.db)

    async def _sync_plugin_enabled(self, plugin_name: str, enabled: bool) -> None:
        """Keep PluginDefinition.enabled in sync with the API connection state."""
        result = await self.db.execute(select(PluginDefinition).where(PluginDefinition.name == plugin_name))
        row = result.scalars().first()
        if row and row.enabled != enabled:
            row.enabled = enabled

    async def _store_auth_credential(self, connection_name: str, credential: str, user_id: str) -> None:
        """Store auth credential as a plugin secret."""
        plugin_name = f"api:{connection_name}"
        await set_secret(plugin_name, "auth_credential", value=credential, user_id=user_id, scope="system")

    async def _load_auth_credential(self, connection_name: str) -> str | None:
        """Load auth credential from plugin secrets."""
        plugin_name = f"api:{connection_name}"
        return await get_secret(plugin_name, "auth_credential", user_id=None, scope="system")

    async def _purge_auth_credential(self, connection_name: str) -> None:
        """Delete auth credential secret for a connection."""
        plugin_name = f"api:{connection_name}"
        await delete_secret(plugin_name, "auth_credential", user_id=None, scope="system")

    def _parse_yaml(self, yaml_content: str) -> dict[str, Any]:
        """Parse raw YAML content into a dict, raising ValidationError on failure."""
        try:
            parsed = yaml.safe_load(yaml_content)
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML: {exc}")
        if not isinstance(parsed, dict):
            raise ValidationError("YAML content must be a mapping")
        return parsed

    def _validate_definition(self, parsed: dict[str, Any]) -> ApiIntegrationDefinition:
        """Validate a parsed YAML dict against the ApiIntegrationDefinition schema."""
        try:
            return ApiIntegrationDefinition(**parsed)
        except Exception as exc:
            raise ValidationError(f"Invalid API integration definition: {exc}")
