import copy
import json
from typing import Any, Dict, List

import jmespath

from shu.models.plugin_execution import CallableTool

from ..adapter_base import (
    BaseProviderAdapter,
    ProviderAdapterContext,
    ProviderCapabilities,
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderReasoningDeltaEventResult,
    ProviderInformation,
    ProviderToolCallEventResult,
    ToolCallInstructions,
    ChatContext,
    ChatMessage,
)


class ResponsesAdapter(BaseProviderAdapter):
    """Base adapter for providers implementing the OpenAI Responses API contract."""

    def __init__(self, context: ProviderAdapterContext):
        super().__init__(context)
        self.function_call_messages: List[Dict[str, Any]] = []
        self.function_call_reasoning_messages: List[Dict[str, Any]] = []

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

    def get_authorization_header(self) -> Dict[str, Any]:
        return {
            "scheme": "bearer",
            "headers": {
                "Authorization": f"Bearer {self.api_key}"
            }
        }

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
    
    def _extract_usage(self, path: str, chunk):
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

    def _format_responses_attachments(self, attachments: List[Any]) -> List[Dict[str, Any]]:
        """Format attachments for OpenAI Responses API.
        
        Uses type: input_text, type: input_image, and type: input_file formats.
        """
        parts: List[Dict[str, Any]] = []
        
        for att in attachments:
            if self._is_image_attachment(att):
                data_uri = self._attachment_to_data_uri(att)
                if data_uri:
                    parts.append({
                        "type": "input_image",
                        "image_url": data_uri
                    })
            elif self.supports_native_documents():
                b64_data = self._read_attachment_base64(att)
                if b64_data:
                    parts.append({
                        "type": "input_file",
                        "filename": att.original_filename,
                        "file_data": f"data:{att.mime_type};base64,{b64_data}",
                    })
                else:
                    fallback = self._attachment_to_input_text_fallback(att)
                    if fallback:
                        parts.append(fallback)
            else:
                fallback = self._attachment_to_input_text_fallback(att)
                if fallback:
                    parts.append(fallback)
        
        return parts

    async def handle_provider_event(self, chunk: Dict[str, Any]) -> ProviderEventResult:
        incomplete_response = jmespath.search("type=='response.incomplete' && response.incomplete_details.reason", chunk)
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
            return ProviderContentDeltaEventResult(content=content_delta)

        reasoning_delta = jmespath.search("type=='response.reasoning_summary_text.delta' && delta", chunk)
        if reasoning_delta:
            return ProviderReasoningDeltaEventResult(content=reasoning_delta)
        
        # Extract usage for this cycle and add to previous cycles
        completion_message = jmespath.search("type=='response.completed'", chunk)
        if completion_message:
            self._extract_usage("response.usage", chunk)

        final_message = jmespath.search("type=='response.completed' && response.output[-1].content[-1].text", chunk)
        if final_message:
            return ProviderFinalEventResult(content=final_message, metadata={"usage": self.usage})

    async def finalize_provider_events(self) -> List[ProviderEventResult]:
        if not self.function_call_messages:
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
            for function_call_message, tool_call in zip(self.function_call_messages, tool_calls)
        ]
        self.function_call_reasoning_messages = []
        self.function_call_messages = []
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

    async def handle_provider_completion(self, data: Dict[str, Any]) -> List[ProviderEventResult]:
        incomplete_response = jmespath.search("status=='incomplete' && incomplete_details.reason", data)
        if incomplete_response:
            return [
                ProviderErrorEventResult(
                    content=f"Got incomplete response from provider because '{incomplete_response}'"
                )
            ]

        # Extract usage for this cycle and add to previous cycles
        self._extract_usage("usage", data)

        final_message = jmespath.search("output[?type=='message'] && output[-1].content[-1].text", data)
        final_messages = [
            ProviderFinalEventResult(content=final_message, metadata={"usage": self.usage})
        ]

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
            for function_call_message, tool_call in zip(function_call_messages, tool_calls)
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
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=additional_messages,
                content="",
            )
        ] + final_messages

    async def inject_tool_payload(self, tools: List[CallableTool], payload: Dict[str, Any]) -> Dict[str, Any]:
        res: List[Dict[str, Any]] = []
        for tool in tools:
            title = None
            if isinstance(tool.enum_labels, dict):
                title = tool.enum_labels.get(str(tool.op))
            fname = f"{tool.name}__{tool.op}"
            op_schema = copy.deepcopy(tool.schema) if tool.schema else {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
            props = op_schema.setdefault("properties", {})
            props["op"] = {
                "type": "string",
                "enum": [tool.op],
                "const": tool.op,
                "default": tool.op,
            }
            if isinstance(op_schema.get("required"), list):
                if "op" not in op_schema["required"]:
                    op_schema["required"].append("op")
            else:
                op_schema["required"] = ["op"]
            description = title or f"Run {tool.name}:{tool.op}"
            tool_entry = {
                "type": "function",
                "name": fname,
                "description": description,
                "function": {
                    "name": fname,
                    "description": description,
                    "parameters": op_schema,
                },
            }
            res.append(tool_entry)
        payload["tools"] = payload.get("tools", []) + res
        return payload

    def _process_message_for_responses_api(self, message: ChatMessage) -> Dict[str, Any]:
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
            content_parts: List[Dict[str, Any]] = []
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

    async def set_messages_in_payload(self, messages: ChatContext, payload: Dict[str, Any]) -> Dict[str, Any]:
        result: List[Dict[str, Any]] = []
        if messages.system_prompt:
            result.append({"role": "system", "content": messages.system_prompt})
        for m in messages.messages:
            result.append(self._process_message_for_responses_api(m))
        payload["input"] = result
        return payload

    async def inject_model_parameter(self, model_value: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["model"] = model_value
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["stream"] = should_stream
        return payload
