from unittest.mock import Mock

from shu.models.plugin_execution import CallableTool

## STREAMING EVENTS

OPENAI_IGNORED_FUNCTION_DELTA = {
    "type": "response.function_call_arguments.delta",
    "sequence_number": 9,
    "item_id": "fc_0b2e19b57e859b070069308ba3bee48194abfa238577d97d99",
    "output_index": 1,
    "delta": '","',
    "obfuscation": "dMYLJBeiR5xxr",
}
OPENAI_IGNORED_RESPONSE_COMPLETE = {
    "type": "response.completed",
    "sequence_number": 39,
    "response": {
        "id": "resp_0b2e19b57e859b070069308b9430648194a9652a7c1f561699",
        "object": "response",
        "created_at": 1764789140,
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "max_tool_calls": None,
        "model": "gpt-5-2025-08-07",
        "output": [
            {
                "id": "rs_0b2e19b57e859b070069308b94d2748194ba9522cd5871622c",
                "type": "reasoning",
                "summary": [],
            },
            {
                "id": "fc_0b2e19b57e859b070069308ba3bee48194abfa238577d97d99",
                "type": "function_call",
                "status": "completed",
                "arguments": '{"op":"list","since_hours":3360,"query_filter":"is:unread in:inbox","max_results":1,"preview":false}',
                "call_id": "call_PL6FEzGcsPmg3lWwZjaTCUpL",
                "name": "gmail_digest__list",
            },
        ],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "prompt_cache_key": None,
        "prompt_cache_retention": None,
        "reasoning": {"effort": "medium", "summary": None},
        "safety_identifier": None,
        "service_tier": "default",
        "store": True,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "tool_choice": "auto",
        "tools": [
            {
                "type": "function",
                "description": "Run calendar_events:list",
                "name": "calendar_events__list",
                "parameters": {
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
                        "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
                "strict": False,
            },
            {
                "type": "function",
                "description": "List emails",
                "name": "gmail_digest__list",
                "parameters": {
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
                "strict": False,
            },
            {
                "type": "function",
                "description": "Digest summary",
                "name": "gmail_digest__digest",
                "parameters": {
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
                            "enum": ["digest"],
                            "const": "digest",
                            "default": "digest",
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
                "strict": False,
            },
            {
                "type": "web_search",
                "filters": None,
                "search_context_size": "medium",
                "user_location": {
                    "type": "approximate",
                    "city": None,
                    "country": "US",
                    "region": None,
                    "timezone": None,
                },
            },
        ],
        "top_logprobs": 0,
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 4963,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 689,
            "output_tokens_details": {"reasoning_tokens": 640},
            "total_tokens": 5652,
        },
        "user": None,
        "metadata": {},
    },
}
OPENAI_ACTIONABLE_REASONING_ITEM = {
    "type": "response.output_item.done",
    "sequence_number": 3,
    "output_index": 0,
    "item": {
        "id": "rs_0b2e19b57e859b070069308b94d2748194ba9522cd5871622c",
        "type": "reasoning",
        "summary": [],
    },
}
OPENAI_ACTIONABLE_FUNCTION_CALL = {
    "type": "response.output_item.done",
    "sequence_number": 38,
    "output_index": 1,
    "item": {
        "id": "fc_0b2e19b57e859b070069308ba3bee48194abfa238577d97d99",
        "type": "function_call",
        "status": "completed",
        "arguments": '{"op":"list","since_hours":3360,"query_filter":"is:unread in:inbox","max_results":1,"preview":false}',
        "call_id": "call_PL6FEzGcsPmg3lWwZjaTCUpL",
        "name": "gmail_digest__list",
    },
}
OPENAI_ACTIONABLE_OUTPUT_DELTA = {
    "type": "response.output_text.delta",
    "sequence_number": 8,
    "item_id": "msg_0b2e19b57e859b070069308bb0a13881949d9ec48a042a4811",
    "output_index": 1,
    "content_index": 0,
    "delta": "at",
    "logprobs": [],
    "obfuscation": "KNUF1jydQ3xvYA",
}
OPENAI_ACTIONABLE_REASONING_DELTA = {
    "type": "response.reasoning_summary_text.delta",
    "sequence_number": 10,
    "item_id": "msg_0b2e19b57e859b070069308bb0a13881949d9ec48a042a4812",
    "output_index": 1,
    "content_index": 0,
    "delta": "something",
    "logprobs": [],
    "obfuscation": "KNUF1jydQ3xvYA",
}
OPENAI_ACTIONABLE_RESPONSE_COMPLETE = {
    "type": "response.completed",
    "sequence_number": 78,
    "response": {
        "id": "resp_0b2e19b57e859b070069308ba6c00c819491005af71ab2bd77",
        "object": "response",
        "created_at": 1764789158,
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "max_tool_calls": None,
        "model": "gpt-5-2025-08-07",
        "output": [
            {
                "id": "rs_0b2e19b57e859b070069308ba88cec81949c2e48c28821a27b",
                "type": "reasoning",
                "summary": [],
            },
            {
                "id": "msg_0b2e19b57e859b070069308bb0a13881949d9ec48a042a4811",
                "type": "message",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "annotations": [],
                        "logprobs": [],
                        "text": "This is the full text.",
                    }
                ],
                "role": "assistant",
            },
        ],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "prompt_cache_key": None,
        "prompt_cache_retention": None,
        "reasoning": {"effort": "medium", "summary": None},
        "safety_identifier": None,
        "service_tier": "default",
        "store": True,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "tool_choice": "auto",
        "tools": [
            {
                "type": "function",
                "description": "Run calendar_events:list",
                "name": "calendar_events__list",
                "parameters": {
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
                        "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
                    },
                    "required": ["op"],
                    "additionalProperties": True,
                },
                "strict": False,
            },
            {
                "type": "function",
                "description": "List emails",
                "name": "gmail_digest__list",
                "parameters": {
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
                "strict": False,
            },
            {
                "type": "function",
                "description": "Digest summary",
                "name": "gmail_digest__digest",
                "parameters": {
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
                            "enum": ["digest"],
                            "const": "digest",
                            "default": "digest",
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
                "strict": False,
            },
            {
                "type": "web_search",
                "filters": None,
                "search_context_size": "medium",
                "user_location": {
                    "type": "approximate",
                    "city": None,
                    "country": "US",
                    "region": None,
                    "timezone": None,
                },
            },
        ],
        "top_logprobs": 0,
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 5887,
            "input_tokens_details": {"cached_tokens": 5632},
            "output_tokens": 395,
            "output_tokens_details": {"reasoning_tokens": 320},
            "total_tokens": 6282,
        },
        "user": None,
        "metadata": {},
    },
}

