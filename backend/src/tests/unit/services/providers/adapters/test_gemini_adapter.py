import json
import types
from unittest.mock import AsyncMock

import pytest
from shared import (
    GEMINI_ACTIONABLE_FUNCTION_CALL,
    GEMINI_ACTIONABLE_OUTPUT_DELTA1,
    GEMINI_ACTIONABLE_OUTPUT_DELTA2,
    GEMINI_ACTIONABLE_RESPONSE_COMPLETE,
    GEMINI_COMPLETE_FUNCTION_CALL_PAYLOAD,
    GEMINI_COMPLETE_OUTPUT_PAYLOAD,
    GEMINI_IGNORED_RESPONSE_COMPLETE,
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
from shu.services.providers.adapters.gemini_adapter import GeminiAdapter

FAKE_PLUGIN_RESULT = {"ok": True}


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-gemini",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def gemini_adapter(mock_db_session, mock_provider):
    return GeminiAdapter(
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
    assert tool_event.tool_calls[0].args_dict == {"op": "list", "max_results": 5}

    assert len(tool_event.additional_messages) == 2

    assistant_msg = tool_event.additional_messages[0]
    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == [
        {
            "function": {
                "name": "gmail_digest__list",
                "arguments": json.dumps({"op": "list", "max_results": 5}),
                "thoughtSignature": "signature1",
            },
        }
    ]

    result_msg = tool_event.additional_messages[1]
    assert result_msg.role == "tool"
    assert result_msg.metadata["tool_call_id"] == ""
    assert result_msg.metadata["name"] == "gmail_digest__list"
    assert result_msg.content == json.dumps(FAKE_PLUGIN_RESULT)


def test_provider_settings(gemini_adapter):
    info = gemini_adapter.get_provider_information()
    assert info.key == "gemini"
    assert info.display_name == "Gemini"

    capabilities = gemini_adapter.get_capabilities()
    assert capabilities.streaming == True
    assert capabilities.tools == True
    assert capabilities.vision == True

    assert gemini_adapter.get_api_base_url() == "https://generativelanguage.googleapis.com/v1beta"
    assert gemini_adapter.get_chat_endpoint() == "/{model}:{operation}"
    assert gemini_adapter.get_models_endpoint() == "/models"

    authorization_header = gemini_adapter.get_authorization_header()
    assert authorization_header.get("headers", {}).get("x-goog-api-key") == f"{None}"  # We're not testing decryption

    # TODO: We'll want to evaluate the mapping in the future to ensure it is valid.
    parameter_mapping = gemini_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)


@pytest.mark.asyncio
async def test_event_handling(gemini_adapter, patch_plugin_calls):
    # Streaming function call
    assert await gemini_adapter.handle_provider_event(GEMINI_ACTIONABLE_FUNCTION_CALL) is None

    # Ignore an empty completion
    assert await gemini_adapter.handle_provider_event(GEMINI_IGNORED_RESPONSE_COMPLETE) is None

    events = await gemini_adapter.finalize_provider_events()
    assert len(events) == 2  # tool call + final message
    _evaluate_tool_call_events(events[0])
    final_event = events[1]
    assert isinstance(final_event, ProviderFinalEventResult)

    # Streaming text deltas accumulate
    event = await gemini_adapter.handle_provider_event(GEMINI_ACTIONABLE_OUTPUT_DELTA1)
    assert isinstance(event, ProviderContentDeltaEventResult)
    assert event.content == "This is the first part.\n"

    event = await gemini_adapter.handle_provider_event(GEMINI_ACTIONABLE_OUTPUT_DELTA2)
    assert isinstance(event, ProviderContentDeltaEventResult)
    assert event.content == "This is the second part."

    # End of stream causes, nothing happens
    assert await gemini_adapter.handle_provider_event(GEMINI_ACTIONABLE_RESPONSE_COMPLETE) is None

    events = await gemini_adapter.finalize_provider_events()
    assert len(events) == 1
    final_event = events[0]
    assert final_event.content == "This is the first part.\nThis is the second part."

    # Aggregated usage stats from GEMINI_IGNORED_RESPONSE_COMPLETE and GEMINI_ACTIONABLE_RESPONSE_COMPLETE
    assert final_event.metadata.get("usage") == gemini_adapter._get_usage(
        input_tokens=438 + 3160,
        output_tokens=24 + 101,
        cached_tokens=0,
        reasoning_tokens=181 + 306,
        total_tokens=643 + 3567,
    )


@pytest.mark.asyncio
async def test_completion_flow(gemini_adapter, patch_plugin_calls):
    events = await gemini_adapter.handle_provider_completion(GEMINI_COMPLETE_FUNCTION_CALL_PAYLOAD)
    assert len(events) == 2  # tool call + final (empty)
    _evaluate_tool_call_events(events[0])
    assert isinstance(events[1], ProviderFinalEventResult)
    assert not events[1].content

    events = await gemini_adapter.handle_provider_completion(GEMINI_COMPLETE_OUTPUT_PAYLOAD)
    assert len(events) == 1
    final_event = events[0]
    assert isinstance(final_event, ProviderFinalEventResult)
    assert final_event.content == "This is the first part.\nThis is the second part."

    assert final_event.metadata.get("usage") == gemini_adapter._get_usage(
        input_tokens=438 + 8348,
        output_tokens=29 + 197,
        cached_tokens=0,
        reasoning_tokens=508 + 530,
        total_tokens=975 + 9075,
    )


@pytest.mark.asyncio
async def test_inject_functions(gemini_adapter):
    payload = {"field": "value"}
    messages = [
        {"role": "user", "content": "content"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking"},
                {
                    "function": {
                        "name": "gmail_digest__list",
                        "arguments": json.dumps({"op": "list", "max_results": 5}),
                        "thought": "considering",
                        "thoughtSignature": "sig-123",
                    },
                    "id": "call_1",
                },
            ],
            "tool_calls": [],
        },
        {
            "role": "tool",
            "metadata": {"name": "gmail_digest__list"},
            "content": json.dumps(FAKE_PLUGIN_RESULT),
        },
    ]
    chat_context = ChatContext.from_dicts(messages, system_prompt="system prompt")

    payload = await gemini_adapter.inject_streaming_parameter(True, payload)
    payload = await gemini_adapter.set_messages_in_payload(chat_context, payload)
    payload = await gemini_adapter.inject_tool_payload(TOOLS, payload)

    expected_payload = {
        "field": "value",
        "system_instruction": {"parts": [{"text": "system prompt"}]},
        "contents": [
            {"role": "user", "parts": [{"text": "content"}]},
            {
                "role": "model",
                "parts": [
                    {"text": "thinking"},
                    {
                        "functionCall": {
                            "name": "gmail_digest__list",
                            "args": {"op": "list", "max_results": 5},
                            "thought": "considering",
                        },
                        "thoughtSignature": "sig-123",
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": "gmail_digest__list",
                            "response": FAKE_PLUGIN_RESULT,
                        }
                    }
                ],
            },
        ],
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "gmail_digest__list",
                        "description": "List emails",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "since_hours": {"type": "integer"},
                                "query_filter": {"type": "string"},
                                "max_results": {"type": "integer"},
                                "op": {
                                    "type": "string",
                                    "description": "Operation name (fixed)",
                                    "enum": ["list"],
                                },
                                "message_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "preview": {"type": "boolean"},
                                "approve": {"type": "boolean"},
                                "kb_id": {
                                    "type": "string",
                                    "description": "Knowledge base ID to upsert digest KO into (required for op=digest)",
                                },
                            },
                            "required": ["op"],
                        },
                    },
                    {
                        "name": "calendar_events__list",
                        "description": "Run calendar_events:list",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "description": "Operation name (fixed)",
                                    "enum": ["list"],
                                },
                                "calendar_id": {"type": "string"},
                                "since_hours": {"type": "integer"},
                                "time_min": {"type": "string"},
                                "time_max": {"type": "string"},
                                "max_results": {"type": "integer"},
                                "kb_id": {"type": "string"},
                            },
                            "required": ["op"],
                        },
                    },
                ]
            }
        ],
    }

    assert payload == expected_payload
