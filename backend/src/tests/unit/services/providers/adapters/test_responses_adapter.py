import json
import types
from unittest.mock import AsyncMock

import pytest
from shared import (
    OPENAI_ACTIONABLE_FUNCTION_CALL,
    OPENAI_ACTIONABLE_OUTPUT_DELTA,
    OPENAI_ACTIONABLE_REASONING_DELTA,
    OPENAI_ACTIONABLE_REASONING_ITEM,
    OPENAI_ACTIONABLE_RESPONSE_COMPLETE,
    OPENAI_COMPLETE_FUNCTION_CALL_PAYLOAD,
    OPENAI_COMPLETE_OUTPUT_PAYLOAD,
    OPENAI_IGNORED_FUNCTION_DELTA,
    OPENAI_IGNORED_RESPONSE_COMPLETE,
    TOOLS,
)

from shu.services.chat_types import ChatContext
from shu.services.providers import adapter_base
from shu.services.providers.adapter_base import (
    ProviderAdapterContext,
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderFinalEventResult,
    ProviderReasoningDeltaEventResult,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)
from shu.services.providers.adapters.responses_adapter import ResponsesAdapter

FAKE_PLUGIN_RESULT = {"ok": True}


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-responses",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def responses_adapter(mock_db_session, mock_provider):
    return ResponsesAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


@pytest.fixture(scope="function")
def patch_plugin_calls(monkeypatch):
    # Mock plugin execution to avoid real plugin calls.
    monkeypatch.setattr(
        adapter_base,
        "execute_plugin",
        AsyncMock(return_value=FAKE_PLUGIN_RESULT),
    )
    # SHU-759: _call_plugin now acquires its own session via
    # get_async_session_local() at the point of use. Patch the factory
    # to a do-nothing async context manager so this unit test doesn't
    # try to construct a real async engine — that fails in environments
    # where SHU_DATABASE_URL is configured for sync psycopg2 (CI).
    fake_session_cm = AsyncMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        adapter_base,
        "get_async_session_local",
        lambda: lambda: fake_session_cm,
    )


def _evaluate_tool_call_events(tool_event):
    """Ensure tool call messages are formatted and executed as expected."""
    assert isinstance(tool_event, ProviderToolCallEventResult)
    assert len(tool_event.tool_calls) == 1
    assert isinstance(tool_event.tool_calls[0], ToolCallInstructions)
    assert tool_event.tool_calls[0].plugin_name == "gmail_digest"
    assert tool_event.tool_calls[0].operation == "list"
    assert tool_event.tool_calls[0].args_dict == {
        "op": "list",
        "since_hours": 3360,
        "query_filter": "is:unread in:inbox",
        "max_results": 1,
        "preview": False,
    }
    assert tool_event.additional_messages[0].content == OPENAI_ACTIONABLE_REASONING_ITEM.get("item")
    assert tool_event.additional_messages[1].content == OPENAI_ACTIONABLE_FUNCTION_CALL.get("item")
    assert tool_event.additional_messages[2].metadata == {
        "type": "function_call_output",
        "call_id": OPENAI_ACTIONABLE_FUNCTION_CALL.get("item", {}).get("call_id", ""),
    }
    assert tool_event.additional_messages[2].content == str(json.dumps(FAKE_PLUGIN_RESULT))


def test_provider_defaults(responses_adapter):
    capabilities = responses_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is True

    assert responses_adapter.get_chat_endpoint() == "/responses"
    assert responses_adapter.get_models_endpoint() == "/models"
    assert responses_adapter.get_model_information_path() == "data[*].{id: id, name: id}"


@pytest.mark.asyncio
async def test_event_handling(responses_adapter, patch_plugin_calls):
    # We don't work with function call delta values, we grab the full values when they are ready.
    assert await responses_adapter.handle_provider_event(OPENAI_IGNORED_FUNCTION_DELTA) is None
    # The final result needs to have text in its output, this just has reasoning and function calls.
    assert await responses_adapter.handle_provider_event(OPENAI_IGNORED_RESPONSE_COMPLETE) is None

    # The adapter handles these reasoning items but stores them internally, we'll pick them up later.
    assert await responses_adapter.handle_provider_event(OPENAI_ACTIONABLE_REASONING_ITEM) is None
    # The adapter handles these function call items but stores them internally, we'll pick them up later.
    assert await responses_adapter.handle_provider_event(OPENAI_ACTIONABLE_FUNCTION_CALL) is None

    # This is standard output text delta information.
    event = await responses_adapter.handle_provider_event(OPENAI_ACTIONABLE_OUTPUT_DELTA)
    assert isinstance(event, ProviderContentDeltaEventResult), f"content delta: {event}"
    assert event.content == "at"

    # This is reasoning text delta information.
    event = await responses_adapter.handle_provider_event(OPENAI_ACTIONABLE_REASONING_DELTA)
    assert isinstance(event, ProviderReasoningDeltaEventResult), f"reasoning delta: {event}"
    assert event.content == "something"

    # This is the final event we are getting when processing is considered complete.
    event = await responses_adapter.handle_provider_event(OPENAI_ACTIONABLE_RESPONSE_COMPLETE)
    assert isinstance(event, ProviderFinalEventResult), f"final: {event}"
    assert event.content == "This is the full text."

    # Multi cycle stage from OPENAI_IGNORED_RESPONSE_COMPLETE + OPENAI_ACTIONABLE_RESPONSE_COMPLETE
    assert event.metadata.get("usage") == {
        "input_tokens": 4963 + 5887,
        "output_tokens": 689 + 395,
        "cached_tokens": 0 + 5632,
        "reasoning_tokens": 640 + 320,
        "total_tokens": 5652 + 6282,
    }

    events = await responses_adapter.finalize_provider_events()
    assert len(events) == 1

    _evaluate_tool_call_events(events[0])


@pytest.mark.asyncio
async def test_completion_flow(responses_adapter, patch_plugin_calls):
    events = await responses_adapter.handle_provider_completion(OPENAI_COMPLETE_FUNCTION_CALL_PAYLOAD)

    # Function-call-only responses produce no final message event (no text in output).
    assert len(events) == 1

    _evaluate_tool_call_events(events[0])

    events = await responses_adapter.handle_provider_completion(OPENAI_COMPLETE_OUTPUT_PAYLOAD)
    assert len(events) == 1

    final_event = events[0]
    assert isinstance(final_event, ProviderFinalEventResult), f"final: {final_event}"
    assert final_event.content == "This is the full text."

    """
    "usage": {
        "input_tokens": 4963,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 561,
        "output_tokens_details": {"reasoning_tokens": 512},
        "total_tokens": 5524,
    },

    "usage": {
        "input_tokens": 5777,
        "input_tokens_details": {"cached_tokens": 5504},
        "output_tokens": 376,
        "output_tokens_details": {"reasoning_tokens": 320},
        "total_tokens": 6153,
    },
    """

    # Multi cycle stage from OPENAI_COMPLETE_FUNCTION_CALL_PAYLOAD + OPENAI_COMPLETE_OUTPUT_PAYLOAD
    assert final_event.metadata.get("usage") == {
        "input_tokens": 4963 + 5777,
        "output_tokens": 561 + 376,
        "cached_tokens": 0 + 5504,
        "reasoning_tokens": 512 + 320,
        "total_tokens": 5524 + 6153,
    }


@pytest.mark.asyncio
async def test_inject_functions(responses_adapter):
    payload = {"field": "value"}
    messages = ChatContext.from_dicts([{"role": "user", "content": "content"}], "system prompt")

    payload = await responses_adapter.inject_model_parameter("model_name", payload)
    payload = await responses_adapter.inject_streaming_parameter(True, payload)
    payload = await responses_adapter.set_messages_in_payload(messages, payload)
    payload = await responses_adapter.inject_tool_payload(TOOLS, payload)

    assert payload == {
        "field": "value",
        "model": "model_name",
        "stream": True,
        "input": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "content"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "gmail_digest__list",
                "description": "List emails",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "since_hours": {"type": "integer", "minimum": 1, "maximum": 3360},
                        "query_filter": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                        "op": {"type": "string", "enum": ["list"]},
                        "message_ids": {"type": "array", "items": {"type": "string"}},
                        "preview": {"type": "boolean"},
                        "approve": {"type": "boolean"},
                        "kb_id": {
                            "type": "string",
                            "description": "Knowledge base ID to upsert digest KO into (required for op=digest)",
                        },
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
            },
            {
                "type": "function",
                "name": "calendar_events__list",
                "description": "Run calendar_events:list",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["list"]},
                        "calendar_id": {"type": "string"},
                        "since_hours": {"type": "integer", "minimum": 1, "maximum": 336},
                        "time_min": {"type": "string"},
                        "time_max": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 250},
                        "kb_id": {"type": "string"},
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_assistant_string_content_is_sent_as_bare_string(responses_adapter):
    """Prior-turn assistant text replays as a bare-string ``content`` value.

    Verified empirically (scripts/responses_replay_probe.sh) against OpenAI,
    OpenRouter, and DigitalOcean (non-Gemma) on /v1/responses, single- and
    multi-turn, with prior-turn context preserved.

    Wrapping the assistant text as an ``output_text`` content part with
    ``annotations: []`` (the c4eeb0f shape) breaks OpenRouter on turn 2+ with
    ``invalid_prompt``; wrapping as ``input_text`` breaks OpenAI on turn 2+
    with "Supported values are: 'output_text' and 'refusal'." Bare string is
    the only shape accepted across every conformant Responses provider.
    """
    messages = ChatContext.from_dicts(
        [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ],
        system_prompt=None,
    )

    payload = await responses_adapter.set_messages_in_payload(messages, {})

    assert payload["input"] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow up"},
    ]


@pytest.mark.asyncio
async def test_response_failed_event_emits_error(responses_adapter):
    """A `response.failed` SSE chunk surfaces as a ProviderErrorEventResult with the cause.

    Without this branch the chunk falls through to `return None`, the SSE
    iteration ends with no terminal event, and the chat-streaming loop
    raises a generic "NoFinalMessage" error that hides the real failure.
    """
    chunk = {
        "type": "response.failed",
        "response": {
            "error": {"code": "server_error", "message": "Backend is having a bad day"},
        },
    }
    result = await responses_adapter.handle_provider_event(chunk)
    assert isinstance(result, ProviderErrorEventResult)
    assert "Backend is having a bad day" in result.content


@pytest.mark.asyncio
async def test_top_level_error_event_emits_error(responses_adapter):
    """A top-level `error` SSE chunk surfaces as a ProviderErrorEventResult."""
    chunk = {
        "type": "error",
        "error": {"message": "rate limited", "type": "rate_limit"},
    }
    result = await responses_adapter.handle_provider_event(chunk)
    assert isinstance(result, ProviderErrorEventResult)
    assert "rate limited" in result.content


@pytest.mark.asyncio
async def test_response_completed_with_no_content_emits_error(responses_adapter):
    """`response.completed` with no text item and no streamed deltas emits an error.

    Previously this silently returned None and bubbled up as a generic
    NoFinalMessage. If tool calls were captured in this stream the
    follow-up path handles them, so this only fires when there's truly
    nothing actionable.
    """
    chunk = {
        "type": "response.completed",
        "response": {"output": [], "usage": {"input_tokens": 1, "output_tokens": 0}},
    }
    result = await responses_adapter.handle_provider_event(chunk)
    assert isinstance(result, ProviderErrorEventResult)
    assert "no text content" in result.content


@pytest.mark.asyncio
async def test_response_completed_with_only_reasoning_emits_error(responses_adapter):
    """`response.completed` with reasoning items but no function_call or text emits an error.

    Reasoning-only completions have no path to a final answer — no text to
    surface, and no function_call output for the follow-up cycle to bounce
    off of. Without this, the chat-streaming loop sees zero events and
    raises a generic NoFinalMessage that hides the real condition.
    """
    chunk = {
        "type": "response.completed",
        "response": {
            "id": "resp_reasoning_only",
            "output": [
                {"id": "rs_only", "type": "reasoning", "summary": []},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }
    result = await responses_adapter.handle_provider_event(chunk)
    assert isinstance(result, ProviderErrorEventResult)
    assert "no text content" in result.content


# SHU-803 AC9e: ResponsesAdapter.cancel() POSTs to /responses/{id}/cancel
# because OpenAI Responses keeps generating server-side after stream
# close (and billing continues) unless the cancel endpoint is hit. These
# tests pin the URL shape, return semantics, and AC9j logging behavior.


import logging  # noqa: E402

import httpx  # noqa: E402

from shu.services.providers.adapters.responses_adapter import (  # noqa: E402
    _reset_cancel_failure_tracking,
)


@pytest.fixture(scope="function")
def _reset_cancel_logging():
    """Ensure each cancel-failure-logging test starts from a clean slate.

    The module-level ``_CANCEL_FAILURE_LOGGED_PROVIDERS`` set tracks
    which provider IDs have already emitted an INFO log; without
    resetting between tests, the INFO-then-WARN escalation can't be
    asserted in isolation.
    """
    _reset_cancel_failure_tracking()
    yield
    _reset_cancel_failure_tracking()


@pytest.fixture(scope="function")
def mock_provider_with_id():
    """A mock provider with an ``id`` set (used by AC9j logging) and a
    ``config`` carrying the API base URL so the adapter's
    ``get_field_with_override("get_api_base_url")`` resolves without
    needing a concrete subclass."""
    return types.SimpleNamespace(
        id="test-provider-id",
        name="test-responses",
        api_key_encrypted=None,
        config={"get_api_base_url": "https://api.example.com/v1"},
    )


@pytest.fixture(scope="function")
def cancellable_adapter(mock_db_session, mock_provider_with_id):
    return ResponsesAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider_with_id,
            conversation_owner_id="unit-test-user",
        )
    )