GEMINI_IGNORED_RESPONSE_COMPLETE = {
    "candidates": [{"content": {"parts": [{"text": ""}], "role": "model"}, "finishReason": "STOP", "index": 0}],
    "usageMetadata": {
        "promptTokenCount": 438,
        "candidatesTokenCount": 24,
        "totalTokenCount": 643,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 438}],
        "thoughtsTokenCount": 181,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "3qkwaajMJ9eez7IPsvrYqQw",
}
GEMINI_ACTIONABLE_FUNCTION_CALL = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "functionCall": {
                            "name": "gmail_digest__list",
                            "args": {"op": "list", "max_results": 5},
                        },
                        "thoughtSignature": "signature1",
                    }
                ],
                "role": "model",
            },
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 438,
        "candidatesTokenCount": 24,
        "totalTokenCount": 643,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 438}],
        "thoughtsTokenCount": 181,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "3qkwaajMJ9eez7IPsvrYqQw",
}
GEMINI_ACTIONABLE_OUTPUT_DELTA1 = {
    "candidates": [{"content": {"parts": [{"text": "This is the first part.\n"}], "role": "model"}, "index": 0}],
    "usageMetadata": {
        "promptTokenCount": 3160,
        "candidatesTokenCount": 39,
        "totalTokenCount": 3505,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 3160}],
        "thoughtsTokenCount": 306,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "5qkwae-BELesz7IP8eWloAw",
}
GEMINI_ACTIONABLE_OUTPUT_DELTA2 = {
    "candidates": [{"content": {"parts": [{"text": "This is the second part."}], "role": "model"}, "index": 0}],
    "usageMetadata": {
        "promptTokenCount": 3160,
        "candidatesTokenCount": 64,
        "totalTokenCount": 3530,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 3160}],
        "thoughtsTokenCount": 306,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "5qkwae-BELesz7IP8eWloAw",
}
GEMINI_ACTIONABLE_RESPONSE_COMPLETE = {
    "candidates": [
        {
            "content": {"parts": [{"text": "", "thoughtSignature": "signature2"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 3160,
        "candidatesTokenCount": 101,
        "totalTokenCount": 3567,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 3160}],
        "thoughtsTokenCount": 306,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "5qkwae-BELesz7IP8eWloAw",
}

ANTHROPIC_IGNORED_START = {
    "type": "message_start",
    "message": {
        "model": "claude-opus-4-5-20251101",
        "id": "msg_01VcjtckP9pmxw51nknxmv1A",
        "type": "message",
        "role": "assistant",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 2913,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            "output_tokens": 1,
            "service_tier": "standard",
        },
    },
}
ANTHROPIC_ACTIONABLE_FUNCTION_CALL1 = {
    "type": "content_block_start",
    "index": 0,
    "content_block": {
        "type": "tool_use",
        "id": "toolu_01P8Dmpo2vu2vZpdyKyhmQPA",
        "name": "gmail_digest__list",
        "input": {},
    },
}
ANTHROPIC_ACTIONABLE_FUNCTION_CALL2 = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "input_json_delta", "partial_json": ""},
}
ANTHROPIC_ACTIONABLE_FUNCTION_CALL3 = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "input_json_delta", "partial_json": '{"op": "dige'},
}
ANTHROPIC_ACTIONABLE_FUNCTION_CALL4 = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "input_json_delta", "partial_json": 'st"}'},
}
ANTHROPIC_ACTIONABLE_FUNCTION_CALL5 = {"type": "content_block_stop", "index": 0}
ANTRHOPIC_ACTIONABLE_FUNCTION_CALL_STOP = {
    "type": "message_delta",
    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
    "usage": {
        "input_tokens": 2913,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 55,
    },
}
ANTHROPIC_ACTIONABLE_MESSAGE_STOP = {"type": "message_stop"}
ANTHROPIC_ACTIONABLE_OUTPUT_DELTA1 = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "text_delta", "text": "This is the first part.\n"},
}
ANTHROPIC_ACTIONABLE_OUTPUT_DELTA2 = {
    "type": "content_block_delta",
    "index": 0,
    "delta": {"type": "text_delta", "text": "This is the second part."},
}
ANTHROPIC_ACTIONABLE_OUTPUT_STOP = {
    "type": "message_delta",
    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
    "usage": {
        "input_tokens": 10578,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 2,
        "output_tokens": 371,
    },
}

COMPLETIONS_ACTIONABLE_FUNCTION_CALL_DELTAS_PAYLOAD = [
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_d8nloBG4cV9aqNRbjoyQL8Oo",
                            "type": "function",
                            "function": {"name": "gmail_digest__list", "arguments": ""},
                        }
                    ],
                    "refusal": None,
                },
                "finish_reason": None,
            }
        ],
        "obfuscation": "A5uvESTsO14",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "CcWG3vOLWASG",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "op"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "DMZpIHE8e7ncd",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":"'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "hVcVWITnDf",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "list"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "xKEZhSIdlYC",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '","'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "7qmNMIJSDo",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "since"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "MOahQ6NJvr",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "_hours"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "2Faqv6ssV",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "E6WnZlcoKUGO",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "336"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "pxqU8n8NP2Ew",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "0"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "JcHIauGSw2RIRH",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ',"'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "B5XsL3FtVdRq",
    },
    # Random event that returns a usage. This usage should be ignored if there is another one following in this exact cycle.
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "query"}}]},
                "finish_reason": None,
            }
        ],
        "usage": {
            "prompt_tokens": 885,
            "completion_tokens": 500,
            "total_tokens": 1385,
            "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
            "completion_tokens_details": {
                "reasoning_tokens": 448,
                "audio_tokens": 0,
                "accepted_prediction_tokens": 0,
                "rejected_prediction_tokens": 0,
            },
        },
        "obfuscation": "suUWltvAPq",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "_filter"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "T6o2fCCf",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":"'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "GfS0y0xCKO",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "in"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "FPHKVjgivfMkF",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "UNTuMCzQht2fyu",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "in"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "deJnoymSsZhYt",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "box"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "vgv9ERAkpC9u",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " is"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "dmGYvFtyDwwq",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "vwhPC0vlCviHeX",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "un"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "pQI04kIQYtCc0",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "read"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "IH8vwL65UT3",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '","'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "7hZCAAF1od",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "max"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "RqrXNPHm55xC",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "_results"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "nhQsO4u",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "1HkSxrx3K5cK",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "50"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "rVEichRV0FoUt",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ',"'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "rdTKAo8F5dAK",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "preview"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "4rBwq5bx",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":'}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "gbipyjrJJjKl",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "true"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "cLNpTfjZtXb",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                "finish_reason": None,
            }
        ],
        "obfuscation": "fBFVjwGHD93m6K",
    },
    {
        "id": "chatcmpl-Cja8tuq0HdaF5vXpdUqZyVgRPNrB5",
        "object": "chat.completion.chunk",
        "created": 1764979727,
        "model": "gpt-5-2025-08-07",
        "service_tier": "default",
        "system_fingerprint": None,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        "obfuscation": "iQ8Y6jXXV20SD",
    },
]
COMPLETIONS_IGNORED_FUNCTION_CALL_COMPLETION_PAYLOAD = {
    "id": "chatcmpl-ClJyYyKUHQdnxspzXPWBe92SqHpdC",
    "object": "chat.completion.chunk",
    "created": 1765394238,
    "model": "gpt-5-2025-08-07",
    "service_tier": "default",
    "system_fingerprint": None,
    "choices": [],
    "usage": {
        "prompt_tokens": 885,
        "completion_tokens": 500,
        "total_tokens": 1385,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 448,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    },
    "obfuscation": "lPTCV",
}
COMPLETIONS_ACTIONABLE_OUTPUT_DELTA1 = {
    "id": "chatcmpl-Ck8CGZjygXnFs9BjgdNauuP8IJEAp",
    "object": "chat.completion.chunk",
    "created": 1765110632,
    "model": "gpt-5-2025-08-07",
    "service_tier": "default",
    "system_fingerprint": None,
    "choices": [{"index": 0, "delta": {"content": "This is the first part.\n"}, "finish_reason": None}],
    "obfuscation": "Xsv6D4tT",
}
COMPLETIONS_ACTIONABLE_OUTPUT_DELTA2 = {
    "id": "chatcmpl-Ck8CGZjygXnFs9BjgdNauuP8IJEAp",
    "object": "chat.completion.chunk",
    "created": 1765110632,
    "model": "gpt-5-2025-08-07",
    "service_tier": "default",
    "system_fingerprint": None,
    "choices": [{"index": 0, "delta": {"content": "This is the second part."}, "finish_reason": None}],
    "obfuscation": "l7NKc",
}
COMPLETIONS_ACTIONABLE_OUTPUT_STOP = {
    "id": "chatcmpl-Ck8CGZjygXnFs9BjgdNauuP8IJEAp",
    "object": "chat.completion.chunk",
    "created": 1765110632,
    "model": "gpt-5-2025-08-07",
    "service_tier": "default",
    "system_fingerprint": None,
    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 1226,
        "completion_tokens": 651,
        "total_tokens": 1877,
        "prompt_tokens_details": {"cached_tokens": 100, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 512,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    },
    "obfuscation": "4pe",
}

