import json
import types
from unittest.mock import AsyncMock

import pytest
from tests.unit.services.providers.adapters.shared import (
    COMPLETIONS_ACTIONABLE_FUNCTION_CALL_DELTAS_PAYLOAD,
    COMPLETIONS_ACTIONABLE_OUTPUT_DELTA1,
    COMPLETIONS_ACTIONABLE_OUTPUT_DELTA2,
    COMPLETIONS_ACTIONABLE_OUTPUT_STOP,
    COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD,
    COMPLETIONS_COMPLETE_OUTPUT_PAYLOAD,
    COMPLETIONS_IGNORED_FUNCTION_CALL_COMPLETION_PAYLOAD,
    TOOLS,
)

from shu.services.chat_types import ChatContext
from shu.services.providers import adapter_base
from shu.services.providers.adapter_base import (
    ProviderAdapterContext,
    ProviderContentDeltaEventResult,
    ProviderFinalEventResult,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)
from shu.services.providers.adapters.completions_adapter import CompletionsAdapter

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
def completions_adapter(mock_db_session, mock_provider):
    return CompletionsAdapter(
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
        "query_filter": "in:inbox is:unread",
        "max_results": 50,
        "preview": True,
    }
    assert len(tool_event.additional_messages) == 2
    assert tool_event.additional_messages[0].role == "assistant"
    assert tool_event.additional_messages[0].metadata["tool_calls"] == [
        {
            "id": COMPLETIONS_ACTIONABLE_FUNCTION_CALL_DELTAS_PAYLOAD[0]
            .get("choices", [])[0]
            .get("delta", {})
            .get("tool_calls", [])[0]
            .get("id"),
            "type": "function",
            "function": {
                "name": "gmail_digest__list",
                "arguments": '{"op":"list","since_hours":3360,"query_filter":"in:inbox is:unread","max_results":50,"preview":true}',
            },
        }
    ]

    assert tool_event.additional_messages[1].role == "tool"
    assert tool_event.additional_messages[1].metadata[
        "tool_call_id"
    ] == COMPLETIONS_ACTIONABLE_FUNCTION_CALL_DELTAS_PAYLOAD[0].get("choices", [])[0].get("delta", {}).get(
        "tool_calls", []
    )[0].get("id")
    assert tool_event.additional_messages[1].content == json.dumps(FAKE_PLUGIN_RESULT)


def test_provider_defaults(completions_adapter):
    capabilities = completions_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is True

    assert completions_adapter.get_chat_endpoint() == "/chat/completions"
    assert completions_adapter.get_models_endpoint() == "/models"
    assert completions_adapter.get_model_information_path() == "data[*].{id: id, name: id}"


@pytest.mark.asyncio
async def test_event_handling(completions_adapter, patch_plugin_calls):
    for payload in COMPLETIONS_ACTIONABLE_FUNCTION_CALL_DELTAS_PAYLOAD:
        await completions_adapter.handle_provider_event(payload)

    await completions_adapter.handle_provider_event(COMPLETIONS_IGNORED_FUNCTION_CALL_COMPLETION_PAYLOAD)

    events = await completions_adapter.finalize_provider_events()
    assert len(events) == 2
    _evaluate_tool_call_events(events[0])
    assert isinstance(events[1], ProviderFinalEventResult)
    assert not events[1].content

    event = await completions_adapter.handle_provider_event(COMPLETIONS_ACTIONABLE_OUTPUT_DELTA1)
    assert isinstance(event, ProviderContentDeltaEventResult)
    assert event.content == "This is the first part.\n"

    event = await completions_adapter.handle_provider_event(COMPLETIONS_ACTIONABLE_OUTPUT_DELTA2)
    assert isinstance(event, ProviderContentDeltaEventResult)
    assert event.content == "This is the second part."

    event = await completions_adapter.handle_provider_event(COMPLETIONS_ACTIONABLE_OUTPUT_STOP)
    assert event is None

    events = await completions_adapter.finalize_provider_events()
    assert len(events) == 1
    assert isinstance(events[0], ProviderFinalEventResult)
    assert events[0].content == "This is the first part.\nThis is the second part."

    # Multi cycle stage from COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD + COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD
    assert events[0].metadata.get("usage") == {
        "input_tokens": 885 + 1226,
        "output_tokens": 500 + 651,
        "cached_tokens": 0 + 100,
        "reasoning_tokens": 448 + 512,
        "total_tokens": 1385 + 1877,
    }


