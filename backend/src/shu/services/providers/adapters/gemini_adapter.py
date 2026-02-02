import copy
import json
from typing import Any

import jmespath

from shu.core.logging import get_logger
from shu.models.plugin_execution import CallableTool
from shu.services.providers.parameter_definitions import (
    ArrayParameter,
    EnumParameter,
    IntegerParameter,
    NumberParameter,
    ObjectParameter,
    Option,
    StringParameter,
)

from ..adapter_base import (
    BaseProviderAdapter,
    ChatContext,
    ChatMessage,
    ProviderAdapterContext,
    ProviderCapabilities,
    ProviderContentDeltaEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderInformation,
    ProviderToolCallEventResult,
    ToolCallInstructions,
    register_adapter,
)

logger = get_logger(__name__)


class GeminiAdapter(BaseProviderAdapter):
    def __init__(self, context: ProviderAdapterContext) -> None:
        super().__init__(context)
        self._latest_usage_event: dict[str, Any] | None = None
        self._stream_content: list[str] = []
        self._stream_tool_calls: dict[int, dict[str, Any]] = {}

    async def _build_assistant_and_result_messages(
        self, sorted_tool_calls: list[dict[str, Any]], tool_calls: list[ToolCallInstructions]
    ) -> tuple[ChatMessage | None, list[ChatMessage]]:
        assistant_message = (
            ChatMessage.build(role="assistant", content=sorted_tool_calls) if sorted_tool_calls else None
        )
        result_messages = [
            ChatMessage.build(
                role="tool",
                metadata={
                    "tool_call_id": raw.get("id", ""),
                    "name": (raw.get("function") or {}).get("name", ""),
                },
                content=await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
            )
            for raw, tool_call in zip(sorted_tool_calls, tool_calls, strict=False)
        ]
        return assistant_message, result_messages

    def _extract_usage(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usageMetadata") if isinstance(payload, dict) else None
        if not usage:
            return
        self._update_usage(
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0),
            0,  # Gemini doesn't offer this
            usage.get("thoughtsTokenCount", 0),
            usage.get("totalTokenCount", 0),
        )

    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        return ProviderInformation(key="gemini", display_name="Gemini")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def supports_native_documents(self) -> bool:
        """Gemini supports documents via inlineData with any mimeType."""
        return True

    def get_api_base_url(self) -> str:
        return "https://generativelanguage.googleapis.com/v1beta"

    def get_chat_endpoint(self) -> str:
        return "/{model}:{operation}"

    def get_models_endpoint(self) -> str:
        return "/models"

    def get_authorization_header(self) -> dict[str, Any]:
        return {"scheme": "bearer", "headers": {"x-goog-api-key": f"{self.api_key}"}}

    def get_parameter_mapping(self) -> dict[str, Any]:
        return {
            # generationConfig-style knobs
            "temperature": NumberParameter(
                min=0,
                max=2,
                default=1.0,
                label="Temperature",
                description=(
                    "Controls randomness in the model's output. Lower = more deterministic, higher = more creative."
                ),
            ),
            "top_p": NumberParameter(
                min=0,
                max=1,
                default=0.95,
                label="Top P",
                description=(
                    "Nucleus sampling. Tokens are sampled from the smallest set whose cumulative probability >= top_p."
                ),
            ),
            "top_k": NumberParameter(
                min=1,
                max=100,
                default=40,
                label="Top K",
                description=("Top-k sampling. The model only considers the top_k most likely tokens at each step."),
            ),
            "max_output_tokens": IntegerParameter(
                min=1,
                label="Max Output Tokens",
                description=("Maximum number of tokens the model is allowed to generate for this response."),
            ),
            # TODO: We don't support multiple candidates at the moment.
            # "candidate_count": IntegerParameter(
            #     min=1,
            #     max=8,
            #     default=1,
            #     label="Candidate Count",
            #     description=(
            #         "Number of candidate responses to generate. More candidates = more cost and latency."
            #     ),
            # ),
            "stop_sequences": ArrayParameter(
                label="Stop Sequences",
                description=(
                    "One or more strings where generation should stop. If any is generated, the model will stop there."
                ),
                items=StringParameter(placeholder="e.g. </END>", label="Stop sequence"),
            ),
            # Tools (Gemini-side equivalents to web/code tools)
            "tools": ArrayParameter(
                label="Tools",
                description="Built-in tools the model is allowed to use.",
                options=[
                    Option(
                        value={"code_execution": {}},
                        label="Code Execution",
                        help=("Allow the model to write and run code using Gemini's code execution tool."),
                    ),
                    Option(
                        value={"google_search": {}},
                        label="Google Search Retrieval",
                        help=(
                            "Allow the model to use Google Search-style retrieval for up-to-date or long-tail information."
                        ),
                    ),
                ],
            ),
            "tool_config": ObjectParameter(
                label="Tool Config",
                description=(
                    "Controls how the model uses tools (function calling). Maps to tool_config.function_calling_config.mode on Gemini."
                ),
                options=[
                    Option(
                        value={"function_calling_config": {"mode": "AUTO"}},
                        label="Auto",
                        help=("Model decides whether to call tools or answer directly."),
                    ),
                    Option(
                        value={"function_calling_config": {"mode": "ANY"}},
                        label="Any tool (force function call)",
                        help=("Force the model to respond with a tool call when possible."),
                    ),
                    Option(
                        value={"function_calling_config": {"mode": "NONE"}},
                        label="None (no tool calls)",
                        help=("Disable function calling and return natural language only."),
                    ),
                ],
            ),
            # Safety
            "safety_settings": ArrayParameter(
                label="Safety Settings",
                description=(
                    "Per-category safety thresholds. Each entry sets a block threshold for a specific harm category."
                ),
                items=ObjectParameter(
                    label="Safety Setting",
                    properties={
                        "category": EnumParameter(
                            label="Category",
                            description="Safety category this setting applies to.",
                            options=[
                                Option(
                                    value="HARM_CATEGORY_HATE_SPEECH",
                                    label="Derogatory / hate",
                                ),
                                Option(
                                    value="HARM_CATEGORY_HARASSMENT",
                                    label="Harassment",
                                ),
                                Option(
                                    value="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                                    label="Sexual content",
                                ),
                                Option(
                                    value="HARM_CATEGORY_DANGEROUS_CONTENT",
                                    label="Dangerous content",
                                ),
                                Option(
                                    value="HARM_CATEGORY_CIVIC_INTEGRITY",
                                    label="Civic integrity",
                                ),
                            ],
                        ),
                        "threshold": EnumParameter(
                            label="Block Threshold",
                            description=("How aggressively to block content in this category."),
                            options=[
                                Option(value="BLOCK_NONE", label="Block none"),
                                Option(
                                    value="BLOCK_LOW_AND_ABOVE",
                                    label="Block low and above",
                                ),
                                Option(
                                    value="BLOCK_MEDIUM_AND_ABOVE",
                                    label="Block medium and above",
                                ),
                                Option(
                                    value="BLOCK_ONLY_HIGH",
                                    label="Block only high",
                                ),
                            ],
                            default="BLOCK_MEDIUM_AND_ABOVE",
                        ),
                    },
                ),
            ),
            # Labels / metadata
            "labels": ObjectParameter(
                label="Labels",
                description=(
                    "Key-value string labels attached to the request (for billing, routing, or analytics). Maps to 'labels' in the Gemini request."
                ),
            ),
        }

    def _build_tool_result_part(self, msg: ChatMessage):
        """Build Gemini functionResponse part from a ChatMessage with role='tool'."""
        raw_content = getattr(msg, "content", "")
        metadata = getattr(msg, "metadata", {}) or {}
        response_obj: dict[str, Any]
        if isinstance(raw_content, dict):
            response_obj = raw_content
        else:
            try:
                response_obj = json.loads(raw_content) if raw_content else {}
            except Exception:
                response_obj = {"result": raw_content}
        parts = [
            {
                "functionResponse": {
                    "name": metadata.get("name") or "",
                    "response": response_obj if isinstance(response_obj, dict) else {},
                }
            }
        ]
        role = "user"
        return role, parts

    def _build_tool_request_part(self, tc):
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments", "")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except Exception:
            args = {}
        function_call: dict[str, Any] = {
            "name": fn.get("name", ""),
            "args": args if isinstance(args, dict) else {},
        }

        thought = fn.get("thought")
        if thought:
            function_call["thought"] = thought

        part = {"functionCall": function_call}
        thought_signature = fn.get("thoughtSignature")
        if thought_signature:
            part["thoughtSignature"] = thought_signature
        return part

    def _format_attachments_for_parts(self, attachments: list[Any]) -> list[dict[str, Any]]:
        """Format attachments into Gemini API parts.

        Gemini uses inlineData format for both images and documents.
        The format is the same regardless of file type.
        """
        parts: list[dict[str, Any]] = []

        for att in attachments:
            # Gemini uses inlineData format for all file types
            b64_data = self._read_attachment_base64(att)
            if b64_data:
                parts.append({"inlineData": {"mimeType": att.mime_type, "data": b64_data}})
            else:
                # Fallback if file read fails - use base class text format
                fallback = self._attachment_to_text_fallback(att)
                if fallback:
                    # Convert from OpenAI format to Gemini format
                    parts.append({"text": fallback.get("text", "")})

        return parts

    async def set_messages_in_payload(self, messages: ChatContext, payload: dict[str, Any]) -> dict[str, Any]:
        system_parts: list[dict[str, Any]] = []
        contents: list[dict[str, Any]] = []

        for msg in messages.messages:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")
            attachments = getattr(msg, "attachments", []) or []
            parts: list[dict[str, Any]] = []

            # Handle tool result messages - these need special Gemini formatting
            if role == "tool":
                role, parts = self._build_tool_result_part(msg)
            else:
                if isinstance(content, str) and content:
                    parts.append({"text": content})
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text" or "text" in part:
                                text = part.get("text", "")
                                if text:
                                    parts.append({"text": text})
                            elif "function" in part or "id" in part:
                                # Tool call
                                if role in ("assistant", "model"):
                                    parts.append(self._build_tool_request_part(part))

                # Add attachments for user messages
                if role == "user" and attachments:
                    parts.extend(self._format_attachments_for_parts(attachments))

            if not parts:
                continue

            contents.append(
                {
                    "role": "user" if role == "user" else "model" if role in ("assistant", "model") else role,
                    "parts": parts,
                }
            )

        if messages.system_prompt:
            system_parts.append({"text": messages.system_prompt})

        if system_parts:
            payload["system_instruction"] = {"parts": system_parts}

        payload["contents"] = contents
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: dict[str, Any]) -> dict[str, Any]:
        # There is no streaming parameter, we use the operation in the request parameter for this.
        return payload

    async def post_process_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Move generation knobs under generationConfig as Gemini expects
        gen_keys = ["temperature", "top_p", "top_k", "max_output_tokens", "stop_sequences"]
        generation_config = dict(payload.get("generationConfig") or {})
        for k in gen_keys:
            if k in payload:
                generation_config[k] = payload.pop(k)
        if generation_config:
            payload["generationConfig"] = generation_config

        return payload

    def _sanitize_schema_for_gemini(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Strip unsupported keys and coerce schema to Gemini's limited Schema shape."""

        def _clean(obj: Any) -> dict[str, Any]:
            if not isinstance(obj, dict):
                return {}

            result: dict[str, Any] = {}

            t = obj.get("type")
            if isinstance(t, list):
                t = next((x for x in t if isinstance(x, str)), None)
            if isinstance(t, str) and t in {
                "object",
                "string",
                "number",
                "integer",
                "boolean",
                "array",
            }:
                result["type"] = t

            desc = obj.get("description")
            if isinstance(desc, str):
                result["description"] = desc

            enum_vals = obj.get("enum")
            if isinstance(enum_vals, list):
                filtered = [v for v in enum_vals if isinstance(v, (str, int, float, bool))]
                if filtered:
                    result["enum"] = filtered

            if result.get("type") == "array":
                items = obj.get("items")
                if isinstance(items, dict):
                    cleaned_items = _clean(items)
                    if cleaned_items:
                        result["items"] = cleaned_items

            if result.get("type") == "object":
                props = obj.get("properties")
                if isinstance(props, dict):
                    cleaned_props: dict[str, Any] = {}
                    for pk, pv in props.items():
                        if not isinstance(pk, str) or not isinstance(pv, dict):
                            continue
                        cleaned_prop = _clean(pv)
                        if cleaned_prop:
                            cleaned_props[pk] = cleaned_prop
                    if cleaned_props:
                        result["properties"] = cleaned_props

                req = obj.get("required")
                if isinstance(req, list):
                    req_list = [r for r in req if isinstance(r, str)]
                    if req_list:
                        result["required"] = req_list

            return result

        return _clean(schema or {})

    async def inject_tool_payload(self, tools: list[CallableTool], payload: dict[str, Any]) -> dict[str, Any]:
        res: list[dict[str, Any]] = []
        for tool in tools:
            title = None
            if isinstance(tool.enum_labels, dict):
                title = tool.enum_labels.get(str(tool.op))
            fname = f"{tool.name}__{tool.op}"
            op_schema = copy.deepcopy(tool.schema) if tool.schema else {"type": "object", "properties": {}}
            props = op_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [tool.op],
                "description": "Operation name (fixed)",
            }
            req = op_schema.get("required")
            if isinstance(req, list):
                if "op" not in req:
                    req.append("op")
            else:
                op_schema["required"] = ["op"]

            sanitized = self._sanitize_schema_for_gemini(op_schema)
            if not sanitized:
                sanitized = {
                    "type": "object",
                    "properties": {"op": {"type": "string", "enum": [tool.op]}},
                }

            description = title or f"Run {tool.name}:{tool.op}"
            tool_entry = {
                "name": fname,
                "description": description,
                "parameters": sanitized,
            }
            res.append(tool_entry)
        if res:
            # TODO: Gemini currently does not support grounding tools and functions at the same time.
            #       We'll have to solve this in a different way. For now we just override the existing configs here.
            # payload["tools"] = payload.get("tools", []) + [{"function_declarations": res}]
            payload["tools"] = [{"function_declarations": res}]
        return payload

    def inject_override_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params or {})
        params["operation"] = "generateContent"
        if params.get("stream"):
            params["operation"] = "streamGenerateContent?alt=sse"
        return params

    def _extract_function_call(self, fc: dict[str, Any], thought_signature: str | None = None) -> dict[str, Any]:
        """Normalize Gemini function call data for reuse in follow-up requests."""
        args = fc.get("args")
        if args is None:
            args = fc.get("arguments") or {}
        tool_call = {
            "function": {
                "name": fc.get("name", ""),
                "arguments": json.dumps(args) if not isinstance(args, str) else args,
            }
        }

        thought_signature = thought_signature or fc.get("thoughtSignature")
        if thought_signature:
            tool_call["function"]["thoughtSignature"] = thought_signature

        thought = fc.get("thought")
        if thought:
            tool_call["function"]["thought"] = thought

        call_id = fc.get("id")
        if call_id:
            tool_call["id"] = call_id

        return tool_call

    def _tool_call_from_raw(self, tool_call: dict[str, Any]) -> ToolCallInstructions:
        function_data = tool_call.get("function") or {}
        tool_name = function_data.get("name", "")
        try:
            plugin_name, op = tool_name.split("__", 1)
        except ValueError:
            plugin_name, op = tool_name, ""

        args_raw = function_data.get("arguments", "")
        try:
            args_dict = json.loads(args_raw) if args_raw else {}
        except Exception:
            args_dict = {}

        return ToolCallInstructions(plugin_name=plugin_name, operation=op, args_dict=args_dict)

    def get_model_information_path(self) -> str:
        return "models[?contains(supportedGenerationMethods, 'generateContent')].{id: name, name: displayName}"

    async def handle_provider_event(self, chunk: dict[str, Any]) -> ProviderEventResult | None:
        parts = jmespath.search("candidates[0].content.parts", chunk) or []
        if not parts:
            return None

        if "usageMetadata" in chunk:
            self._latest_usage_event = chunk

        for part in parts:
            if "text" in part:
                text_piece = part.get("text", "")
                if text_piece:
                    self._stream_content.append(text_piece)
                    return ProviderContentDeltaEventResult(content=text_piece)
            elif "functionCall" in part:
                idx = len(self._stream_tool_calls)
                self._stream_tool_calls[idx] = self._extract_function_call(
                    part.get("functionCall", {}),
                    thought_signature=part.get("thoughtSignature"),
                )
        return None

    async def finalize_provider_events(self) -> list[ProviderEventResult]:
        self._extract_usage(self._latest_usage_event)
        self._latest_usage_event = None

        final_text = "".join(self._stream_content)
        final_event = [ProviderFinalEventResult(content=final_text, metadata={"usage": self.usage})]
        if not self._stream_tool_calls:
            return final_event

        sorted_calls = [self._stream_tool_calls[k] for k in sorted(self._stream_tool_calls.keys())]
        tool_calls = [self._tool_call_from_raw(tc) for tc in sorted_calls]

        assistant_message, result_messages = await self._build_assistant_and_result_messages(sorted_calls, tool_calls)

        self._stream_tool_calls = {}
        self._stream_content = []

        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=[m for m in [assistant_message, *result_messages] if m],
                content="",
            ),
            *final_event,
        ]

    async def handle_provider_completion(self, data: dict[str, Any]) -> list[ProviderEventResult]:
        candidates = data.get("candidates") or []
        if not candidates:
            return [ProviderFinalEventResult(content="")]

        self._extract_usage(data)

        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []

        text_parts: list[str] = []
        raw_tool_calls: list[dict[str, Any]] = []
        for part in parts:
            if "text" in part:
                text_parts.append(part.get("text", ""))
            elif "functionCall" in part:
                raw_tool_calls.append(
                    self._extract_function_call(
                        part.get("functionCall", {}),
                        thought_signature=part.get("thoughtSignature"),
                    )
                )

        content_text = "".join(text_parts)
        tool_calls = [self._tool_call_from_raw(tc) for tc in raw_tool_calls]

        assistant_message, result_messages = await self._build_assistant_and_result_messages(raw_tool_calls, tool_calls)

        events: list[ProviderEventResult] = []
        if tool_calls:
            additional_messages = [m for m in [assistant_message, *result_messages] if m]
            events.append(
                ProviderToolCallEventResult(
                    tool_calls=tool_calls,
                    additional_messages=additional_messages,
                    content="",
                )
            )

        events.append(ProviderFinalEventResult(content=content_text, metadata={"usage": self.usage}))
        return events


register_adapter("gemini", GeminiAdapter)
