import copy
import json
from typing import Any

import jmespath

from shu.models.plugin_execution import CallableTool

from ..adapter_base import (
    BaseProviderAdapter,
    ChatContext,
    ChatMessage,
    ProviderAdapterContext,
    ProviderCapabilities,
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderInformation,
    ProviderReasoningDeltaEventResult,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)


class ResponsesAdapter(BaseProviderAdapter):
    """Base adapter for providers implementing the OpenAI Responses API contract."""

    def __init__(self, context: ProviderAdapterContext) -> None:
        super().__init__(context)
        self.function_call_messages: list[dict[str, Any]] = []
        self.function_call_reasoning_messages: list[dict[str, Any]] = []
        self._streamed_text: list[str] = []

    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        raise NotImplementedError("Function get_provider_information is not implemented.")

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, tools=True, vision=True)

    def supports_native_documents(self) -> bool:
        """OpenAI Responses API supports native file uploads."""
        return True

    def get_api_base_url(self) -> str:
        raise NotImplementedError("Function get_api_base_url is not implemented.")

    def get_chat_endpoint(self) -> str:
        return "/responses"

    def get_models_endpoint(self) -> str:
        return "/models"

    def get_model_information_path(self) -> str:
        return "data[*].{id: id, name: id}"

    def get_authorization_header(self) -> dict[str, Any]:
        return {"scheme": "bearer", "headers": {"Authorization": f"Bearer {self.api_key}"}}

    def _get_function_call_arguments_from_response(self, stream_event: dict) -> ToolCallInstructions:
        if isinstance(stream_event, list):
            stream_event = stream_event[0] if stream_event else {}
        elif not isinstance(stream_event, dict):
            stream_event = {}

        tool_name, arguments_raw = stream_event.get("name"), stream_event.get("arguments")

        try:
            plugin_name, op = tool_name.split("__", 1)
        except ValueError:
            plugin_name, op = tool_name, ""

        try:
            args_dict = json.loads(arguments_raw)
        except Exception:
            args_dict = {}

        return ToolCallInstructions(plugin_name=plugin_name, operation=op, args_dict=args_dict)

    def _extract_usage(self, path: str, chunk) -> None:
        usage = jmespath.search(path, chunk) or {}
        if not usage:
            return
        self._update_usage(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("input_tokens_details", {}).get("cached_tokens", 0),
            usage.get("output_tokens_details", {}).get("reasoning_tokens", 0),
            usage.get("total_tokens", 0),
        )

    def _format_responses_attachments(self, attachments: list[Any]) -> list[dict[str, Any]]:
        """Format attachments for OpenAI Responses API.

        Uses type: input_text, type: input_image, and type: input_file formats.
        """
        parts: list[dict[str, Any]] = []

        for att in attachments:
            if self._is_image_attachment(att):
                data_uri = self._attachment_to_data_uri(att)
                if data_uri:
                    parts.append({"type": "input_image", "image_url": data_uri})
            elif self.supports_native_documents():
                b64_data = self._read_attachment_base64(att)
                if b64_data:
                    parts.append(
                        {
                            "type": "input_file",
                            "filename": att.original_filename,
                            "file_data": f"data:{att.mime_type};base64,{b64_data}",
                        }
                    )
                else:
                    fallback = self._attachment_to_input_text_fallback(att)
                    if fallback:
                        parts.append(fallback)
            else:
                fallback = self._attachment_to_input_text_fallback(att)
                if fallback:
                    parts.append(fallback)

        return parts

    async def handle_provider_event(self, chunk: dict[str, Any]) -> ProviderEventResult | None:
        incomplete_response = jmespath.search(
            "type=='response.incomplete' && response.incomplete_details.reason", chunk
        )
        if incomplete_response:
            return ProviderErrorEventResult(
                content=f"Got incomplete response from provider because '{incomplete_response}'"
            )

        function_call_reasoning_result = jmespath.search(
            "type=='response.output_item.done' && item.type=='reasoning' && item", chunk
        )
        if function_call_reasoning_result:
            self.function_call_reasoning_messages.append(function_call_reasoning_result)
            return None

        function_call_result = jmespath.search(
            "type=='response.output_item.done' && item.type=='function_call' && item", chunk
        )
        if function_call_result:
            self.function_call_messages.append(function_call_result)
            return None

        content_delta = jmespath.search("type=='response.output_text.delta' && delta", chunk)
        if content_delta:
            self._streamed_text.append(content_delta)
            return ProviderContentDeltaEventResult(content=content_delta)

        reasoning_delta = jmespath.search("type=='response.reasoning_summary_text.delta' && delta", chunk)
        if reasoning_delta:
            return ProviderReasoningDeltaEventResult(content=reasoning_delta)

        if chunk.get("type") == "response.completed":
            self._extract_usage("response.usage", chunk)

            # Scan output items for the message regardless of position — reasoning models may place
            # a reasoning summary item after the message, making output[-1] point to it instead.
            final_text: str | None = None
            for item in reversed((chunk.get("response") or {}).get("output") or []):
                if item.get("type") == "message":
                    content_list = item.get("content") or []
                    if content_list:
                        final_text = content_list[-1].get("text")
                    break

            # Fallback: reconstruct from streamed deltas when the output array is missing/malformed.
            if not final_text and self._streamed_text:
                final_text = "".join(self._streamed_text)

            self._streamed_text = []

            if final_text:
                return ProviderFinalEventResult(content=final_text, metadata={"usage": self.usage})

        return None

    async def finalize_provider_events(self) -> list[ProviderEventResult]:
        if not self.function_call_messages:
            self.function_call_reasoning_messages = []
            return []

        function_call_reasoning_messages = self.function_call_reasoning_messages
        function_call_messages = self.function_call_messages
        tool_calls = list(map(self._get_function_call_arguments_from_response, self.function_call_messages))
        result_messages = [
            ChatMessage.build(
                role="tool",
                content=await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
                metadata={
                    "type": "function_call_output",
                    "call_id": function_call_message.get("call_id", ""),
                },
            )
            for function_call_message, tool_call in zip(self.function_call_messages, tool_calls, strict=False)
        ]
        self.function_call_reasoning_messages = []
        self.function_call_messages = []
        self._streamed_text = []
        additional_messages = [
            ChatMessage.build(
                role=msg.get("role", "") or "assistant",
                content=msg,
                id=msg.get("id"),
                created_at=msg.get("created_at"),
                attachments=[],
                metadata={"type": msg.get("type")},
            )
            for msg in (function_call_reasoning_messages + function_call_messages)
        ] + result_messages
        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=additional_messages,
                content="",
            )
        ]

    async def handle_provider_completion(self, data: dict[str, Any]) -> list[ProviderEventResult]:
        incomplete_response = jmespath.search("status=='incomplete' && incomplete_details.reason", data)
        if incomplete_response:
            return [
                ProviderErrorEventResult(
                    content=f"Got incomplete response from provider because '{incomplete_response}'"
                )
            ]

        # Extract usage for this cycle and add to previous cycles
        self._extract_usage("usage", data)

        final_message = jmespath.search("output[?type=='message'] | [-1].content[-1].text", data)
        final_messages = [ProviderFinalEventResult(content=final_message, metadata={"usage": self.usage})]

        function_call_messages = jmespath.search("output[?type=='function_call']", data) or []
        if not function_call_messages:
            return final_messages

        function_call_reasoning_messages = jmespath.search("output[?type=='reasoning']", data) or []
        tool_calls = list(map(self._get_function_call_arguments_from_response, function_call_messages))
        result_messages = [
            ChatMessage.build(
                role="tool",
                content=await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
                metadata={
                    "type": "function_call_output",
                    "call_id": function_call_message.get("call_id", ""),
                },
            )
            for function_call_message, tool_call in zip(function_call_messages, tool_calls, strict=False)
        ]

        additional_messages = [
            ChatMessage.build(
                role=msg.get("role", "") or "assistant",
                content=msg,
                id=msg.get("id"),
                created_at=msg.get("created_at"),
                attachments=[],
                metadata={"type": msg.get("type")},
            )
            for msg in (function_call_reasoning_messages + function_call_messages)
        ] + result_messages
        return [
            ProviderToolCallEventResult(tool_calls=tool_calls, additional_messages=additional_messages, content=""),
            *final_messages,
        ]

    def _sanitize_schema_for_responses_api(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Coerce a JSON Schema to the subset accepted by the OpenAI Responses API.

        The Responses API rejects several standard JSON Schema features:
        - ``"type"`` as a list (e.g. ``["string", "null"]``) must be a single string.
        - ``"const"`` is not supported; use ``"enum"`` with one value instead.
        - ``"default"`` is stripped (informational only, not part of the wire schema).
        """

        def _clean(obj: Any) -> dict[str, Any]:
            if not isinstance(obj, dict):
                return {}

            result: dict[str, Any] = {}

            # Flatten array types: ["string", "null"] → "string"
            t = obj.get("type")
            if isinstance(t, list):
                t = next((x for x in t if isinstance(x, str) and x != "null"), None)
            if isinstance(t, str):
                result["type"] = t

            if "description" in obj:
                result["description"] = obj["description"]

            if "enum" in obj:
                result["enum"] = obj["enum"]
            # "const" is not supported — already covered by single-value "enum" above

            if "minimum" in obj:
                result["minimum"] = obj["minimum"]
            if "maximum" in obj:
                result["maximum"] = obj["maximum"]

            if result.get("type") == "array" and "items" in obj:
                cleaned = _clean(obj["items"])
                if cleaned:
                    result["items"] = cleaned

            if result.get("type") == "object":
                if "properties" in obj and isinstance(obj["properties"], dict):
                    cleaned_props = {k: _clean(v) for k, v in obj["properties"].items() if isinstance(v, dict)}
                    cleaned_props = {k: v for k, v in cleaned_props.items() if v}
                    if cleaned_props:
                        result["properties"] = cleaned_props

                if "required" in obj and isinstance(obj["required"], list):
                    req = [r for r in obj["required"] if isinstance(r, str)]
                    if req:
                        result["required"] = req

                if "additionalProperties" in obj:
                    result["additionalProperties"] = obj["additionalProperties"]

            return result

        return _clean(schema)

    async def inject_tool_payload(self, tools: list[CallableTool], payload: dict[str, Any]) -> dict[str, Any]:
        res: list[dict[str, Any]] = []
        for tool in tools:
            title = None
            if isinstance(tool.enum_labels, dict):
                title = tool.enum_labels.get(str(tool.op))
            fname = f"{tool.name}__{tool.op}"
            op_schema = (
                copy.deepcopy(tool.schema)
                if tool.schema
                else {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }
            )
            props = op_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [tool.op],
            }
            if isinstance(op_schema.get("required"), list):
                if "op" not in op_schema["required"]:
                    op_schema["required"].append("op")
            else:
                op_schema["required"] = ["op"]

            sanitized = self._sanitize_schema_for_responses_api(op_schema)

            description = title or f"Run {tool.name}:{tool.op}"
            # Responses API uses a flat tool format: name/description/parameters at the top level.
            # (Unlike Chat Completions API which nests them under a "function" key.)
            tool_entry = {
                "type": "function",
                "name": fname,
                "description": description,
                "parameters": sanitized,
            }
            res.append(tool_entry)
        payload["tools"] = payload.get("tools", []) + res
        return payload

    def _process_message_for_responses_api(self, message: ChatMessage) -> dict[str, Any]:
        """Convert ChatMessage to Responses API format, handling special message types."""
        metadata = getattr(message, "metadata", {}) or {}
        content = getattr(message, "content", "")
        role = getattr(message, "role", "")
        attachments = getattr(message, "attachments", []) or []

        # Handle function_call_output messages - these are special API objects
        if metadata.get("type") == "function_call_output":
            return {
                "type": "function_call_output",
                "call_id": metadata.get("call_id", ""),
                "output": content if isinstance(content, str) else str(content),
            }

        # Handle reasoning and function_call messages - pass through as-is
        if metadata.get("type") in ("reasoning", "function_call") and isinstance(content, dict):
            return content

        # Handle user messages with attachments (multimodal)
        if role == "user" and attachments:
            content_parts: list[dict[str, Any]] = []
            # Add text content first
            if isinstance(content, str) and content:
                content_parts.append({"type": "input_text", "text": content})
            elif isinstance(content, list):
                content_parts.extend(content)

            # Add formatted attachments
            content_parts.extend(self._format_responses_attachments(attachments))

            return {"role": role, "content": content_parts if content_parts else content}

        # Standard role/content message
        return {"role": role, "content": content}

    async def set_messages_in_payload(self, messages: ChatContext, payload: dict[str, Any]) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        if messages.system_prompt:
            result.append({"role": "system", "content": messages.system_prompt})
        for m in messages.messages:
            result.append(self._process_message_for_responses_api(m))
        payload["input"] = result
        return payload

    async def inject_model_parameter(self, model_value: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload["model"] = model_value
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: dict[str, Any]) -> dict[str, Any]:
        payload["stream"] = should_stream
        return payload
