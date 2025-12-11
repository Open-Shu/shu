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
        return ProviderCapabilities(streaming=True, tools=True, vision=False)

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
            {
                "type": "function_call_output",
                "call_id": function_call_message.get("call_id", ""),
                "output": await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
            }
            for function_call_message, tool_call in zip(self.function_call_messages, tool_calls)
        ]
        self.function_call_reasoning_messages = []
        self.function_call_messages = []
        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=function_call_reasoning_messages + function_call_messages + result_messages,
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
            {
                "type": "function_call_output",
                "call_id": function_call_message.get("call_id", ""),
                "output": await self._call_plugin(tool_call.plugin_name, tool_call.operation, tool_call.args_dict),
            }
            for function_call_message, tool_call in zip(function_call_messages, tool_calls)
        ]

        return [
            ProviderToolCallEventResult(
                tool_calls=tool_calls,
                additional_messages=function_call_reasoning_messages + function_call_messages + result_messages,
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

    async def set_messages_in_payload(self, messages: List[Dict[str, str]], payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["input"] = messages
        return payload

    async def inject_model_parameter(self, model_value: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["model"] = model_value
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload["stream"] = should_stream
        return payload
