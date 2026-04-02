"""Base plugin adapter — protocol-agnostic logic for executing and ingesting plugin operations."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any

from shu.core.config import get_settings_instance
from shu.core.logging import get_logger
from shu.plugins.base import ExecuteContext, PluginResult
from shu.utils.path_access import DotPath

logger = get_logger(__name__)


class BasePluginAdapter(ABC):
    """Abstract base for plugin adapters.

    Provides generic dispatch, ingest pagination, field mapping, and cursor
    persistence. Subclasses implement transport-specific ``_call_tool`` and
    ``_assemble_response_data``.
    """

    def __init__(
        self,
        name: str,
        version: str,
        tool_configs: dict[str, Any] | None,
        discovered_tools: list[dict[str, Any]] | None,
        settings: Any | None = None,
    ) -> None:
        self.name: str = name
        self.version: str = version
        self._tool_configs = tool_configs
        self._discovered_tools = discovered_tools
        settings = settings or get_settings_instance()
        self._max_pagination_pages: int = getattr(settings, "max_pagination_limit", 1000)

    def _enabled_tools(self) -> dict[str, dict[str, Any]]:
        """Return tool_configs entries that are enabled."""
        configs = self._tool_configs or {}
        return {name: cfg for name, cfg in configs.items() if cfg.get("enabled", True)}

    def _discovered_tools_by_name(self) -> dict[str, dict[str, Any]]:
        """Index discovered tools by name for schema lookup."""
        tools = self._discovered_tools or []
        return {t["name"]: t for t in tools if isinstance(t, dict) and "name" in t}

    def get_schema_for_op(self, op: str) -> dict[str, Any] | None:
        """Return a flat per-op schema matching the native plugin format.

        Used by build_agent_tools so each tool gets a clean schema
        in the LLM payload rather than the combined allOf blob from get_schema().
        """
        enabled = self._enabled_tools()
        if op not in enabled:
            return None

        discovered = self._discovered_tools_by_name()
        tool_info = discovered.get(op, {})
        input_schema = tool_info.get("inputSchema")
        description = tool_info.get("description")

        if input_schema and isinstance(input_schema, dict):
            schema = dict(input_schema)
        else:
            schema = {"type": "object", "properties": {}, "additionalProperties": True}

        schema["title"] = op.replace("_", " ").replace("-", " ").title()
        if description:
            schema["description"] = description
        return schema

    def get_output_schema(self) -> dict[str, Any] | None:
        """Output schemas are ephemeral — return None."""
        return None

    async def execute(self, params: dict[str, Any], context: ExecuteContext, host: Any) -> PluginResult:
        """Dispatch to the appropriate tool based on params['op']."""
        op = params.get("op")
        if not op:
            return PluginResult.err("Missing required parameter: op", code="missing_op")

        enabled = self._enabled_tools()
        if op not in enabled:
            return PluginResult.err(f"Unknown or disabled tool: {op}", code="unknown_op")

        tool_config = enabled[op]
        is_feed_run = "__schedule_id" in params
        is_feed_eligible = tool_config.get("feed_eligible", False)
        is_chat_callable = tool_config.get("chat_callable", True)
        has_ingest_config = bool(tool_config.get("ingest"))

        if is_feed_run and is_feed_eligible and has_ingest_config:
            return await self._execute_ingest(op, params, tool_config, host)

        if not is_chat_callable:
            return PluginResult.err(
                f"Tool '{op}' is not callable from chat",
                code="not_chat_callable",
            )

        return await self._execute_chat_callable(op, params)

    @abstractmethod
    async def _call_tool(self, op: str, params: dict[str, Any]) -> Any | PluginResult:
        """Call the underlying tool. Returns a transport-specific result on success, or PluginResult on error."""

    @abstractmethod
    def _assemble_response_data(self, raw_result: Any) -> dict[str, Any]:
        """Convert transport-specific raw result into a plain dict for ingest processing."""

    async def _execute_chat_callable(self, op: str, params: dict[str, Any]) -> PluginResult:
        """Execute a chat-callable tool and return the result."""
        outcome = await self._call_tool(op, params)
        if isinstance(outcome, PluginResult):
            return outcome
        return PluginResult.ok({"result": outcome.content})

    async def _execute_ingest(
        self, op: str, params: dict[str, Any], tool_config: dict[str, Any], host: Any
    ) -> PluginResult:
        """Execute an ingest-annotated tool.

        Calls the tool, extracts items via collection_field, maps fields
        per the ingest config, and routes each through host.kb.ingest_text()
        or host.kb.ingest_document().

        Supports pagination: if cursor_field and cursor_param are configured,
        loops until no cursor is returned. Persists the last cursor via
        host.cursor between feed runs for incremental sync.
        """
        ingest_cfg = tool_config.get("ingest", {})
        if not ingest_cfg:
            return PluginResult.err("Missing ingest configuration for tool", code="missing_ingest_config")

        kb_id = params.get("kb_id")
        if not kb_id:
            kb_ids = getattr(host.kb, "_knowledge_base_ids", []) if host and hasattr(host, "kb") else []
            kb_id = kb_ids[0] if kb_ids else None
        if not kb_id:
            return PluginResult.err("No knowledge base bound for ingest", code="no_knowledge_base")
        field_mapping = ingest_cfg.get("field_mapping", {})
        collection_field = ingest_cfg.get("collection_field")
        method = ingest_cfg.get("method", "text")
        static_attributes = ingest_cfg.get("attributes", {})
        cursor_field = ingest_cfg.get("cursor_field")
        cursor_param = ingest_cfg.get("cursor_param")

        reset_cursor = params.get("reset_cursor", False)
        call_params = dict(params)
        if cursor_field and cursor_param and not reset_cursor:
            saved_cursor = await self._load_cursor(host, kb_id)
            if saved_cursor:
                call_params[cursor_param] = saved_cursor

        ingested_count = 0
        skipped_count = 0
        error_count = 0
        total_items = 0
        warnings: list[str] = []
        last_cursor: str | None = None

        for page in range(self._max_pagination_pages):
            outcome = await self._call_tool(op, call_params)
            if isinstance(outcome, PluginResult):
                if cursor_field and cursor_param and last_cursor:
                    await self._save_cursor(host, kb_id, last_cursor)
                return outcome

            response_data = self._assemble_response_data(outcome)
            items = self._extract_items(response_data, collection_field)
            total_items += len(items)

            for item in items:
                counts = await self._ingest_item(
                    item,
                    field_mapping,
                    method,
                    static_attributes,
                    kb_id,
                    host,
                    idx=(ingested_count + skipped_count + error_count),
                    op=op,
                    warnings=warnings,
                )
                ingested_count += counts[0]
                skipped_count += counts[1]
                error_count += counts[2]

            if not cursor_field or not cursor_param:
                break

            next_cursor = DotPath.get(response_data, cursor_field)
            if not next_cursor or next_cursor == last_cursor:
                break

            last_cursor = str(next_cursor)
            call_params[cursor_param] = last_cursor
        else:
            logger.warning(
                "plugin.pagination_limit_reached [%s/%s] after %d pages, ingested=%d",
                self.name, op, self._max_pagination_pages, ingested_count,
            )
            warnings.append(f"Pagination stopped after {self._max_pagination_pages} pages")

        if cursor_field and cursor_param and last_cursor and not await self._save_cursor(host, kb_id, last_cursor):
            warnings.append("Cursor save failed; next run may re-process items")

        logger.info(
            "plugin.ingest_complete [%s/%s] ingested=%d skipped=%d errors=%d total=%d",
            self.name,
            op,
            ingested_count,
            skipped_count,
            error_count,
            total_items,
        )

        return PluginResult.ok(
            data={
                "ingested_count": ingested_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "total_items": total_items,
            },
            warnings=warnings or None,
        )

    async def _ingest_item(
        self,
        item: dict[str, Any],
        field_mapping: dict[str, str],
        method: str,
        static_attributes: dict[str, str],
        kb_id: str,
        host: Any,
        idx: int,
        op: str,
        warnings: list[str],
    ) -> tuple[int, int, int]:
        """Ingest a single item. Returns (ingested, skipped, errored) counts."""
        mapped = self._map_fields(item, field_mapping, idx, warnings)
        if mapped is None:
            return (0, 1, 0)

        mapped_attrs = {**(static_attributes or {}), **(mapped.get("attributes") or {})}

        try:
            if method == "document":
                await host.kb.ingest_document(
                    kb_id,
                    file_bytes=mapped["content"].encode("utf-8")
                    if isinstance(mapped["content"], str)
                    else mapped["content"],
                    filename=mapped.get("filename", mapped["title"]),
                    mime_type=mapped.get("mime_type", "text/plain"),
                    source_id=mapped["source_id"],
                    source_url=mapped.get("source_url"),
                    attributes=mapped_attrs or None,
                )
            else:
                await host.kb.ingest_text(
                    kb_id,
                    title=mapped["title"],
                    content=mapped["content"],
                    source_id=mapped["source_id"],
                    source_url=mapped.get("source_url"),
                    attributes=mapped_attrs or None,
                )
            return (1, 0, 0)
        except Exception as exc:
            warnings.append(f"Item {idx}: ingest failed: {exc}")
            logger.warning("plugin.ingest_item_failed [%s/%s] item=%d: %s", self.name, op, idx, exc)
            return (0, 0, 1)

    async def _load_cursor(self, host: Any, kb_id: str) -> str | None:
        """Load the saved cursor from host.cursor if available."""
        if not hasattr(host, "cursor"):
            return None
        try:
            return await host.cursor.get(kb_id)
        except Exception as e:
            logger.warning("Could not load cursor: %s", e)
            return None

    async def _save_cursor(self, host: Any, kb_id: str, cursor: str) -> bool:
        """Persist the cursor via host.cursor for the next feed run. Returns True on success."""
        if not hasattr(host, "cursor"):
            return False
        try:
            await host.cursor.set(kb_id, cursor)
            return True
        except Exception:
            logger.warning("plugin.cursor_save_failed [%s] kb=%s", self.name, kb_id)
            return False

    def _extract_items(self, data: dict[str, Any], collection_field: str | None) -> list[dict[str, Any]]:
        """Extract items from response data using the collection_field path."""
        if not collection_field:
            return [data]

        value = DotPath.get(data, collection_field)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return [data]

    def _map_fields(
        self, item: dict[str, Any], field_mapping: dict[str, str], idx: int, warnings: list[str]
    ) -> dict[str, Any] | None:
        """Apply field mapping to extract title, content, source_id, source_url from an item.

        Returns None if required fields (title, content, source_id) are missing.
        """
        mapped: dict[str, Any] = {}
        for target_field in ("title", "content", "source_id"):
            source_path = field_mapping.get(target_field)
            if not source_path:
                warnings.append(f"Item {idx}: field mapping missing for required field '{target_field}'")
                return None
            value = DotPath.get(item, source_path)
            if value is None:
                warnings.append(f"Item {idx}: field '{target_field}' not found at path '{source_path}'")
                return None
            mapped[target_field] = str(value)

        for optional_field in ("source_url", "filename", "mime_type"):
            source_path = field_mapping.get(optional_field)
            if source_path:
                value = DotPath.get(item, source_path)
                if value is not None:
                    mapped[optional_field] = str(value)

        return mapped

    def _log_tool_call(
        self,
        op: str,
        start: float,
        status: str,
        result_size: int,
        code: str | None = None,
        error: str | None = None,
    ) -> None:
        """Log a structured tool call event."""
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "plugin.tool_call [%s/%s] %dms status=%s size=%d code=%s error=%s",
            self.name,
            op,
            duration_ms,
            status,
            result_size,
            code,
            error,
        )