@pytest.mark.asyncio
async def test_completion_flow(completions_adapter, patch_plugin_calls):
    events = await completions_adapter.handle_provider_completion(COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD)

    # We get two events here, the first one is the tool call, the second is an empty final message.
    # Empty final messages are ignored.
    assert len(events) == 2

    _evaluate_tool_call_events(events[0])

    final_event = events[1]
    assert final_event.content is None  # empty events are ignored

    events = await completions_adapter.handle_provider_completion(COMPLETIONS_COMPLETE_OUTPUT_PAYLOAD)
    assert len(events) == 1

    final_event = events[0]
    assert isinstance(final_event, ProviderFinalEventResult), f"final: {final_event}"
    assert final_event.content == "This is the first part.\nThis is the second part."

    # Multi cycle stage from COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD + COMPLETIONS_COMPLETE_OUTPUT_PAYLOAD
    assert final_event.metadata.get("usage") == {
        "input_tokens": 662 + 623,
        "output_tokens": 560 + 95,
        "cached_tokens": 0 + 100,
        "reasoning_tokens": 512 + 64,
        "total_tokens": 1222 + 718,
    }


@pytest.mark.asyncio
async def test_inject_functions(completions_adapter):
    payload = {"field": "value"}
    messages = ChatContext.from_dicts([{"role": "user", "content": "content"}], "system prompt")

    payload = await completions_adapter.inject_model_parameter("model_name", payload)
    payload = await completions_adapter.inject_streaming_parameter(True, payload)
    payload = await completions_adapter.set_messages_in_payload(messages, payload)
    payload = await completions_adapter.inject_tool_payload(TOOLS, payload)

    assert payload == {
        "field": "value",
        "model": "model_name",
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "content"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "gmail_digest__list",
                "description": "List emails",
                "function": {
                    "name": "gmail_digest__list",
                    "description": "List emails",
                    "parameters": {
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "type": "object",
                        "properties": {
                            "since_hours": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 3360,
                                "default": 48,
                                "x-ui": {
                                    "help": "Look-back window in hours; used to build newer_than:Xd when query_filter is empty."
                                },
                            },
                            "query_filter": {
                                "type": ["string", "null"],
                                "x-ui": {
                                    "help": "Gmail search query (e.g., from:me is:unread). Requires appropriate Gmail read access. Leave blank to use newer_than derived from since_hours."
                                },
                            },
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 500,
                                "default": 50,
                                "x-ui": {"help": "Max messages to inspect (capped at 500)."},
                            },
                            "op": {
                                "type": "string",
                                "enum": ["list"],
                                "const": "list",
                                "default": "list",
                            },
                            "message_ids": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                                "x-ui": {"help": "For actions, provide Gmail message ids to modify."},
                            },
                            "preview": {
                                "type": ["boolean", "null"],
                                "default": None,
                                "x-ui": {"help": "When true with approve=false, returns a plan without side effects."},
                            },
                            "approve": {
                                "type": ["boolean", "null"],
                                "default": None,
                                "x-ui": {"help": "Set to true (with or without preview) to perform the action."},
                            },
                            "kb_id": {
                                "type": ["string", "null"],
                                "description": "Knowledge base ID to upsert digest KO into (required for op=digest)",
                                "x-ui": {
                                    "hidden": True,
                                    "help": "Target Knowledge Base for digest output.",
                                },
                            },
                        },
                        "required": ["op"],
                        "additionalProperties": True,
                    },
                },
            },
            {
                "type": "function",
                "name": "calendar_events__list",
                "description": "Run calendar_events:list",
                "function": {
                    "name": "calendar_events__list",
                    "description": "Run calendar_events:list",
                    "parameters": {
                        "$schema": "http://json-schema.org/draft-07/schema#",
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["list"],
                                "const": "list",
                                "default": "list",
                            },
                            "calendar_id": {
                                "type": ["string", "null"],
                                "default": "primary",
                                "x-ui": {"help": "Calendar ID (default: primary)"},
                            },
                            "since_hours": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 336,
                                "default": 48,
                                "x-ui": {"help": "Look-back window in hours when no syncToken is present."},
                            },
                            "time_min": {
                                "type": ["string", "null"],
                                "x-ui": {"help": "ISO timeMin override (UTC)."},
                            },
                            "time_max": {
                                "type": ["string", "null"],
                                "x-ui": {"help": "ISO timeMax override (UTC)."},
                            },
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 250,
                                "default": 50,
                            },
                            "kb_id": {
                                "type": ["string", "null"],
                                "x-ui": {"hidden": True},
                            },
                        },
                        "required": ["op"],
                        "additionalProperties": True,
                    },
                },
            },
        ],
    }