def _make_mock_transport(handler):
    """Build an httpx.MockTransport that delegates each request to the
    test-supplied handler."""
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_cancel_returns_false_when_response_id_not_yet_seen(cancellable_adapter):
    """If terminate fires before `response.created` lands (extremely tight
    race), cancel() no-ops with False. Drain still runs and captures any
    eventual `response.completed` usage.
    """
    assert cancellable_adapter._response_id is None
    result = await cancellable_adapter.cancel()
    assert result is False
    # No transport should be installed for the no-op path.
    assert cancellable_adapter._cancel_transport is None


@pytest.mark.asyncio
async def test_response_created_event_captures_response_id(cancellable_adapter):
    """`response.created` is the first event in a Responses-API stream.
    The adapter must stash `response.id` so a subsequent `cancel()` can
    address the right in-flight run.
    """
    await cancellable_adapter.handle_provider_event(
        {"type": "response.created", "response": {"id": "resp_abc123"}}
    )
    assert cancellable_adapter._response_id == "resp_abc123"


@pytest.mark.asyncio
async def test_response_created_second_event_overwrites_id(cancellable_adapter):
    """A second ``response.created`` MUST refresh ``_response_id`` to the
    new id. The adapter instance is reused across tool-call follow-up
    turns in ``_stream_variant_phase`` — each turn opens a fresh stream
    and emits a new ``response.created``. If we kept the first id,
    ``cancel()`` on Stop during the second turn would POST to a stale
    /responses/{id}/cancel endpoint and the live stream would keep
    billing.
    """
    await cancellable_adapter.handle_provider_event(
        {"type": "response.created", "response": {"id": "resp_first"}}
    )
    await cancellable_adapter.handle_provider_event(
        {"type": "response.created", "response": {"id": "resp_second"}}
    )
    assert cancellable_adapter._response_id == "resp_second"


@pytest.mark.asyncio
async def test_cancel_posts_to_responses_cancel_endpoint_on_2xx(
    cancellable_adapter, _reset_cancel_logging
):
    """The load-bearing AC9e assertion: cancel POSTs to
    `{base_url}/responses/{response_id}/cancel` with the adapter's auth
    headers, and returns True when the upstream responds 2xx.
    """
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"cancelled": True})

    # Seed response_id and inject the mock transport — cancel() builds
    # its own ``httpx.AsyncClient`` inside an ``async with``, passing
    # ``self._cancel_transport`` as the client's transport.
    cancellable_adapter._response_id = "resp_xyz"
    cancellable_adapter._cancel_transport = _make_mock_transport(handler)

    result = await cancellable_adapter.cancel()

    assert result is True
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.method == "POST"
    # The path must end in /responses/{id}/cancel.
    assert str(request.url).endswith("/responses/resp_xyz/cancel")
    # Built atop the provider's configured base URL.
    assert str(request.url).startswith("https://api.example.com/v1/")


