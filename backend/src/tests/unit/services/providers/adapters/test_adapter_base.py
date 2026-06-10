"""Tests for BaseProviderAdapter's tool-dispatch fast-path (SHU-816)."""

import json
import types
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import (
    BaseProviderAdapter,
    ProviderAdapterContext,
)


@pytest.fixture(scope="function")
def adapter(monkeypatch):
    """Build a bare BaseProviderAdapter for fast-path testing.

    Also stubs out the usage recorder so tests don't try to write to the
    DB. Individual tests that want to inspect the recorded row can rebind
    the stub via ``monkeypatch``.
    """
    ctx = ProviderAdapterContext(
        provider=types.SimpleNamespace(
            id="prov-1", name="t", api_key_encrypted=None, config={}
        ),
        conversation_owner_id="u1",
    )
    a = BaseProviderAdapter(ctx)

    # Default no-op usage recorder; tests can swap in a spy.
    default_recorder = types.SimpleNamespace(record=AsyncMock())
    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_usage_recorder",
        lambda: default_recorder,
    )
    a._test_default_recorder = default_recorder
    return a


def test_internal_tool_router_property_is_lazy(adapter):
    # Constructing the adapter must NOT eagerly build the router.
    assert adapter._internal_tool_router is None
    # First access constructs it.
    router = adapter.internal_tool_router
    assert router is not None
    # Repeated access returns the same instance — it is cached.
    assert adapter.internal_tool_router is router


@pytest.mark.asyncio
async def test_call_plugin_routes_int_namespace_to_internal_router(adapter, monkeypatch):
    # The model's tool_call name `int__web_search` is parsed by
    # _tool_call_to_instructions into plugin_name="int", operation="web_search".
    # _call_plugin's fast-path delegates the "is this internal?" decision
    # to the router and dispatches with `operation` as the bare op.
    spy = AsyncMock(return_value=("router-result", False, Decimal("0.005")))
    monkeypatch.setattr(adapter.internal_tool_router, "execute", spy)

    result = await adapter._call_plugin("int", "web_search", {"query": "anything"})

    # _call_plugin returns just the content string (not the tuple).
    assert result == "router-result"
    spy.assert_awaited_once_with("web_search", {"query": "anything"})


@pytest.mark.asyncio
async def test_call_plugin_records_internal_tool_usage_on_success(adapter, monkeypatch):
    # Successful tool call → record_usage called with success=True, cost
    # forwarded, tool_name in request_metadata.
    monkeypatch.setattr(
        adapter.internal_tool_router,
        "execute",
        AsyncMock(return_value=("result", False, Decimal("0.005"))),
    )
    record_spy = AsyncMock()
    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_usage_recorder",
        lambda: types.SimpleNamespace(record=record_spy),
    )

    await adapter._call_plugin("int", "web_search", {"query": "x"})

    record_spy.assert_awaited_once()
    kwargs = record_spy.await_args.kwargs
    assert kwargs["provider_id"] == "prov-1"
    assert kwargs["model_id"] is None
    assert kwargs["user_id"] == "u1"
    assert kwargs["request_type"] == "internal_tool"
    assert kwargs["total_cost"] == Decimal("0.005")
    assert kwargs["success"] is True
    assert kwargs["error_message"] is None
    assert kwargs["request_metadata"] == {"tool_name": "int__web_search"}


@pytest.mark.asyncio
async def test_call_plugin_records_zero_cost_success(adapter, monkeypatch):
    # Regression for SHU-816 H2: a successful tool that legitimately
    # costs zero (free upstream, or operator set cost_per_query=0) must
    # still record success=True with no error_message — not failure with
    # the tool's output dumped into error_message.
    monkeypatch.setattr(
        adapter.internal_tool_router,
        "execute",
        AsyncMock(return_value=('{"web": []}', False, Decimal("0"))),
    )
    record_spy = AsyncMock()
    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_usage_recorder",
        lambda: types.SimpleNamespace(record=record_spy),
    )

    await adapter._call_plugin("int", "web_search", {"query": "x"})

    kwargs = record_spy.await_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["error_message"] is None
    assert kwargs["total_cost"] == Decimal("0")


