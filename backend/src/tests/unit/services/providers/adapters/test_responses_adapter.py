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

    # We get two events here, the first one is the tool call, the second is an empty final message.
    # Empty final messages are ignored.
    assert len(events) == 2

    _evaluate_tool_call_events(events[0])

    final_event = events[1]
    assert final_event.content == []  # empty events are ignored

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
