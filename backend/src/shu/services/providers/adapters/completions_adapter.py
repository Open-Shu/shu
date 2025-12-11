import copy
import json
from typing import Any, Dict, List, Optional

import jmespath

from shu.core.logging import get_logger
from shu.models.plugin_execution import CallableTool

from ..adapter_base import (
    BaseProviderAdapter,
    ProviderAdapterContext,
    ProviderCapabilities,
    ProviderContentDeltaEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderInformation,
    ProviderToolCallEventResult,
    ToolCallInstructions,
)

logger = get_logger(__name__)


class CompletionsAdapter(BaseProviderAdapter):
    """Base adapter for providers implementing OpenAI-style /v1/chat/completions."""

    def __init__(self, context: ProviderAdapterContext):
        super().__init__(context)
        self.latest_usage_event = None
        self._stream_content: List[str] = []
        self._function_call_messages: Dict[int, Dict[str, Any]] = {}
        self._stream_finished = None

    # General provider information
    def get_provider_information(self) -> ProviderInformation:
        raise NotImplementedError("Function get_provider_information is not implemented.")

    def get_capabilities(self) -> ProviderCapabilities:
        # Most OpenAI-compatible chat providers support streaming; tool support is provider-specific but allowed here.
        return ProviderCapabilities(streaming=True, tools=True, vision=False)

    def get_api_base_url(self) -> str:
        raise NotImplementedError("Function get_api_base_url is not implemented.")

    def get_chat_endpoint(self) -> str:
        return "/chat/completions"

    def get_models_endpoint(self) -> str:
        return "/models"

    def get_authorization_header(self) -> Dict[str, Any]:
        return {
            "scheme": "bearer",
            "headers": {
                "Authorization": f"Bearer {self.api_key}"
            }
        }

    def get_model_information_path(self) -> str:
        return "data[*].{id: id, name: id}"

    def _tool_call_to_instructions(self, call: Dict[str, Any]) -> ToolCallInstructions:
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or ""
        args_raw = fn.get("arguments") or call.get("arguments") or "{}"

        try:
            plugin_name, op = name.split("__", 1)
        except ValueError:
            plugin_name, op = name, ""

        try:
            args_dict = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except Exception:
            args_dict = {}

        return ToolCallInstructions(plugin_name=plugin_name, operation=op, args_dict=args_dict)

    def _merge_tool_call_deltas(self, function_call: Dict[str, Any]):

        index = function_call.get("index")
        function = function_call.get("function", {})

        if index not in self._function_call_messages:
            del function_call["index"]
            self._function_call_messages[index] = function_call
        else:
            existing_function = self._function_call_messages[index].setdefault("function", {})
            for key, value in function.items():
                if key == "index":
                    continue
                # Initialize missing keys before concatenating incremental chunks
                if key not in existing_function:
                    existing_function[key] = value
                else:
                    existing_function[key] += value
    
    def _transform_function_messages(self, messages):
        return {
            "role": "assistant",
            "tool_calls": messages
        }

    def _extract_usage(self, path: str, chunk):
        usage = jmespath.search(path, chunk) or {}
        if not usage:
            return
        self._update_usage(
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            usage.get("total_tokens", 0),
        )

    def get_finish_reason_path(self):
        return "(object == 'chat.completion' || object == 'chat.completion.chunk') && choices[*].finish_reason | [0]"

    async def handle_provider_event(self, chunk: Dict[str, Any]) -> Optional[ProviderEventResult]:
        """Handle streaming chat completion deltas."""

        content_delta = jmespath.search("object == 'chat.completion.chunk' && choices[*].delta.content | [0]", chunk)
        if content_delta:
            self._stream_content.append(content_delta)
            return ProviderContentDeltaEventResult(content=content_delta)

        # Some providers return incremental usage statistics, we only consider the last one.
        if "usage" in chunk:
            self.latest_usage_event = chunk
        
        finish_reason = jmespath.search(self.get_finish_reason_path(), chunk)
        if finish_reason in set(["stop", "length"]):
            self._stream_finished = finish_reason
        
        function_calls = jmespath.search("object == 'chat.completion.chunk' && choices[*].delta.tool_calls | []", chunk)
        if function_calls:
            for function_call in function_calls:
                self._merge_tool_call_deltas(function_call)

    async def finalize_provider_events(self) -> List[ProviderEventResult]:

        final_text = "".join(self._stream_content)
        self._stream_content = []

        # If the provider interrupts for length, we may not get any response at all.
        if self._stream_finished == "length" and not final_text:
            final_text = "No response received because the output exceeded the maximum tokens."
        self._stream_finished = None

        # Extract usage for this cycle and add to previous cycles
        self._extract_usage("usage", self.latest_usage_event)
        self.latest_usage_event = None

        final_event = ProviderFinalEventResult(content=final_text, metadata={"usage": self.usage})

        if not self._function_call_messages:
            return [final_event]
        
        function_call_messages = list(self._function_call_messages.values())

        tool_calls = list(map(self._tool_call_to_instructions, self._function_call_messages.values()))
        result_messages = [
            {
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
            }
            for call, tool_call in zip(self._function_call_messages.values(), tool_calls)
        ]

        self._function_call_messages = {}

        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=[self._transform_function_messages(function_call_messages)] + result_messages,
                content="",
            ),
            final_event
        ]

    async def handle_provider_completion(self, data: Dict[str, Any]) -> List[ProviderEventResult]:
 
        finish_reason = jmespath.search(self.get_finish_reason_path(), data)
        final_message = jmespath.search("object == 'chat.completion' && choices[0].message.content", data)

        # If the provider interrupts for length, we may not get any response at all.
        if finish_reason == "length" and not final_message:
            final_message = "No response received because the output exceeded the maximum tokens."

        # Extract usage for this cycle and add to previous cycles
        self._extract_usage("usage", data)

        final_messages = [
            ProviderFinalEventResult(content=final_message, metadata={"usage": self.usage})
        ]

        function_call_messages = jmespath.search("object == 'chat.completion' && choices[0].message.tool_calls", data) or []
        if not function_call_messages:
            return final_messages
        
        tool_calls = list(map(self._tool_call_to_instructions, function_call_messages))
        result_messages = [
            {
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
            }
            for call, tool_call in zip(function_call_messages, tool_calls)
        ]

        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=[self._transform_function_messages(function_call_messages)] + result_messages,
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
        if res:
            payload["tools"] = payload.get("tools", []) + res
        return payload

    async def set_messages_in_payload(self, messages: List[Dict[str, str]], payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["messages"] = messages
        return payload

    async def inject_model_parameter(self, model_value: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["model"] = model_value
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["stream"] = should_stream
        # Inject usage request parameter
        if should_stream:
            payload["stream_options"] = { "include_usage": True }
        return payload

    async def post_process_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload
