import json
import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers import adapter_base
from shu.services.providers.adapter_base import (
    ProviderAdapterContext,
    ProviderContentDeltaEventResult,
    ProviderFinalEventResult,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)
from shu.services.chat_types import ChatContext
from shu.services.providers.adapters.anthropic_adapter import AnthropicAdapter

from shared import (
    ANTHROPIC_ACTIONABLE_FUNCTION_CALL1,
    ANTHROPIC_ACTIONABLE_FUNCTION_CALL2,
    ANTHROPIC_ACTIONABLE_FUNCTION_CALL3,
    ANTHROPIC_ACTIONABLE_FUNCTION_CALL4,
    ANTHROPIC_ACTIONABLE_FUNCTION_CALL5,
    ANTHROPIC_ACTIONABLE_MESSAGE_STOP,
    ANTHROPIC_ACTIONABLE_OUTPUT_DELTA1,
    ANTHROPIC_ACTIONABLE_OUTPUT_DELTA2,
    ANTHROPIC_ACTIONABLE_OUTPUT_STOP,
    ANTHROPIC_IGNORED_START,
    ANTRHOPIC_ACTIONABLE_FUNCTION_CALL_STOP,
    ANTHROPIC_COMPLETE_FUNCTION_CALL_PAYLOAD,
    ANTHROPIC_COMPLETE_OUTPUT_PAYLOAD,
    TOOLS,
)
FAKE_PLUGIN_RESULT = {"ok": True}


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-anthropic",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def anthropic_adapter(mock_db_session, mock_provider):
    return AnthropicAdapter(
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
    """
    Ensure that tool call messages are formatted the way they should be, and execute plugins according to the tool call standard.
    """

    assert isinstance(tool_event, ProviderToolCallEventResult)
    assert len(tool_event.tool_calls) == 1

    assert isinstance(tool_event.tool_calls[0], ToolCallInstructions)
    assert tool_event.tool_calls[0].plugin_name == "gmail_digest"
    assert tool_event.tool_calls[0].operation == "list"
    assert tool_event.tool_calls[0].args_dict == {"op": "digest"}

    # Assistant message with tool_use (text may or may not be present), followed by tool_result.
    assert len(tool_event.additional_messages) == 2
    assert tool_event.additional_messages[0].role == "assistant"
    assert tool_event.additional_messages[0].content == [
            {"type": "text", "text": "This is the first part.\nThis is the second part."},
            {"type": "tool_use", "id": "toolu_01P8Dmpo2vu2vZpdyKyhmQPA", "name": "gmail_digest__list", "input": {"op": "digest"}}
        ]

    result_msg = tool_event.additional_messages[1]
    assert result_msg.role == "user"
    assert result_msg.content == [
            {"type": "tool_result", "tool_use_id": "toolu_01P8Dmpo2vu2vZpdyKyhmQPA", "content": json.dumps(FAKE_PLUGIN_RESULT)}
        ]


def test_provider_settings(anthropic_adapter):

    info = anthropic_adapter.get_provider_information()
    assert info.key == "anthropic"
    assert info.display_name == "Anthropic"

    capabilities = anthropic_adapter.get_capabilities()
    assert capabilities.streaming == True
    assert capabilities.tools == True
    assert capabilities.vision == True

    assert anthropic_adapter.get_api_base_url() == "https://api.anthropic.com/v1"
    assert anthropic_adapter.get_chat_endpoint() == "/messages"
    assert anthropic_adapter.get_models_endpoint() == "/models"

    authorization_header = anthropic_adapter.get_authorization_header()
    assert authorization_header.get("headers", {}).get("x-api-key") == f"{None}"  # We're not testing decryption

    # TODO: We'll want to evaluate the mapping in the future to ensure it is valid.
    parameter_mapping = anthropic_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)


@pytest.mark.asyncio
async def test_event_handling(anthropic_adapter, patch_plugin_calls):

    await anthropic_adapter.handle_provider_event(ANTHROPIC_IGNORED_START)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_FUNCTION_CALL1)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_FUNCTION_CALL2)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_FUNCTION_CALL3)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_FUNCTION_CALL4)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_FUNCTION_CALL5)
    await anthropic_adapter.handle_provider_event(ANTRHOPIC_ACTIONABLE_FUNCTION_CALL_STOP)
    await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_MESSAGE_STOP)

    delta1 = await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_OUTPUT_DELTA1)
    assert isinstance(delta1, ProviderContentDeltaEventResult)
    assert delta1.content == "This is the first part.\n"

    delta2 = await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_OUTPUT_DELTA2)
    assert isinstance(delta2, ProviderContentDeltaEventResult)
    assert delta2.content == "This is the second part."

    final_event = await anthropic_adapter.handle_provider_event(ANTHROPIC_ACTIONABLE_OUTPUT_STOP)
    assert isinstance(final_event, ProviderFinalEventResult)
    assert final_event.content == "This is the first part.\nThis is the second part."

    events = await anthropic_adapter.finalize_provider_events()
    assert len(events) == 1
    _evaluate_tool_call_events(events[0])

    assert final_event.metadata.get("usage") == anthropic_adapter._get_usage(
        input_tokens=10578 + 2913,
        output_tokens=371 + 55,
        cached_tokens=0 + 12,
        reasoning_tokens=0,
        total_tokens=(10578 + 2913) + (371 + 55) + 12,
    )

@pytest.mark.asyncio
async def test_completion_flow(anthropic_adapter, patch_plugin_calls):

    events = await anthropic_adapter.handle_provider_completion(ANTHROPIC_COMPLETE_FUNCTION_CALL_PAYLOAD)
    assert len(events) == 1
    _evaluate_tool_call_events(events[0])

    events = await anthropic_adapter.handle_provider_completion(ANTHROPIC_COMPLETE_OUTPUT_PAYLOAD)
    assert len(events) == 1
    final_event = events[0]
    assert isinstance(final_event, ProviderFinalEventResult)
    assert isinstance(final_event.content, str) and len(final_event.content) > 0

    # Aggregated usage stats from ANTHROPIC_COMPLETE_FUNCTION_CALL_PAYLOAD and ANTHROPIC_COMPLETE_OUTPUT_PAYLOAD
    assert final_event.metadata.get("usage") == anthropic_adapter._get_usage(
        input_tokens=3077 + 10505,
        output_tokens=55 + 450,
        cached_tokens=0 + 5,
        reasoning_tokens=0,
        total_tokens=(3077 + 55 + 0) + (10505 + 450 + 5),
    )


@pytest.mark.asyncio
async def test_inject_functions(anthropic_adapter):
    payload = {"field": "value"}
    messages = ChatContext.from_dicts(
        [{"role": "user", "content": "content"}],
        system_prompt="system prompt"
    )

    payload = await anthropic_adapter.inject_streaming_parameter(True, payload)
    payload = await anthropic_adapter.set_messages_in_payload(messages, payload)
    payload = await anthropic_adapter.inject_tool_payload(TOOLS, payload)

    assert payload == {
        "field": "value",
        "stream": True,
        "system": "system prompt",
        "messages": [{"role": "user", "content": "content"}],
        "tools": [
            {
                "name": "gmail_digest__list",
                "description": "List emails",
                "input_schema": {
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
                            "x-ui": {
                                "help": "Max messages to inspect (capped at 500)."
                            },
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
                            "x-ui": {
                                "help": "For actions, provide Gmail message ids to modify."
                            },
                        },
                        "preview": {
                            "type": ["boolean", "null"],
                            "default": None,
                            "x-ui": {
                                "help": "When true with approve=false, returns a plan without side effects."
                            },
                        },
                        "approve": {
                            "type": ["boolean", "null"],
                            "default": None,
                            "x-ui": {
                                "help": "Set to true (with or without preview) to perform the action."
                            },
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
            {
                "name": "calendar_events__list",
                "description": "Run calendar_events:list",
                "input_schema": {
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
                            "x-ui": {
                                "help": "Look-back window in hours when no syncToken is present."
                            },
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
                        "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_post_process_payload_injects_max_tokens_default(anthropic_adapter):
    """Test that post_process_payload injects default max_tokens when not provided."""
    # Test case 1: No max_tokens provided - should inject default
    payload_without_max_tokens = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    
    result = await anthropic_adapter.post_process_payload(payload_without_max_tokens)
    
    assert "max_tokens" in result
    assert result["max_tokens"] == 4096
    assert result["model"] == "claude-3-5-sonnet-20241022"
    assert result["messages"] == [{"role": "user", "content": "Hello"}]


@pytest.mark.asyncio
async def test_post_process_payload_preserves_user_max_tokens(anthropic_adapter):
    """Test that post_process_payload preserves user-provided max_tokens."""
    # Test case 2: max_tokens already provided - should NOT override
    payload_with_max_tokens = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 2000
    }
    
    result = await anthropic_adapter.post_process_payload(payload_with_max_tokens)
    
    assert "max_tokens" in result
    assert result["max_tokens"] == 2000  # Should preserve user value
    assert result["model"] == "claude-3-5-sonnet-20241022"