@pytest.mark.asyncio
async def test_call_plugin_records_internal_tool_usage_on_failure(adapter, monkeypatch):
    # Failed tool call → record_usage called with success=False,
    # total_cost=0, error_message populated from the tool's returned
    # content. The user is not billed.
    monkeypatch.setattr(
        adapter.internal_tool_router,
        "execute",
        AsyncMock(return_value=("web search failed: HTTP 429 from Brave", True, Decimal("0"))),
    )
    record_spy = AsyncMock()
    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_usage_recorder",
        lambda: types.SimpleNamespace(record=record_spy),
    )

    await adapter._call_plugin("int", "web_search", {"query": "x"})

    kwargs = record_spy.await_args.kwargs
    assert kwargs["total_cost"] == Decimal("0")
    assert kwargs["success"] is False
    assert kwargs["error_message"] == "web search failed: HTTP 429 from Brave"


@pytest.mark.asyncio
async def test_call_plugin_skips_usage_recording_when_no_provider(monkeypatch):
    # Without a provider on the context, there's no FK target for the
    # usage row — _record_internal_tool_usage early-returns silently.
    # The model still gets its result; we just don't write a billing row.
    ctx = ProviderAdapterContext(provider=None, conversation_owner_id="u1")
    a = BaseProviderAdapter(ctx)

    monkeypatch.setattr(
        a.internal_tool_router,
        "execute",
        AsyncMock(return_value=("result", False, Decimal("0.005"))),
    )
    record_spy = AsyncMock()
    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_usage_recorder",
        lambda: types.SimpleNamespace(record=record_spy),
    )

    result = await a._call_plugin("int", "web_search", {"query": "x"})

    assert result == "result"
    record_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_plugin_falls_through_for_non_int_names(adapter, monkeypatch):
    # The router must NOT be touched for plugin-style names. Stub the
    # downstream `execute_plugin` so we don't need a real plugin.
    router_spy = AsyncMock()
    monkeypatch.setattr(adapter.internal_tool_router, "execute", router_spy)

    async def fake_execute_plugin(session, plugin_name, operation, args_dict, owner_id):
        return {"plugin": plugin_name, "op": operation, "args": args_dict, "user": owner_id}

    monkeypatch.setattr(
        "shu.services.providers.adapter_base.execute_plugin",
        fake_execute_plugin,
    )

    # `_call_plugin` opens a short-lived session via `get_async_session_local`.
    # Stub that to an async-context-manager mock.
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    def _fake_factory():
        return _FakeSession()

    monkeypatch.setattr(
        "shu.services.providers.adapter_base.get_async_session_local",
        lambda: _fake_factory,
    )

    result = await adapter._call_plugin("gmail", "send", {"to": "a@b"})

    # Internal router was not called for the non-int name.
    router_spy.assert_not_awaited()
    # The result is a JSON string from the existing plugin path.
    payload = json.loads(result)
    assert payload["plugin"] == "gmail"
    assert payload["op"] == "send"
    assert payload["args"] == {"to": "a@b"}


@pytest.mark.asyncio
async def test_call_plugin_int_namespace_skips_kb_enrichment(adapter, monkeypatch):
    # When knowledge_base_ids is set, plugin-routed calls get __host.kb
    # merged into args_dict. Internal tools must skip that step entirely.
    adapter.knowledge_base_ids = ["kb-1"]

    captured = {}

    async def fake_execute(bare_op, args):
        captured["args"] = args
        return ("ok", False, Decimal("0"))

    monkeypatch.setattr(adapter.internal_tool_router, "execute", fake_execute)

    await adapter._call_plugin("int", "web_search", {"query": "anything"})

    # The args dict passed to the router must NOT have __host injected.
    assert "__host" not in captured["args"]
    assert captured["args"] == {"query": "anything"}
