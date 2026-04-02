"""Tests for BasePluginAdapter.

Covers dispatch logic, schema generation, chat-callable execution,
ingest execution with pagination and cursor persistence, field mapping,
collection extraction, cursor load/save, and structured logging.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from shu.plugins.base import ExecuteContext, PluginResult
from shu.plugins.base_adapter import BasePluginAdapter


class _ChatResult:
    """Minimal object with a .content attribute for chat-callable tests."""

    def __init__(self, content: Any) -> None:
        self.content = content


class StubAdapter(BasePluginAdapter):
    """Concrete adapter for testing base class logic."""

    def __init__(
        self,
        tool_configs: dict[str, Any] | None = None,
        discovered_tools: list[dict[str, Any]] | None = None,
        name: str = "stub:test",
        version: str = "1.0",
        settings: Any | None = None,
    ) -> None:
        super().__init__(
            name=name,
            version=version,
            tool_configs=tool_configs,
            discovered_tools=discovered_tools,
            settings=settings,
        )
        self.call_tool_returns: Any = _ChatResult("ok")
        self.call_tool_calls: list[tuple[str, dict[str, Any]]] = []

    async def _call_tool(self, op: str, params: dict[str, Any]) -> Any:
        self.call_tool_calls.append((op, params))
        ret = self.call_tool_returns
        if callable(ret) and not isinstance(ret, PluginResult):
            return ret()
        return ret

    def _assemble_response_data(self, raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            return raw_result
        return {"content": raw_result}


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
        if cursor is not None:
            self.cursor = cursor


class FakeHostWithCursor(FakeHost):
    def __init__(self, kb: FakeKb | None = None, cursor: FakeCursor | None = None) -> None:
        super().__init__(kb=kb, cursor=cursor or FakeCursor())


class FakeSettings:
    max_pagination_limit = 1000


def _ctx() -> ExecuteContext:
    return ExecuteContext(user_id="u1")


def _adapter(
    tool_configs: dict[str, Any] | None = None,
    discovered_tools: list[dict[str, Any]] | None = None,
) -> StubAdapter:
    return StubAdapter(
        tool_configs=tool_configs,
        discovered_tools=discovered_tools,
        settings=FakeSettings(),
    )


def _ingest_tool_config(
    *,
    chat_callable: bool = True,
    feed_eligible: bool = True,
    collection_field: str | None = "items",
    cursor_field: str | None = None,
    cursor_param: str | None = None,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "chat_callable": chat_callable,
        "feed_eligible": feed_eligible,
        "ingest": {
            "field_mapping": {
                "title": "name",
                "content": "body",
                "source_id": "id",
            },
            "collection_field": collection_field,
            "method": "text",
            "cursor_field": cursor_field,
            "cursor_param": cursor_param,
        },
    }


class TestExecuteDispatch:
    """Tests for the execute() dispatch logic."""

    @pytest.mark.asyncio
    async def test_missing_op_returns_error(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True}})
        result = await adapter.execute({}, _ctx(), FakeHost())
        assert result.status == "error"
        assert result.error["code"] == "missing_op"

    @pytest.mark.asyncio
    async def test_unknown_op_returns_error(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True}})
        result = await adapter.execute({"op": "nonexistent"}, _ctx(), FakeHost())
        assert result.status == "error"
        assert result.error["code"] == "unknown_op"

    @pytest.mark.asyncio
    async def test_disabled_op_returns_unknown_op(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": False}})
        result = await adapter.execute({"op": "fetch"}, _ctx(), FakeHost())
        assert result.status == "error"
        assert result.error["code"] == "unknown_op"

    @pytest.mark.asyncio
    async def test_chat_callable_tool_dispatches(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True, "chat_callable": True}})
        adapter.call_tool_returns = _ChatResult("hello")
        result = await adapter.execute({"op": "fetch"}, _ctx(), FakeHost())
        assert result.status == "success"
        assert result.data == {"result": "hello"}
        assert len(adapter.call_tool_calls) == 1

    @pytest.mark.asyncio
    async def test_feed_run_dispatches_to_ingest(self) -> None:
        cfg = _ingest_tool_config()
        adapter = _adapter(tool_configs={"fetch": cfg})
        adapter.call_tool_returns = {"items": [{"name": "t", "body": "b", "id": "1"}]}
        result = await adapter.execute(
            {"op": "fetch", "__schedule_id": "s1"},
            _ctx(),
            FakeHostWithCursor(),
        )
        assert result.status == "success"
        assert result.data["ingested_count"] == 1

    @pytest.mark.asyncio
    async def test_not_chat_callable_returns_error(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True, "chat_callable": False}})
        result = await adapter.execute({"op": "fetch"}, _ctx(), FakeHost())
        assert result.status == "error"
        assert result.error["code"] == "not_chat_callable"


class TestGetSchemaForOp:
    def test_disabled_op_returns_none(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": False}})
        assert adapter.get_schema_for_op("fetch") is None

    def test_enabled_op_with_input_schema(self) -> None:
        adapter = _adapter(
            tool_configs={"fetch": {"enabled": True}},
            discovered_tools=[
                {
                    "name": "fetch",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    "description": "Fetch data",
                }
            ],
        )
        schema = adapter.get_schema_for_op("fetch")
        assert schema is not None
        assert "title" in schema
        assert schema["description"] == "Fetch data"
        assert "properties" in schema

    def test_op_with_no_input_schema_returns_default(self) -> None:
        adapter = _adapter(
            tool_configs={"fetch": {"enabled": True}},
            discovered_tools=[{"name": "fetch"}],
        )
        schema = adapter.get_schema_for_op("fetch")
        assert schema is not None
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is True


class TestGetOutputSchema:
    def test_always_returns_none(self) -> None:
        adapter = _adapter()
        assert adapter.get_output_schema() is None


class TestExecuteChatCallable:
    @pytest.mark.asyncio
    async def test_success_returns_ok_with_result(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True}})
        adapter.call_tool_returns = _ChatResult({"key": "value"})
        result = await adapter._execute_chat_callable("fetch", {"op": "fetch"})
        assert result.status == "success"
        assert result.data == {"result": {"key": "value"}}

    @pytest.mark.asyncio
    async def test_plugin_result_error_passes_through(self) -> None:
        adapter = _adapter(tool_configs={"fetch": {"enabled": True}})
        adapter.call_tool_returns = PluginResult.err("boom", code="tool_error")
        result = await adapter._execute_chat_callable("fetch", {"op": "fetch"})
        assert result.status == "error"
        assert result.error["code"] == "tool_error"


class TestExecuteIngest:
    @pytest.mark.asyncio
    async def test_single_page_ingest(self) -> None:
        cfg = _ingest_tool_config()
        adapter = _adapter(tool_configs={"fetch": cfg})
        adapter.call_tool_returns = {
            "items": [
                {"name": "Title1", "body": "Content1", "id": "src1"},
                {"name": "Title2", "body": "Content2", "id": "src2"},
            ]
        }
        host = FakeHostWithCursor()
        result = await adapter._execute_ingest("fetch", {"op": "fetch"}, cfg, host)
        assert result.status == "success"
        assert result.data["ingested_count"] == 2
        assert result.data["total_items"] == 2
        assert len(host.kb.text_calls) == 2
        assert host.kb.text_calls[0]["title"] == "Title1"

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self) -> None:
        cfg = _ingest_tool_config(cursor_field="next_cursor", cursor_param="cursor")
        adapter = _adapter(tool_configs={"fetch": cfg})
        pages = [
            {"items": [{"name": "P1", "body": "B1", "id": "1"}], "next_cursor": "page2"},
            {"items": [{"name": "P2", "body": "B2", "id": "2"}], "next_cursor": None},
        ]
        page_idx = {"i": 0}

        def next_page():
            result = pages[page_idx["i"]]
            page_idx["i"] += 1
            return result

        adapter.call_tool_returns = next_page
        cursor = FakeCursor()
        host = FakeHostWithCursor(cursor=cursor)
        result = await adapter._execute_ingest("fetch", {"op": "fetch"}, cfg, host)
        assert result.status == "success"
        assert result.data["ingested_count"] == 2
        assert result.data["total_items"] == 2
        assert cursor.saved == "page2"

    @pytest.mark.asyncio
    async def test_reset_cursor_ignores_saved(self) -> None:
        cfg = _ingest_tool_config(cursor_field="next_cursor", cursor_param="cursor")
        adapter = _adapter(tool_configs={"fetch": cfg})
        adapter.call_tool_returns = {"items": [{"name": "T", "body": "B", "id": "1"}], "next_cursor": None}
        cursor = FakeCursor(initial="old_cursor")
        host = FakeHostWithCursor(cursor=cursor)
        result = await adapter._execute_ingest(
            "fetch", {"op": "fetch", "reset_cursor": True}, cfg, host
        )
        assert result.status == "success"
        assert len(adapter.call_tool_calls) == 1
        call_params = adapter.call_tool_calls[0][1]
        assert "cursor" not in call_params

    @pytest.mark.asyncio
    async def test_missing_ingest_config_returns_error(self) -> None:
        cfg = {"enabled": True, "feed_eligible": True}
        adapter = _adapter(tool_configs={"fetch": cfg})
        host = FakeHostWithCursor()
        result = await adapter._execute_ingest("fetch", {"op": "fetch"}, cfg, host)
        assert result.status == "error"
        assert result.error["code"] == "missing_ingest_config"

    @pytest.mark.asyncio
    async def test_missing_kb_id_returns_error(self) -> None:
        cfg = _ingest_tool_config()
        adapter = _adapter(tool_configs={"fetch": cfg})

        class EmptyKbHost:
            kb = None

        host = EmptyKbHost()
        result = await adapter._execute_ingest("fetch", {"op": "fetch"}, cfg, host)
        assert result.status == "error"
        assert result.error["code"] == "no_knowledge_base"


class TestMapFields:
    def _adapter(self) -> StubAdapter:
        return _adapter()

    def test_maps_required_fields(self) -> None:
        adapter = self._adapter()
        mapping = {"title": "name", "content": "body", "source_id": "id"}
        item = {"name": "My Title", "body": "My Content", "id": "src-1"}
        warnings: list[str] = []
        result = adapter._map_fields(item, mapping, 0, warnings)
        assert result is not None
        assert result["title"] == "My Title"
        assert result["content"] == "My Content"
        assert result["source_id"] == "src-1"
        assert not warnings

    def test_maps_optional_fields(self) -> None:
        adapter = self._adapter()
        mapping = {
            "title": "name",
            "content": "body",
            "source_id": "id",
            "source_url": "url",
            "filename": "file",
            "mime_type": "type",
        }
        item = {"name": "T", "body": "C", "id": "1", "url": "http://x", "file": "a.txt", "type": "text/plain"}
        warnings: list[str] = []
        result = adapter._map_fields(item, mapping, 0, warnings)
        assert result is not None
        assert result["source_url"] == "http://x"
        assert result["filename"] == "a.txt"
        assert result["mime_type"] == "text/plain"

    def test_returns_none_when_required_path_not_found(self) -> None:
        adapter = self._adapter()
        mapping = {"title": "name", "content": "missing_path", "source_id": "id"}
        item = {"name": "T", "id": "1"}
        warnings: list[str] = []
        result = adapter._map_fields(item, mapping, 0, warnings)
        assert result is None
        assert any("not found at path" in w for w in warnings)

    def test_returns_none_when_field_mapping_missing(self) -> None:
        adapter = self._adapter()
        mapping = {"title": "name", "source_id": "id"}
        item = {"name": "T", "id": "1"}
        warnings: list[str] = []
        result = adapter._map_fields(item, mapping, 0, warnings)
        assert result is None
        assert any("field mapping missing" in w for w in warnings)


class TestExtractItems:
    def _adapter(self) -> StubAdapter:
        return _adapter()

    def test_no_collection_field_returns_whole_data(self) -> None:
        adapter = self._adapter()
        data = {"key": "val"}
        result = adapter._extract_items(data, None)
        assert result == [data]

    def test_valid_collection_field_returns_list(self) -> None:
        adapter = self._adapter()
        data = {"items": [{"a": 1}, {"b": 2}]}
        result = adapter._extract_items(data, "items")
        assert len(result) == 2
        assert result[0] == {"a": 1}

    def test_invalid_collection_field_returns_whole_data(self) -> None:
        adapter = self._adapter()
        data = {"items": "not_a_list"}
        result = adapter._extract_items(data, "items")
        assert result == [data]


class TestLoadCursor:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_cursor_attr(self) -> None:
        adapter = _adapter()
        host = FakeHost()  # no cursor attribute
        result = await adapter._load_cursor(host, "kb1")
        assert result is None

    @pytest.mark.asyncio
    async def test_loads_from_host_cursor(self) -> None:
        adapter = _adapter()
        cursor = FakeCursor(initial="saved_val")
        host = FakeHostWithCursor(cursor=cursor)
        result = await adapter._load_cursor(host, "kb1")
        assert result == "saved_val"


class TestSaveCursor:
    @pytest.mark.asyncio
    async def test_save_returns_true_on_success(self) -> None:
        adapter = _adapter()
        cursor = FakeCursor()
        host = FakeHostWithCursor(cursor=cursor)
        result = await adapter._save_cursor(host, "kb1", "new_val")
        assert result is True
        assert cursor.saved == "new_val"

    @pytest.mark.asyncio
    async def test_save_returns_false_when_no_cursor(self) -> None:
        adapter = _adapter()
        host = FakeHost()  # no cursor attribute
        result = await adapter._save_cursor(host, "kb1", "val")
        assert result is False


class TestLogToolCall:
    def test_logs_structured_event(self) -> None:
        adapter = _adapter()
        with patch("shu.plugins.base_adapter.logger") as mock_logger:
            adapter._log_tool_call(
                op="fetch",
                start=0.0,
                status="success",
                result_size=42,
                code=None,
                error=None,
            )
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert "plugin.tool_call" in call_args[0][0]
            assert "fetch" in call_args[0][1] or "fetch" in str(call_args)
