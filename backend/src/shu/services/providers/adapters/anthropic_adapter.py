import json
from typing import Any, Dict, List, Optional

import jmespath

from shu.services.providers.parameter_definitions import (
    ArrayParameter,
    BooleanParameter,
    EnumParameter,
    InputField,
    IntegerParameter,
    NumberParameter,
    ObjectParameter,
    Option,
    StringParameter,
)
from shu.core.logging import get_logger

from ..adapter_base import (
    BaseProviderAdapter,
    ProviderAdapterContext,
    ProviderCapabilities,
    ProviderInformation,
    register_adapter,
    ProviderContentDeltaEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)
from shu.models.plugin_execution import CallableTool

logger = get_logger(__name__)


class AnthropicAdapter(BaseProviderAdapter):

    def __init__(self, context: ProviderAdapterContext):
        super().__init__(context)
        self._latest_usage_event: Optional[Dict[str, Any]] = None
        self._stream_content: List[str] = []
        self._stream_tool_calls: Dict[int, Dict[str, Any]] = {}

    async def _build_assistant_and_result_messages(self, assistant_blocks: List[Dict[str, Any]], tool_blocks: List[Dict[str, Any]], tool_calls: list[ToolCallInstructions]):
        assistant_message = {"role": "assistant", "content": assistant_blocks} if assistant_blocks else None
        result_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("id", ""),
                        "content": await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
                    }
                ],
            }
            for block, tool_call in zip(tool_blocks, tool_calls)
        ]
        return assistant_message, result_messages

    def _extract_usage(self, payload: Dict[str, Any]) -> None:
        usage = jmespath.search("usage", payload) if isinstance(payload, dict) else None
        if not usage:
            return

        cached_tokens = (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        self._update_usage(
            input_tokens,
            output_tokens,
            cached_tokens,
            0,
            input_tokens + output_tokens + cached_tokens,
        )

    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="anthropic", display_name="Anthropic")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=False)

    def get_api_base_url(self) -> str:
        return "https://api.anthropic.com/v1"

    def get_chat_endpoint(self) -> str:
        return "/messages"

    def get_models_endpoint(self) -> str:
        return "/models"

    def get_authorization_header(self) -> Dict[str, Any]:
        return {
            "scheme": "x-api-key",
            "headers": {"x-api-key": f"{self.api_key}", "anthropic-version": "2023-06-01"},
        }

    def get_parameter_mapping(self) -> Dict[str, Any]:
        return {
            # Sampling / generation controls
            "temperature": NumberParameter(
                min=0,
                max=1,
                default=1.0,
                label="Temperature",
                description=(
                    "Controls randomness in the model's output. Lower = more deterministic, higher = more creative."
                ),
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                label="Top P",
                description=(
                    "Nucleus sampling: consider the smallest set of tokens whose cumulative probability ≥ top_p. Leave unset to use Anthropic defaults."
                ),
            ),
            "top_k": IntegerParameter(
                min=1,
                label="Top K",
                description=(
                    "Top-k sampling: restrict token selection to the k most likely tokens. Leave unset to use Anthropic defaults."
                ),
            ),
            "max_tokens": IntegerParameter(
                min=1,
                label="Max Tokens",
                description=(
                    "Maximum number of tokens the model can generate in this response (output tokens only)."
                ),
            ),
            "stop_sequences": ArrayParameter(
                label="Stop Sequences",
                description=(
                    "Custom strings where generation should stop. If the model outputs any of these, it will stop immediately."
                ),
                items=StringParameter(
                    label="Stop sequence",
                    placeholder="e.g. </END>",
                ),
            ),

            # Tools & tool choice (Anthropic Messages API)
            # Built-in & custom tools
            "tools": ArrayParameter(
                label="Tools",
                description=(
                    "Built-in Anthropic server tools (web search, web fetch, code execution, tool search) plus any custom tools you define."
                ),
                # Predefined built-in tools
                options=[
                    # Web search tool
                    Option(
                        value={
                            "type": "web_search_20250305",
                            "name": "web_search",
                        },
                        label="Web Search",
                        help=(
                            "Give Claude real-time web search capabilities with citations. Requires the web search beta header."
                        ),
                        input_schema=ObjectParameter(
                            label="Web search configuration",
                            properties={
                                "max_uses": IntegerParameter(
                                    min=1,
                                    label="Max uses",
                                    description="Limit the number of searches per request.",
                                ),
                                "allowed_domains": ArrayParameter(
                                    label="Allowed domains",
                                    description=(
                                        "Only search within these domains. Cannot be combined with blocked_domains."
                                    ),
                                    items=StringParameter(
                                        label="Domain",
                                        placeholder="example.com",
                                    ),
                                ),
                                "blocked_domains": ArrayParameter(
                                    label="Blocked domains",
                                    description=(
                                        "Never include results from these domains. Cannot be combined with allowed_domains."
                                    ),
                                    items=StringParameter(
                                        label="Domain",
                                        placeholder="untrustedsource.com",
                                    ),
                                ),
                                "user_location": ObjectParameter(
                                    label="User location",
                                    description="Approximate location to localize search results.",
                                    properties={
                                        "type": StringParameter(
                                            default="approximate",
                                            label="Type",
                                            placeholder="approximate",
                                        ),
                                        "city": StringParameter(label="City"),
                                        "region": StringParameter(label="Region / State"),
                                        "country": StringParameter(label="Country (ISO code)"),
                                        "timezone": StringParameter(
                                            label="Timezone",
                                            placeholder="America/Los_Angeles",
                                        ),
                                    },
                                ),
                            },
                        ),
                    ),
                    # Web fetch tool
                    Option(
                        value={
                            "type": "web_fetch_20250910",
                            "name": "web_fetch",
                        },
                        label="Web Fetch",
                        help=(
                            "Fetch full content from explicit URLs and PDFs, then let Claude analyze it. Requires the web fetch beta header."
                        ),
                        input_schema=ObjectParameter(
                            label="Web fetch configuration",
                            properties={
                                "max_uses": IntegerParameter(
                                    min=1,
                                    label="Max uses",
                                    description="Limit the number of fetches per request.",
                                ),
                                "allowed_domains": ArrayParameter(
                                    label="Allowed domains",
                                    description=(
                                        "Only fetch from these domains. Cannot be combined with blocked_domains."
                                    ),
                                    items=StringParameter(
                                        label="Domain",
                                        placeholder="example.com",
                                    ),
                                ),
                                "blocked_domains": ArrayParameter(
                                    label="Blocked domains",
                                    description=(
                                        "Never fetch from these domains. Cannot be combined with allowed_domains."
                                    ),
                                    items=StringParameter(
                                        label="Domain",
                                        placeholder="private.example.com",
                                    ),
                                ),
                                "citations": ObjectParameter(
                                    label="Citations",
                                    description=(
                                        "Control whether Claude can emit citations to specific passages in fetched documents."
                                    ),
                                    properties={
                                        "enabled": BooleanParameter(
                                            label="Enable citations",
                                            default=True,
                                        ),
                                    },
                                ),
                                "max_content_tokens": IntegerParameter(
                                    min=1024,
                                    label="Max content tokens",
                                    description=(
                                        "Approximate maximum number of tokens from fetched content to include in context."
                                    ),
                                ),
                            },
                        ),
                    ),
                    # Code execution tool
                    Option(
                        value={
                            "type": "code_execution_20250825",
                            "name": "code_execution",
                        },
                        label="Code Execution",
                        help=(
                            "Run Bash commands and manipulate files in a secure sandbox. Powers data analysis, scripting, and file editing. Requires the code execution beta header."
                        ),
                        # No extra parameters in the tool definition per Anthropic docs
                    ),
                    # TODO: We don't support this for now.
                    # # Tool search (regex variant)
                    # Option(
                    #     value={
                    #         "type": "tool_search_tool_regex_20251119",
                    #         "name": "tool_search_tool_regex",
                    #     },
                    #     label="Tool Search (Regex)",
                    #     help=(
                    #         "Server-side tool search using regex over your tool catalog. "
                    #         "Requires the advanced tool use beta header."
                    #     ),
                    # ),
                    # # Tool search (BM25 / natural language variant)
                    # Option(
                    #     value={
                    #         "type": "tool_search_tool_bm25_20251119",
                    #         "name": "tool_search_tool_bm25",
                    #     },
                    #     label="Tool Search (BM25)",
                    #     help=(
                    #         "Server-side tool search using natural language queries over "
                    #         "your tool catalog. Requires the advanced tool use beta header."
                    #     ),
                    # ),
                ],
                # Generic custom tool definition (client tools, memory tool wrappers, etc.)
                items=ObjectParameter(
                    label="Custom Tool",
                    description=(
                        "Custom tool definition (name, description, JSON input schema). These are standard Anthropic client tools."
                    ),
                    properties={
                        "name": StringParameter(
                            label="Name",
                            description="Tool name (identifier used in tool calls).",
                            placeholder="e.g. get_weather",
                        ),
                        "description": StringParameter(
                            label="Description",
                            description="Short description of what this tool does.",
                            placeholder="Describe the tool's purpose",
                        ),
                        "input_schema": StringParameter(
                            label="Input schema (JSON)",
                            description=(
                                "JSON Schema for the tool's arguments, as a JSON object."
                            ),
                            placeholder='e.g. {"type":"object","properties":{...}}',
                        ),
                        "defer_loading": BooleanParameter(
                            label="Defer loading",
                            description=(
                                "If true, tool can be discovered via tool search instead of being loaded into context immediately."
                            ),
                            default=False,
                        ),
                    },
                    required=["name", "input_schema"],
                ),
            ),

            # Tool choice
            "tool_choice": ObjectParameter(
                label="Tool Choice",
                description=(
                    "Controls how tools are used. 'Auto' lets the model decide. 'Any' forces a tool call. 'None' disables tools. 'Specific tool' forces one tool by name."
                ),
                options=[
                    Option(
                        value={"type": "auto"},
                        label="Auto",
                        help="Model decides whether to call tools or answer directly.",
                    ),
                    Option(
                        value={"type": "any"},
                        label="Any (force tool call)",
                        help="Model must call at least one of the provided tools.",
                    ),
                    Option(
                        value={"type": "none"},
                        label="None",
                        help="Disable all tool calls for this request.",
                    ),
                    Option(
                        value={"type": "tool", "name": ""},
                        label="Specific tool (set name)",
                        help=(
                            "Force the model to call a specific tool. The tool name must match one of the tools in 'tools'."
                        ),
                        input_fields=[
                            InputField(
                                path="name",
                                type="string",
                                label="Tool name",
                                required=True,
                            )
                        ],
                    ),
                ],
            ),

            # TODO: Currently broken. Opus 4.5 requires us to send thinking segments back to the provider for follow-ups. We need to fix this before we can support the thinking settings.
            # Extended thinking (Claude 4 / thinking models)
            # "thinking": ObjectParameter(
            #     label="Thinking (Extended reasoning)",
            #     description=(
            #         "Controls Claude's extended thinking mode. Disabled by default. When enabled, the model uses extra 'thinking' tokens internally before producing the final answer."
            #     ),
            #     options=[
            #         Option(
            #             value=None,
            #             label="Disabled",
            #             help=(
            #                 "Do not send a 'thinking' object. Claude will respond normally without extended thinking."
            #             ),
            #         ),
            #         Option(
            #             value={"type": "enabled"},
            #             label="Enabled (set budget)",
            #             help=(
            #                 "Enable extended thinking and specify a budget for internal reasoning tokens. Must be less than max_tokens."
            #             ),
            #             input_schema=ObjectParameter(
            #                 label="Thinking Configuration",
            #                 properties={
            #                     "budget_tokens": IntegerParameter(
            #                         min=1024,
            #                         label="Budget Tokens",
            #                         description=(
            #                             "Maximum tokens Claude can spend on internal reasoning. Must be less than max_tokens."
            #                         ),
            #                         placeholder="e.g. 4096",
            #                     )
            #                 },
            #                 required=["budget_tokens"],
            #             ),
            #         ),
            #     ],
            # ),

            # Metadata / routing
            "metadata": ObjectParameter(
                label="Metadata",
                description=(
                    "Arbitrary JSON object to tag the request (for analytics, debugging, or billing). Passed through as 'metadata' to Anthropic."
                ),
            ),
            "service_tier": EnumParameter(
                label="Service Tier",
                description=(
                    "Controls which capacity tier can be used. 'Auto' uses priority capacity when available, falling back to standard; 'Standard only' avoids priority tier."
                ),
                options=[
                    Option(value="auto", label="Auto"),
                    Option(value="standard_only", label="Standard only"),
                ],
                default="auto",
            ),
        }
    
    async def set_messages_in_payload(self, messages: List[Dict[str, str]], payload: Dict[str, Any]) -> Dict[str, Any]:
        system_messages: List[str] = []
        formatted_messages: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_messages.append(content)
                continue

            formatted_messages.append({"role": role, "content": content})

        if system_messages:
            payload["system"] = "\n\n".join(system_messages)

        payload["messages"] = formatted_messages
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["stream"] = should_stream
        return payload

    async def inject_tool_payload(self, tools: List[CallableTool], payload: Dict[str, Any]) -> Dict[str, Any]:
        anthropic_tools = []
        for tool in tools:
            title = None
            if isinstance(tool.enum_labels, dict):
                title = tool.enum_labels.get(str(tool.op))
            tool_name = f"{tool.name}__{tool.op}"
            input_schema = tool.schema or {"type": "object", "properties": {}, "additionalProperties": True}
            props = input_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [tool.op],
                "const": tool.op,
                "default": tool.op,
            }
            if isinstance(input_schema.get("required"), list):
                if "op" not in input_schema["required"]:
                    input_schema["required"].append("op")
            else:
                input_schema["required"] = ["op"]

            anthropic_tools.append(
                {
                    "name": tool_name,
                    "description": title or f"Run {tool.name}:{tool.op}",
                    "input_schema": input_schema,
                }
            )

        if anthropic_tools:
            payload["tools"] = payload.get("tools", []) + anthropic_tools

        return payload

    def _tool_call_from_block(self, block: Dict[str, Any]) -> ToolCallInstructions:
        tool_name = block.get("name", "")
        try:
            plugin_name, op = tool_name.split("__", 1)
        except ValueError:
            plugin_name, op = tool_name, ""

        args_dict = block.get("input") if isinstance(block.get("input"), dict) else {}
        input_buffer = block.get("_input_buffer") or ""
        if input_buffer:
            try:
                parsed = json.loads(input_buffer)
                if isinstance(parsed, dict):
                    args_dict = parsed
            except Exception:
                args_dict = args_dict or {}

        return ToolCallInstructions(
            plugin_name=plugin_name,
            operation=op,
            args_dict=args_dict,
        )

    def get_model_information_path(self) -> str:
        return "data[*].{id: id, name: id}"

    async def handle_provider_event(self, chunk: Dict[str, Any]) -> ProviderEventResult:

        content_delta = jmespath.search("type=='content_block_delta' && delta.text", chunk)
        if content_delta:
            self._stream_content.append(content_delta)
            return ProviderContentDeltaEventResult(content=content_delta)
        
        if "usage" in chunk:
            self._latest_usage_event = chunk

        _, index, start_event = jmespath.search("type=='content_block_start' && content_block.type == 'tool_use' && *", chunk) or (None, None, None)
        if start_event:
            start_event["_input_buffer"] = ""
            self._stream_tool_calls[index] = start_event

        _, index, delta_event = jmespath.search("type=='content_block_delta' && delta.type == 'input_json_delta' && *", chunk) or (None, None, None)
        if delta_event and index is not None and index in self._stream_tool_calls:
            block = self._stream_tool_calls[index]
            block["_input_buffer"] = block.get("_input_buffer", "") + (delta_event.get("partial_json") or "")

        stop_reason = jmespath.search("delta.stop_reason", chunk)
        if stop_reason:
            self._extract_usage(self._latest_usage_event)
        if stop_reason == "end_turn":
            final_text = "".join(self._stream_content)
            return ProviderFinalEventResult(content=final_text, metadata={"usage": self.usage})

    async def finalize_provider_events(self) -> List[ProviderEventResult]:

        if not self._stream_tool_calls:
            return []

        tool_blocks = [self._stream_tool_calls[k] for k in sorted(self._stream_tool_calls.keys())]
        tool_calls = [self._tool_call_from_block(block) for block in tool_blocks]

        assistant_blocks: List[Dict[str, Any]] = []
        final_text = "".join(self._stream_content)
        if final_text:
            assistant_blocks.append({"type": "text", "text": final_text})
        for block, tool_call in zip(tool_blocks, tool_calls):
            assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": tool_call.args_dict,
                }
            )

        assistant_message, result_messages = await self._build_assistant_and_result_messages(
            assistant_blocks,
            tool_blocks,
            tool_calls,
        )

        self._stream_tool_calls = {}
        self._stream_content = []

        additional_messages = []
        if assistant_message:
            additional_messages.append(assistant_message)
        additional_messages.extend(result_messages)

        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=additional_messages,
                content="",
            )
        ]

    async def handle_provider_completion(self, data: Dict[str, Any]) -> List[ProviderEventResult]:

        self._extract_usage(data)

        content_blocks = data.get("content") or []
        text_parts: List[str] = []
        tool_blocks: List[Dict[str, Any]] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_blocks.append(block)

        final_text = "".join(text_parts)
        tool_calls = [self._tool_call_from_block(block) for block in tool_blocks]

        assistant_blocks: List[Dict[str, Any]] = []
        if final_text:
            assistant_blocks.append({"type": "text", "text": final_text})
        for block, tool_call in zip(tool_blocks, tool_calls):
            assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": tool_call.args_dict,
                }
            )

        assistant_message, result_messages = await self._build_assistant_and_result_messages(
            assistant_blocks,
            tool_blocks,
            tool_calls,
        )

        additional_messages = []
        if assistant_message:
            additional_messages.append(assistant_message)
        additional_messages.extend(result_messages)

        events: List[ProviderEventResult] = []
        if tool_calls:
            events.append(
                ProviderToolCallEventResult(
                    tool_calls=tool_calls,
                    additional_messages=additional_messages,
                    content="",
                )
            )

        end_turn = jmespath.search("stop_reason=='end_turn' || stop_reason=='max_tokens'", data)
        if end_turn:
            events.append(ProviderFinalEventResult(content=final_text, metadata={"usage": self.usage}))

        return events

    async def post_process_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload


register_adapter("anthropic", AnthropicAdapter)