@pytest.mark.asyncio
async def test_cancel_returns_false_on_4xx_response(cancellable_adapter, _reset_cancel_logging):
    """Non-2xx responses are best-effort failures: the cancel was attempted
    but the provider didn't honor it (or doesn't support the endpoint).
    Returns False so the consumer loop falls through to drain.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cancellable_adapter._response_id = "resp_xyz"
    cancellable_adapter._cancel_transport = _make_mock_transport(handler)

    result = await cancellable_adapter.cancel()
    assert result is False


@pytest.mark.asyncio
async def test_cancel_returns_false_on_5xx_response(cancellable_adapter, _reset_cancel_logging):
    """Same shape as 4xx — upstream failure means drain takes over."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cancellable_adapter._response_id = "resp_xyz"
    cancellable_adapter._cancel_transport = _make_mock_transport(handler)

    result = await cancellable_adapter.cancel()
    assert result is False


@pytest.mark.asyncio
async def test_cancel_swallows_network_exceptions_and_returns_false(
    cancellable_adapter, _reset_cancel_logging
):
    """The cancel contract says implementations MUST NOT raise. A network
    failure (DNS, TLS, connection refused, timeout) is folded into a
    False return so `asyncio.gather(cancel_task, drain_task)` does not
    surface the cancel-side exception.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    cancellable_adapter._response_id = "resp_xyz"
    cancellable_adapter._cancel_transport = _make_mock_transport(handler)

    result = await cancellable_adapter.cancel()
    assert result is False


@pytest.mark.asyncio
async def test_cancel_returns_false_when_base_url_missing(cancellable_adapter, _reset_cancel_logging):
    """A mis-configured adapter (no base URL resolvable from
    get_field_with_override) returns False — we can't construct the
    cancel URL without the base, and we shouldn't crash the gather.
    """
    cancellable_adapter.provider.config = {"get_api_base_url": ""}
    cancellable_adapter._response_id = "resp_xyz"

    result = await cancellable_adapter.cancel()
    assert result is False


@pytest.mark.asyncio
async def test_ac9j_first_cancel_failure_logs_info_then_warn(
    cancellable_adapter, _reset_cancel_logging, caplog
):
    """AC9j: the first non-2xx from a given provider logs INFO; subsequent
    failures from the same provider escalate to WARN with a hint to
    configure as completions-shape.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cancellable_adapter._response_id = "resp_xyz"
    cancellable_adapter._cancel_transport = _make_mock_transport(handler)

    with caplog.at_level(logging.INFO, logger="shu.services.providers.adapters.responses_adapter"):
        await cancellable_adapter.cancel()  # First failure → INFO
        await cancellable_adapter.cancel()  # Second failure → WARN

    failure_records = [
        r for r in caplog.records if "ResponsesAdapter.cancel" in r.getMessage()
    ]
    assert len(failure_records) == 2
    assert failure_records[0].levelno == logging.INFO, (
        f"First failure should log INFO, got level {failure_records[0].levelname}"
    )
    assert failure_records[1].levelno == logging.WARNING, (
        f"Second failure should escalate to WARN, got level {failure_records[1].levelname}"
    )


@pytest.mark.asyncio
async def test_ac9j_two_different_providers_each_get_one_info(_reset_cancel_logging, caplog):
    """The INFO-then-WARN escalation is per-provider — two different
    providers each get their own first-time INFO without polluting each
    other's escalation state.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = _make_mock_transport(handler)

    adapter_a = ResponsesAdapter(
        ProviderAdapterContext(
            provider=types.SimpleNamespace(
                id="provider-A",
                name="A",
                api_key_encrypted=None,
                config={"get_api_base_url": "https://a.example.com"},
            )
        )
    )
    adapter_a._response_id = "resp_a"
    adapter_a._cancel_transport = transport

    adapter_b = ResponsesAdapter(
        ProviderAdapterContext(
            provider=types.SimpleNamespace(
                id="provider-B",
                name="B",
                api_key_encrypted=None,
                config={"get_api_base_url": "https://b.example.com"},
            )
        )
    )
    adapter_b._response_id = "resp_b"
    adapter_b._cancel_transport = transport

    with caplog.at_level(logging.INFO, logger="shu.services.providers.adapters.responses_adapter"):
        await adapter_a.cancel()
        await adapter_b.cancel()

    failure_records = [
        r for r in caplog.records if "ResponsesAdapter.cancel" in r.getMessage()
    ]
    assert len(failure_records) == 2
    assert all(r.levelno == logging.INFO for r in failure_records), (
        f"Both providers should INFO on first failure, got: "
        f"{[(r.name, r.levelname) for r in failure_records]}"
    )