## COMPLETE PAYLOAD

OPENAI_COMPLETE_FUNCTION_CALL_PAYLOAD = {
    "id": "resp_01df972359e95b0300693094a9fa9c8196a9840729cc9148bf",
    "object": "response",
    "created_at": 1764791466,
    "status": "completed",
    "background": False,
    "billing": {"payer": "developer"},
    "error": None,
    "incomplete_details": None,
    "instructions": None,
    "max_output_tokens": None,
    "max_tool_calls": None,
    "model": "gpt-5-2025-08-07",
    "output": [
        {
            "id": "rs_0b2e19b57e859b070069308b94d2748194ba9522cd5871622c",
            "type": "reasoning",
            "summary": [],
        },
        {
            "id": "fc_0b2e19b57e859b070069308ba3bee48194abfa238577d97d99",
            "type": "function_call",
            "status": "completed",
            "arguments": '{"op":"list","since_hours":3360,"query_filter":"is:unread in:inbox","max_results":1,"preview":false}',
            "call_id": "call_PL6FEzGcsPmg3lWwZjaTCUpL",
            "name": "gmail_digest__list",
        },
    ],
    "parallel_tool_calls": True,
    "previous_response_id": None,
    "prompt_cache_key": None,
    "prompt_cache_retention": None,
    "reasoning": {"effort": "medium", "summary": None},
    "safety_identifier": None,
    "service_tier": "default",
    "store": True,
    "temperature": 1.0,
    "text": {"format": {"type": "text"}, "verbosity": "medium"},
    "tool_choice": "auto",
    "tools": [
        {
            "type": "function",
            "description": "Run calendar_events:list",
            "name": "calendar_events__list",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["list"],
                        "const": "list",
                        "default": "list",
                    },
                    "calendar_id": {
                        "type": ["string", "None"],
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
                        "type": ["string", "None"],
                        "x-ui": {"help": "ISO timeMin override (UTC)."},
                    },
                    "time_max": {
                        "type": ["string", "None"],
                        "x-ui": {"help": "ISO timeMax override (UTC)."},
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 250,
                        "default": 50,
                    },
                    "kb_id": {"type": ["string", "None"], "x-ui": {"hidden": True}},
                },
                "required": ["op"],
                "additionalProperties": True,
            },
            "strict": False,
        },
        {
            "type": "function",
            "description": "List emails",
            "name": "gmail_digest__list",
            "parameters": {
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
                        "type": ["string", "None"],
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
            "strict": False,
        },
        {
            "type": "function",
            "description": "Digest summary",
            "name": "gmail_digest__digest",
            "parameters": {
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
                        "enum": ["digest"],
                        "const": "digest",
                        "default": "digest",
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
            "strict": False,
        },
        {
            "type": "web_search",
            "filters": None,
            "search_context_size": "medium",
            "user_location": {
                "type": "approximate",
                "city": None,
                "country": "US",
                "region": None,
                "timezone": None,
            },
        },
    ],
    "top_logprobs": 0,
    "top_p": 1.0,
    "truncation": "disabled",
    "usage": {
        "input_tokens": 4963,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 561,
        "output_tokens_details": {"reasoning_tokens": 512},
        "total_tokens": 5524,
    },
    "user": None,
    "metadata": {},
}
OPENAI_COMPLETE_OUTPUT_PAYLOAD = {
    "id": "resp_01df972359e95b0300693094be404881968d3ea220f439920d",
    "object": "response",
    "created_at": 1764791486,
    "status": "completed",
    "background": False,
    "billing": {"payer": "developer"},
    "error": None,
    "incomplete_details": None,
    "instructions": None,
    "max_output_tokens": None,
    "max_tool_calls": None,
    "model": "gpt-5-2025-08-07",
    "output": [
        {
            "id": "rs_01df972359e95b0300693094bea6208196af302de454b60dfe",
            "type": "reasoning",
            "summary": [],
        },
        {
            "id": "msg_01df972359e95b0300693094c57344819684ad49e1f096d195",
            "type": "message",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "annotations": [],
                    "logprobs": [],
                    "text": "This is the full text.",
                }
            ],
            "role": "assistant",
        },
    ],
    "parallel_tool_calls": True,
    "previous_response_id": None,
    "prompt_cache_key": None,
    "prompt_cache_retention": None,
    "reasoning": {"effort": "medium", "summary": None},
    "safety_identifier": None,
    "service_tier": "default",
    "store": True,
    "temperature": 1.0,
    "text": {"format": {"type": "text"}, "verbosity": "medium"},
    "tool_choice": "auto",
    "tools": [
        {
            "type": "function",
            "description": "Run calendar_events:list",
            "name": "calendar_events__list",
            "parameters": {
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
                    "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
                },
                "required": ["op"],
                "additionalProperties": True,
            },
            "strict": False,
        },
        {
            "type": "function",
            "description": "List emails",
            "name": "gmail_digest__list",
            "parameters": {
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
            "strict": False,
        },
        {
            "type": "function",
            "description": "Digest summary",
            "name": "gmail_digest__digest",
            "parameters": {
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
                        "enum": ["digest"],
                        "const": "digest",
                        "default": "digest",
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
            "strict": False,
        },
        {
            "type": "web_search",
            "filters": None,
            "search_context_size": "medium",
            "user_location": {
                "type": "approximate",
                "city": None,
                "country": "US",
                "region": None,
                "timezone": None,
            },
        },
    ],
    "top_logprobs": 0,
    "top_p": 1.0,
    "truncation": "disabled",
    "usage": {
        "input_tokens": 5777,
        "input_tokens_details": {"cached_tokens": 5504},
        "output_tokens": 376,
        "output_tokens_details": {"reasoning_tokens": 320},
        "total_tokens": 6153,
    },
    "user": None,
    "metadata": {},
}

