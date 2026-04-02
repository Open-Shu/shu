"""Tests for McpPluginAdapter.

Covers schema generation, chat-callable execution, ingest execution
(including field mapping, collection extraction, document routing,
error resilience), dispatch validation, and cursor-based pagination.

All tests use fake collaborators (FakeConnection, FakeKb, FakeCursor,
FakeHost) and an AsyncMock McpClient to avoid DB or network calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from shu.plugins.base import ExecuteContext, PluginResult
from shu.plugins.mcp_adapter import McpPluginAdapter
from shu.plugins.mcp_client import McpTimeoutError, McpToolResult


class FakeConnection:
    """Minimal stand-in for McpServerConnection."""

    def __init__(
        self,
        name: str = "test-server",
        tool_configs: dict[str, Any] | None = None,
        discovered_tools: list[dict[str, Any]] | None = None,
        server_info: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.tool_configs = tool_configs
        self.discovered_tools = discovered_tools or []
        self.server_info = server_info or {}


class FakeKb:
    """Tracks ingest calls for assertions."""

    def __init__(self) -> None:
        self._knowledge_base_ids = ["kb1"]
        self.text_calls: list[dict[str, Any]] = []
        self.doc_calls: list[dict[str, Any]] = []

    async def ingest_text(
        self,
        kb_id: str,
        *,
        title: str,
        content: str,
        source_id: str,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.text_calls.append({"kb_id": kb_id, "title": title, "source_id": source_id, "content": content})
        return {"status": "ok"}

    async def ingest_document(
        self,
        kb_id: str,
        *,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        source_id: str,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.doc_calls.append({"kb_id": kb_id, "filename": filename, "source_id": source_id})
        return {"status": "ok"}


class FakeCursor:
    """Tracks cursor get/set calls."""

    def __init__(self, initial: str | None = None) -> None:
        self.saved: str | None = None
        self._initial = initial

    async def get(self, kb_id: str) -> str | None:
        return self._initial

    async def set(self, kb_id: str, value: str) -> None:
        self.saved = value


class FakeHost:
    def __init__(self, kb: FakeKb | None = None, cursor: FakeCursor | None = None) -> None:
        self.kb = kb or FakeKb()
        self.cursor = cursor or FakeCursor()


def _make_adapter(
    tool_configs: dict[str, Any] | None = None,
    discovered_tools: list[dict[str, Any]] | None = None,
    server_info: dict[str, Any] | None = None,
) -> tuple[McpPluginAdapter, AsyncMock]:
    """Build an adapter with a FakeConnection and an AsyncMock client."""
    conn = FakeConnection(
        tool_configs=tool_configs,
        discovered_tools=discovered_tools,
        server_info=server_info,
    )
    client = AsyncMock()
    adapter = McpPluginAdapter(conn, client)  # type: ignore[arg-type]
    return adapter, client


def _ctx() -> ExecuteContext:
    """Shorthand for a test ExecuteContext."""
    return ExecuteContext(user_id="u1")


def _json_result(data: dict[str, Any], is_error: bool = False) -> McpToolResult:
    """Build an McpToolResult wrapping a JSON text block."""
    return McpToolResult(
        content=[{"type": "text", "text": json.dumps(data)}],
        is_error=is_error,
    )


class TestGetSchema:
    """Schema generation from discovered tools and admin tool_configs."""

    def test_multiple_tools_produces_op_enum_and_conditional_properties(self) -> None:
        """Two enabled tools produce a sorted op enum and show_when properties."""
        adapter, _ = _make_adapter(
            tool_configs={
                "search": {"enabled": True, "chat_callable": True, "feed_eligible": False},
                "fetch": {"enabled": True, "chat_callable": False, "feed_eligible": True},
            },
            discovered_tools=[
                {
                    "name": "search",
                    "description": "Search things",
                    "inputSchema": {
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "fetch",
                    "description": "Fetch data",
                    "inputSchema": {
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            ],
        )

        schema = adapter.get_schema()
        assert schema is not None
        assert schema["properties"]["op"]["enum"] == ["fetch", "search"]
        assert "allOf" not in schema
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["x-ui"]["show_when"] == {"field": "op", "in": ["search"]}
        assert "url" in schema["properties"]
        assert schema["properties"]["url"]["x-ui"]["show_when"] == {"field": "op", "in": ["fetch"]}

    def test_disabled_tools_filtered_from_schema(self) -> None:
        """Only enabled tools appear in the op enum."""
        adapter, _ = _make_adapter(
            tool_configs={
                "enabled_tool": {"enabled": True},
                "disabled_tool": {"enabled": False},
            },
            discovered_tools=[
                {"name": "enabled_tool", "description": "Enabled"},
                {"name": "disabled_tool", "description": "Disabled"},
            ],
        )

        schema = adapter.get_schema()
        assert schema is not None
        assert schema["properties"]["op"]["enum"] == ["enabled_tool"]

    def test_no_enabled_tools_returns_none(self) -> None:
        """Empty tool_configs produces no schema."""
        adapter, _ = _make_adapter(tool_configs={})
        assert adapter.get_schema() is None


class TestChatCallable:
    """Chat-callable tool execution: success, MCP errors, and tool-level errors."""

    @pytest.mark.asyncio
    async def test_success_returns_plugin_result_ok(self) -> None:
        """Successful tool call wraps content in PluginResult.ok and forwards params sans 'op'."""
        adapter, client = _make_adapter(
            tool_configs={"search": {"enabled": True, "chat_callable": True, "feed_eligible": False}},
        )
        client.call_tool.return_value = McpToolResult(
            content=[{"type": "text", "text": "hello"}], is_error=False
        )

        result = await adapter.execute({"op": "search", "q": "test"}, _ctx(), None)

        assert result.status == "success"
        assert result.data == {"result": [{"type": "text", "text": "hello"}]}
        client.call_tool.assert_awaited_once_with("search", {"q": "test"})

    @pytest.mark.asyncio
    async def test_timeout_returns_plugin_result_err(self) -> None:
        """McpTimeoutError maps to PluginResult.err with code mcp_timeout."""
        adapter, client = _make_adapter(
            tool_configs={"search": {"enabled": True, "chat_callable": True, "feed_eligible": False}},
        )
        client.call_tool.side_effect = McpTimeoutError("timed out")

        result = await adapter.execute({"op": "search"}, _ctx(), None)

        assert result.status == "error"
        assert result.error["code"] == "mcp_timeout"
        assert "timed out" in result.error["message"]

    @pytest.mark.asyncio
    async def test_is_error_true_returns_plugin_result_err(self) -> None:
        """Tool-level error (isError=true) maps to mcp_server_error with text content."""
        adapter, client = _make_adapter(
            tool_configs={"search": {"enabled": True, "chat_callable": True, "feed_eligible": False}},
        )
        client.call_tool.return_value = McpToolResult(
            content=[{"type": "text", "text": "something broke"}], is_error=True
        )

        result = await adapter.execute({"op": "search"}, _ctx(), None)

        assert result.status == "error"
        assert result.error["code"] == "mcp_server_error"
        assert "something broke" in result.error["message"]


class TestIngest:
    """Ingest execution: collection extraction, field mapping, document routing, error resilience."""

    def _ingest_config(self, **overrides: Any) -> dict[str, Any]:
        """Build a standard ingest tool_config with optional overrides."""
        base = {
            "enabled": True,
            "chat_callable": False, "feed_eligible": True,
            "ingest": {
                "collection_field": "items",
                "method": "text",
                "field_mapping": {
                    "title": "name",
                    "content": "body",
                    "source_id": "id",
                },
            },
        }
        base["ingest"].update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_collection_all_mapped(self) -> None:
        """All items in the collection have required fields — all ingested."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._ingest_config()},
        )
        client.call_tool.return_value = _json_result({
            "items": [
                {"name": "A", "body": "a-body", "id": "1"},
                {"name": "B", "body": "b-body", "id": "2"},
                {"name": "C", "body": "c-body", "id": "3"},
            ]
        })
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["ingested_count"] == 3
        assert result.data["skipped_count"] == 0
        assert len(host.kb.text_calls) == 3

    @pytest.mark.asyncio
    async def test_collection_missing_field_skips_item(self) -> None:
        """Item missing a required mapped field is skipped with a warning."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._ingest_config()},
        )
        client.call_tool.return_value = _json_result({
            "items": [
                {"name": "A", "body": "a-body", "id": "1"},
                {"name": "B", "id": "2"},  # missing "body"
                {"name": "C", "body": "c-body", "id": "3"},
            ]
        })
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["ingested_count"] == 2
        assert result.data["skipped_count"] == 1
        assert result.warnings is not None
        assert any("body" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_collection_field_single_item(self) -> None:
        """Without collection_field, the entire response is treated as a single item."""
        cfg = self._ingest_config()
        del cfg["ingest"]["collection_field"]
        adapter, client = _make_adapter(tool_configs={"fetch": cfg})
        client.call_tool.return_value = _json_result({
            "name": "Single", "body": "single-body", "id": "s1",
        })
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["ingested_count"] == 1
        assert result.data["total_items"] == 1

    @pytest.mark.asyncio
    async def test_method_document_calls_ingest_document(self) -> None:
        """method='document' routes to host.kb.ingest_document instead of ingest_text."""
        cfg = self._ingest_config(method="document")
        cfg["ingest"]["field_mapping"]["filename"] = "file"
        adapter, client = _make_adapter(tool_configs={"fetch": cfg})
        client.call_tool.return_value = _json_result({
            "items": [{"name": "Doc", "body": "content", "id": "d1", "file": "doc.pdf"}],
        })
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["ingested_count"] == 1
        assert len(host.kb.doc_calls) == 1
        assert host.kb.doc_calls[0]["filename"] == "doc.pdf"
        assert len(host.kb.text_calls) == 0

    @pytest.mark.asyncio
    @patch("shu.plugins.base_adapter.logger")
    async def test_ingest_text_raises_increments_error_count(self, _mock_logger: Any) -> None:
        """A failed ingest_text call increments error_count without aborting the batch."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._ingest_config()},
        )
        client.call_tool.return_value = _json_result({
            "items": [
                {"name": "A", "body": "a-body", "id": "1"},
                {"name": "B", "body": "b-body", "id": "2"},
            ]
        })
        kb = FakeKb()
        call_count = 0

        original_ingest = kb.ingest_text

        async def failing_ingest(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("KB unavailable")
            return await original_ingest(*args, **kwargs)

        kb.ingest_text = failing_ingest  # type: ignore[assignment]
        host = FakeHost(kb=kb)

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["error_count"] == 1
        assert result.data["ingested_count"] == 1
        assert result.warnings is not None
        assert any("ingest failed" in w for w in result.warnings)


class TestDispatchErrors:
    """Dispatch validation: unknown and missing op params."""

    @pytest.mark.asyncio
    async def test_unknown_op(self) -> None:
        """An op not in tool_configs returns unknown_op error."""
        adapter, _ = _make_adapter(
            tool_configs={"search": {"enabled": True}},
        )

        result = await adapter.execute({"op": "nonexistent"}, _ctx(), None)

        assert result.status == "error"
        assert result.error["code"] == "unknown_op"

    @pytest.mark.asyncio
    async def test_missing_op(self) -> None:
        """Omitting the op param returns missing_op error."""
        adapter, _ = _make_adapter(
            tool_configs={"search": {"enabled": True}},
        )

        result = await adapter.execute({}, _ctx(), None)

        assert result.status == "error"
        assert result.error["code"] == "missing_op"


class TestIngestPagination:
    """Cursor-based pagination for ingest: multi-page fetching and cross-run cursor persistence."""

    def _paginated_config(self) -> dict[str, Any]:
        """Build an ingest config with cursor_field and cursor_param set."""
        return {
            "enabled": True,
            "chat_callable": False, "feed_eligible": True,
            "ingest": {
                "collection_field": "items",
                "method": "text",
                "field_mapping": {"title": "name", "content": "body", "source_id": "id"},
                "cursor_field": "next_cursor",
                "cursor_param": "cursor",
            },
        }

    @pytest.mark.asyncio
    async def test_pagination_loops_until_no_cursor(self) -> None:
        """Fetches page 1 (has cursor), then page 2 (no cursor) — ingests both and persists cursor."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._paginated_config()},
        )
        client.call_tool.side_effect = [
            _json_result({"items": [{"name": "A", "body": "a", "id": "1"}], "next_cursor": "page2"}),
            _json_result({"items": [{"name": "B", "body": "b", "id": "2"}]}),
        ]
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert result.data["ingested_count"] == 2
        assert result.data["total_items"] == 2
        assert client.call_tool.await_count == 2
        assert host.cursor.saved == "page2"

    @pytest.mark.asyncio
    async def test_pagination_passes_cursor_as_tool_argument(self) -> None:
        """The cursor from page 1's response is passed as the configured tool argument on page 2."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._paginated_config()},
        )
        client.call_tool.side_effect = [
            _json_result({"items": [{"name": "A", "body": "a", "id": "1"}], "next_cursor": "tok2"}),
            _json_result({"items": [{"name": "B", "body": "b", "id": "2"}]}),
        ]
        host = FakeHost()

        await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        # First call has no cursor argument
        first_call_args = client.call_tool.call_args_list[0]
        assert "cursor" not in (first_call_args[0][1] or {})
        # Second call passes the cursor from page 1
        second_call_args = client.call_tool.call_args_list[1]
        assert second_call_args[0][1]["cursor"] == "tok2"

    @pytest.mark.asyncio
    async def test_saved_cursor_used_on_next_run(self) -> None:
        """A cursor persisted from a previous feed run is loaded and passed on the first call."""
        adapter, client = _make_adapter(
            tool_configs={"fetch": self._paginated_config()},
        )
        client.call_tool.return_value = _json_result({
            "items": [{"name": "A", "body": "a", "id": "1"}],
        })
        host = FakeHost(cursor=FakeCursor(initial="saved-cursor"))

        await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        call_args = client.call_tool.call_args_list[0]
        assert call_args[0][1]["cursor"] == "saved-cursor"

    @pytest.mark.asyncio
    async def test_no_cursor_config_does_single_call(self) -> None:
        """Without cursor_field/cursor_param, only one call is made even if response has a cursor."""
        cfg = self._paginated_config()
        del cfg["ingest"]["cursor_field"]
        del cfg["ingest"]["cursor_param"]
        adapter, client = _make_adapter(tool_configs={"fetch": cfg})
        client.call_tool.return_value = _json_result({
            "items": [{"name": "A", "body": "a", "id": "1"}],
            "next_cursor": "should-be-ignored",
        })
        host = FakeHost()

        result = await adapter.execute({"op": "fetch", "__schedule_id": "sched-1"}, _ctx(), host)

        assert result.status == "success"
        assert client.call_tool.await_count == 1
        assert host.cursor.saved is None
