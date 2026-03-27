"""Service for MCP server connection management.

Handles CRUD operations, URL validation, and auth header storage
(via plugin secrets) for MCP server connections.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.exceptions import ConflictError, NotFoundError
from shu.core.logging import get_logger
from shu.models.mcp_server_connection import McpServerConnection
from shu.models.plugin_feed import PluginFeed
from shu.models.plugin_registry import PluginDefinition
from shu.plugins.loader import PluginRecord
from shu.plugins.mcp_adapter import McpPluginAdapter
from shu.plugins.mcp_client import McpClient, McpError
from shu.schemas.mcp_admin import (
    McpConnectionCreate,
    McpConnectionUpdate,
    McpSyncResult,
    McpToolConfigUpdate,
)
from shu.services.plugin_secrets import delete_secret, get_secret, list_secret_keys, set_secret
from shu.services.policy_engine import POLICY_CACHE, enforce_pbac

logger = get_logger(__name__)

DEGRADED_THRESHOLD = 5


class McpService:
    """Business logic for MCP server connection management."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_connection(self, data: McpConnectionCreate, user_id: str) -> McpServerConnection:
        """Create a new MCP server connection.

        Validates the URL, encrypts auth headers, and persists the connection.
        """
        await enforce_pbac(
            user_id,
            "plugin.create",
            f"plugin:mcp:{data.name}",
            self.db,
            message="Not authorized to manage MCP connections",
        )

        await self._check_name_unique(data.name)

        connection = McpServerConnection(
            name=data.name,
            url=data.url,
            timeouts=data.timeouts.model_dump() if data.timeouts else None,
            response_size_limit_bytes=data.response_size_limit_bytes,
            enabled=data.enabled,
        )

        self.db.add(connection)
        await self.db.commit()
        await self.db.refresh(connection)

        if data.headers:
            await self._store_headers(connection.name, data.headers, user_id)

        logger.info("mcp.connection_created [%s] %s", connection.name, connection.url)
        return connection

    async def update_connection(
        self, connection_id: str, data: McpConnectionUpdate, user_id: str
    ) -> McpServerConnection:
        """Update an existing MCP server connection.

        Re-encrypts headers if changed.
        """
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")
        old_name = connection.name

        if data.name is not None and data.name != connection.name:
            await self._check_name_unique(data.name, exclude_id=connection_id)
            connection.name = data.name

        if data.url is not None:
            connection.url = data.url

        if data.timeouts is not None:
            connection.timeouts = data.timeouts.model_dump()

        if data.response_size_limit_bytes is not None:
            connection.response_size_limit_bytes = data.response_size_limit_bytes

        if data.enabled is not None:
            connection.enabled = data.enabled
            await self._sync_plugin_enabled(f"mcp:{connection.name}", data.enabled)

        await self.db.commit()
        await self.db.refresh(connection)

        if connection.name != old_name:
            await self._migrate_headers(old_name, connection.name, user_id)

        if data.headers is not None:
            await self._store_headers(connection.name, data.headers, user_id)

        logger.info("mcp.connection_updated [%s] %s", connection.name, connection.url)
        return connection

    async def delete_connection(self, connection_id: str, user_id: str) -> None:
        """Delete an MCP server connection.

        Blocks deletion if active feeds reference this connection's plugin name.
        """
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.delete")

        plugin_name = f"mcp:{connection.name}"
        result = await self.db.execute(select(PluginFeed.id).where(PluginFeed.plugin_name == plugin_name))
        feed_ids = [row[0] for row in result.all()]
        if feed_ids:
            raise ConflictError(
                f"Cannot delete MCP connection '{connection.name}': " f"{len(feed_ids)} active feed(s) reference it",
                details={"feed_ids": feed_ids},
            )

        await self._purge_headers(connection.name)
        await self.db.delete(connection)
        await self.db.commit()

        logger.info("mcp.connection_deleted [%s] %s", connection.name, connection.url)

    async def list_connections(self, user_id: str) -> list[McpServerConnection]:
        """Return MCP server connections the user has read access to."""
        result = await self.db.execute(select(McpServerConnection).order_by(McpServerConnection.name))
        connections = list(result.scalars().all())
        if not connections:
            return []

        resource_ids = [f"mcp:{c.name}" for c in connections]
        denied = await POLICY_CACHE.get_denied_resources(
            user_id,
            "plugin.read",
            "plugin",
            resource_ids,
            self.db,
        )
        return [c for c in connections if f"mcp:{c.name}" not in denied]

    async def get_connection(self, connection_id: str, user_id: str) -> McpServerConnection:
        """Return a single MCP server connection or raise 404."""
        return await self._get_connection_or_404(connection_id, user_id, "plugin.read")

    async def sync_connection(self, connection_id: str, user_id: str) -> McpSyncResult:
        """Connect to an MCP server, discover tools, and update the connection record.

        Also serves as the connectivity test — latency is tracked in the result.
        """
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")
        client = await self.make_client(connection)

        start = time.monotonic()
        try:
            init_result = await client.connect()
            tools = await client.list_tools()
        except McpError as exc:
            self._record_failure(connection, str(exc))
            await self.db.commit()
            logger.info(
                "mcp.sync_failed [%s] %s failures=%d error=%s",
                connection.name,
                connection.url,
                connection.consecutive_failures,
                exc,
            )
            return McpSyncResult(tools_discovered=0, errors=[str(exc)])
        finally:
            await client.close()

        latency_ms = int((time.monotonic() - start) * 1000)
        self._record_success(connection)

        result = self._merge_discovered_tools(connection, tools, init_result)

        await self.db.commit()
        await self.db.refresh(connection)

        logger.info(
            "mcp.sync_complete [%s] %dms tools=%d added=%s removed=%s",
            connection.name,
            latency_ms,
            len(result.tools),
            result.added,
            result.removed,
        )
        return result

    async def update_tool_config(
        self,
        connection_id: str,
        tool_name: str,
        data: McpToolConfigUpdate,
        user_id: str,
    ) -> McpServerConnection:
        """Update the configuration for a single tool on a connection."""
        connection = await self._get_connection_or_404(connection_id, user_id, "plugin.update")

        configs = dict(connection.tool_configs or {})
        if tool_name not in configs:
            discovered_names = [t.get("name") for t in (connection.discovered_tools or [])]
            if tool_name not in discovered_names:
                raise NotFoundError(f"Tool '{tool_name}' not found on connection '{connection.name}'")
            configs[tool_name] = {"type": "chat_callable", "enabled": True}

        configs[tool_name] = data.model_dump(exclude_none=False)
        connection.tool_configs = configs

        await self.db.commit()
        await self.db.refresh(connection)

        logger.info("mcp.tool_config_updated [%s] tool=%s type=%s", connection.name, tool_name, data.type.value)
        return connection

    def _merge_discovered_tools(
        self,
        connection: McpServerConnection,
        tools: list[Any],
        init_result: dict[str, Any],
    ) -> McpSyncResult:
        """Merge discovered tools into the connection record.

        Preserves admin type/field_mapping for known tools.
        Defaults new tools to chat_callable. Removes stale tools.
        Updates: server_info, discovered_tools, tool_configs, last_synced_at.
        """
        connection.server_info = init_result.get("serverInfo")

        existing_configs = dict(connection.tool_configs or {})
        new_names = {t.name for t in tools}

        merged_configs: dict[str, Any] = {}
        added: list[str] = []

        for tool in tools:
            if tool.name in existing_configs:
                merged_configs[tool.name] = existing_configs[tool.name]
            else:
                merged_configs[tool.name] = {"type": "chat_callable", "enabled": True}
                added.append(tool.name)

        removed = [name for name in existing_configs if name not in new_names]

        connection.discovered_tools = [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools
        ]
        connection.tool_configs = merged_configs
        connection.last_synced_at = datetime.now(UTC)

        return McpSyncResult(
            tools=sorted(new_names),
            added=added,
            removed=removed,
        )

    async def generate_all_plugin_records(self) -> list[PluginRecord]:
        """Query all enabled MCP connections and build a PluginRecord for each."""
        result = await self.db.execute(select(McpServerConnection).where(McpServerConnection.enabled.is_(True)))
        records = []
        for conn in result.scalars().all():
            try:
                records.append(self.generate_plugin_record(conn))
            except Exception:
                logger.warning("Failed to generate PluginRecord for MCP connection '%s'", conn.name)
        return records

    async def is_connection_enabled(self, connection_name: str) -> bool:
        """Check if an MCP connection is enabled by its connection name."""
        result = await self.db.execute(
            select(McpServerConnection.enabled).where(McpServerConnection.name == connection_name)
        )
        return bool(result.scalar())

    async def resolve_adapter(self, connection_name: str):
        """Load an enabled connection and return an McpPluginAdapter instance, or None."""
        result = await self.db.execute(
            select(McpServerConnection).where(
                McpServerConnection.name == connection_name,
                McpServerConnection.enabled.is_(True),
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            return None

        client = await self.make_client(connection)
        return McpPluginAdapter(connection, client)

    async def get_connection_schema(self, connection_name: str) -> dict | None:
        """Build the input schema for an MCP connection from its discovered tools."""
        result = await self.db.execute(select(McpServerConnection).where(McpServerConnection.name == connection_name))
        connection = result.scalar_one_or_none()
        if not connection:
            return None
        return McpPluginAdapter(connection).get_schema()

    def generate_plugin_record(self, connection: McpServerConnection) -> PluginRecord:
        """Build a PluginRecord from the connection's tool_configs."""
        configs = connection.tool_configs or {}
        chat_ops = []
        feed_ops = []

        for name, cfg in configs.items():
            if not cfg.get("enabled", True):
                continue
            tool_type = cfg.get("type", "chat_callable")
            if tool_type == "chat_callable":
                chat_ops.append(name)
            elif tool_type == "ingest":
                feed_ops.append(name)

        server_info = connection.server_info or {}
        return PluginRecord(
            name=f"mcp:{connection.name}",
            version=server_info.get("version", "1.0"),
            entry="shu.plugins.mcp_adapter:McpPluginAdapter",
            capabilities=["http", "kb"],
            display_name=f"{connection.name} (MCP)",
            default_feed_op=feed_ops[0] if feed_ops else None,
            allowed_feed_ops=feed_ops or None,
            chat_callable_ops=chat_ops or None,
        )

    async def _sync_plugin_enabled(self, plugin_name: str, enabled: bool) -> None:
        """Keep PluginDefinition.enabled in sync with the MCP connection state."""
        result = await self.db.execute(select(PluginDefinition).where(PluginDefinition.name == plugin_name))
        row = result.scalars().first()
        if row and row.enabled != enabled:
            row.enabled = enabled

    def _record_success(self, connection: McpServerConnection) -> None:
        """Update health tracking on successful connection."""
        connection.last_connected_at = datetime.now(UTC)
        connection.consecutive_failures = 0
        connection.last_error = None

    def _record_failure(self, connection: McpServerConnection, error: str) -> None:
        """Update health tracking on failed connection."""
        previous_failures = connection.consecutive_failures or 0
        connection.consecutive_failures = previous_failures + 1
        connection.last_error = error[:500]

        if previous_failures < DEGRADED_THRESHOLD <= connection.consecutive_failures:
            logger.info(
                "mcp.connection_degraded [%s] %s failures=%d",
                connection.name,
                connection.url,
                connection.consecutive_failures,
            )

    async def make_client(self, connection: McpServerConnection) -> McpClient:
        """Create a new McpClient for a connection, loading headers from plugin secrets."""
        headers = await self._load_headers(connection.name)
        return McpClient(
            url=connection.url,
            headers=headers,
            timeouts=connection.timeouts,
            response_size_limit=connection.response_size_limit_bytes,
        )

    async def _get_connection_or_404(
        self,
        connection_id: str,
        user_id: str,
        action: str,
    ) -> McpServerConnection:
        """Load a connection by ID, enforce PBAC, or raise NotFoundError."""
        result = await self.db.execute(select(McpServerConnection).where(McpServerConnection.id == connection_id))
        connection = result.scalar_one_or_none()
        if not connection:
            raise NotFoundError(f"MCP connection '{connection_id}' not found")
        await enforce_pbac(
            user_id,
            action,
            f"plugin:mcp:{connection.name}",
            self.db,
            message=f"MCP connection '{connection_id}' not found",
        )
        return connection

    async def _check_name_unique(self, name: str, exclude_id: str | None = None) -> None:
        """Raise ConflictError if the name is already taken."""
        stmt = select(McpServerConnection.id).where(McpServerConnection.name == name)
        if exclude_id:
            stmt = stmt.where(McpServerConnection.id != exclude_id)
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictError(f"MCP connection with name '{name}' already exists")

    async def _get_header_keys(self, connection_name: str) -> tuple[str, list[str]]:
        """Return (plugin_name, header secret keys) for a connection."""
        plugin_name = f"mcp:{connection_name}"
        all_keys = await list_secret_keys(plugin_name, user_id=None, scope="system")
        return plugin_name, [k for k in all_keys if k.startswith("header:")]

    async def _store_headers(self, connection_name: str, headers: dict[str, str], user_id: str) -> None:
        """Store auth headers as plugin secrets, replacing any existing ones."""
        plugin_name, existing = await self._get_header_keys(connection_name)
        for key in existing:
            await delete_secret(plugin_name, key, user_id=None, scope="system")
        for key, value in headers.items():
            await set_secret(plugin_name, f"header:{key}", value=value, user_id=user_id, scope="system")

    async def _load_headers(self, connection_name: str) -> dict[str, str] | None:
        """Load auth headers from plugin secrets. Returns None if no headers stored."""
        plugin_name, header_keys = await self._get_header_keys(connection_name)
        if not header_keys:
            return None
        headers = {}
        for key in header_keys:
            value = await get_secret(plugin_name, key, user_id=None, scope="system")
            if value is not None:
                headers[key.removeprefix("header:")] = value
        return headers or None

    async def _purge_headers(self, connection_name: str) -> None:
        """Delete all auth header secrets for a connection."""
        plugin_name, header_keys = await self._get_header_keys(connection_name)
        for key in header_keys:
            await delete_secret(plugin_name, key, user_id=None, scope="system")

    async def _migrate_headers(self, old_name: str, new_name: str, user_id: str) -> None:
        """Move auth header secrets from old plugin name to new on rename."""
        old_plugin, header_keys = await self._get_header_keys(old_name)
        for key in header_keys:
            value = await get_secret(old_plugin, key, user_id=None, scope="system")
            if value is not None:
                await set_secret(f"mcp:{new_name}", key, value=value, user_id=user_id, scope="system")
            await delete_secret(old_plugin, key, user_id=None, scope="system")