GEMINI_COMPLETE_FUNCTION_CALL_PAYLOAD = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "functionCall": {
                            "name": "gmail_digest__list",
                            "args": {"op": "list", "max_results": 5},
                        },
                        "thoughtSignature": "signature1",
                    }
                ],
                "role": "model",
            },
            "finishReason": "STOP",
            "index": 0,
            "finishMessage": "Model generated function call(s).",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 438,
        "candidatesTokenCount": 29,
        "totalTokenCount": 975,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 438}],
        "thoughtsTokenCount": 508,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "H6swad7cHr3Oz7IPurHSsAw",
}
GEMINI_COMPLETE_OUTPUT_PAYLOAD = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": "This is the first part.\nThis is the second part.",
                        "thoughtSignature": "signature2",
                    }
                ],
                "role": "model",
            },
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 8348,
        "candidatesTokenCount": 197,
        "totalTokenCount": 9075,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 8348}],
        "thoughtsTokenCount": 530,
    },
    "modelVersion": "gemini-3-pro-preview",
    "responseId": "LqswaZ7oEO6Jz7IPidS4wQw",
}

ANTHROPIC_COMPLETE_FUNCTION_CALL_PAYLOAD = {
    "model": "claude-opus-4-5-20251101",
    "id": "msg_01XAYy4TqeTGa1zwcKRMrcpH",
    "type": "message",
    "role": "assistant",
    "content": [
        {"type": "text", "text": "This is the first part.\nThis is the second part."},
        {
            "type": "tool_use",
            "id": "toolu_01P8Dmpo2vu2vZpdyKyhmQPA",
            "name": "gmail_digest__list",
            "input": {"op": "digest"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 3077,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        "output_tokens": 55,
        "service_tier": "standard",
    },
}
ANTHROPIC_COMPLETE_OUTPUT_PAYLOAD = {
    "model": "claude-opus-4-5-20251101",
    "id": "msg_01Aj1WvEPPXr4bxAXd7WxTGY",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "This is the first part.\nThis is the second part."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 10505,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 3,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        "output_tokens": 450,
        "service_tier": "standard",
    },
}

COMPLETIONS_COMPLETE_FUNCTION_CALL_PAYLOAD = {
    "id": "chatcmpl-Ck8eJrYJjGJQ7UPhyDLm7a2DxD3A9",
    "object": "chat.completion",
    "created": 1765112371,
    "model": "gpt-5-2025-08-07",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_d8nloBG4cV9aqNRbjoyQL8Oo",
                        "type": "function",
                        "function": {
                            "name": "gmail_digest__list",
                            "arguments": '{"op":"list","since_hours":3360,"query_filter":"in:inbox is:unread","max_results":50,"preview":true}',
                        },
                    }
                ],
                "refusal": None,
                "annotations": [],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {
        "prompt_tokens": 662,
        "completion_tokens": 560,
        "total_tokens": 1222,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 512,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    },
    "service_tier": "default",
    "system_fingerprint": None,
}
COMPLETIONS_COMPLETE_OUTPUT_PAYLOAD = {
    "id": "chatcmpl-Ck8ZFRwAcRMQaEiM8BE0B65FepAwu",
    "object": "chat.completion",
    "created": 1765112057,
    "model": "gpt-5-2025-08-07",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "This is the first part.\nThis is the second part.",
                "refusal": None,
                "annotations": [],
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 623,
        "completion_tokens": 95,
        "total_tokens": 718,
        "prompt_tokens_details": {"cached_tokens": 100, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 64,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    },
    "service_tier": "default",
    "system_fingerprint": None,
}

