import types
from unittest.mock import AsyncMock

import pytest

from shu.services.providers.adapter_base import ProviderAdapterContext
from shu.services.providers.adapters.xai_adapter import XAIAdapter
from tests.unit.services.providers.adapters.shared import TOOLS


@pytest.fixture(scope="function")
def mock_db_session():
    return AsyncMock()


@pytest.fixture(scope="function")
def mock_provider():
    return types.SimpleNamespace(
        name="test-xai",
        api_key_encrypted=None,
        config={},
    )


@pytest.fixture(scope="function")
def xai_adapter(mock_db_session, mock_provider):
    return XAIAdapter(
        ProviderAdapterContext(
            db_session=mock_db_session,
            provider=mock_provider,
            conversation_owner_id="unit-test-user",
        )
    )


def test_provider_settings(xai_adapter):
    info = xai_adapter.get_provider_information()
    assert info.key == "xai"
    assert info.display_name == "xAI"

    capabilities = xai_adapter.get_capabilities()
    assert capabilities.streaming is True
    assert capabilities.tools is True
    assert capabilities.vision is False

    assert xai_adapter.get_api_base_url() == "https://api.x.ai/v1"
    assert xai_adapter.get_chat_endpoint() == "/responses"
    assert xai_adapter.get_models_endpoint() == "/models"
    assert xai_adapter.get_model_information_path() == "data[*].{id: id, name: id}"

    authorization_header = xai_adapter.get_authorization_header()
    assert authorization_header.get("headers", {}).get("Authorization") == f"Bearer {None}"  # decryption not under test

    parameter_mapping = xai_adapter.get_parameter_mapping()
    assert isinstance(parameter_mapping, dict)


@pytest.mark.asyncio
async def test_inject_functions(xai_adapter):
    payload = {"field": "value"}

    payload = await xai_adapter.inject_tool_payload(TOOLS, payload)

    assert payload == {
        "field": "value",
        "tools": [
            {
                "type": "function",
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
                "type": "function",
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
                        "kb_id": {
                            "type": ["string", "null"],
                            "x-ui": {"hidden": True},
                        },
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
            },
        ],
    }