## OTHER CONSTANTS

TOOLS = [
    CallableTool(
        name="gmail_digest",
        op="list",
        plugin=Mock(),
        schema={
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
                    "type": ["string", "null"],
                    "enum": ["list", "mark_read", "archive", "digest", "ingest"],
                    "default": "ingest",
                    "x-ui": {
                        "help": "Choose an operation.",
                        "enum_labels": {
                            "list": "List emails",
                            "mark_read": "Mark read",
                            "archive": "Archive",
                            "digest": "Digest summary",
                            "ingest": "Ingest to KB",
                        },
                        "enum_help": {
                            "list": "List recent messages (no changes)",
                            "mark_read": "Mark selected messages as read (approval required)",
                            "archive": "Remove Inbox label for selected messages (approval required)",
                            "digest": "Create a short inbox summary",
                            "ingest": "Ingest full email contents into KB as individual KOs",
                        },
                    },
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
            "required": [],
            "additionalProperties": True,
        },
        enum_labels={
            "list": "List emails",
            "mark_read": "Mark read",
            "archive": "Archive",
            "digest": "Digest summary",
            "ingest": "Ingest to KB",
        },
    ),
    CallableTool(
        name="calendar_events",
        op="list",
        plugin=Mock(),
        schema={
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "ingest"],
                    "default": "ingest",
                    "x-ui": {"help": "Choose operation"},
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
                "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
            },
            "required": [],
            "additionalProperties": True,
        },
        enum_labels=None,
    ),
]
