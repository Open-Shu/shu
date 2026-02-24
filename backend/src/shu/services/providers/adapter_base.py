"""Provider adapter base interface and factory.

Adapters encapsulate provider-specific behavior for chat/tool calls.
This scaffolding intentionally avoids runtime wiring until concrete adapters land.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.config import get_settings_instance
from shu.core.exceptions import LLMConfigurationError
from shu.core.logging import get_logger
from shu.models.attachment import Attachment
from shu.models.llm_provider import LLMProvider
from shu.models.plugin_execution import CallableTool
from shu.services.chat_types import ChatContext, ChatMessage
from shu.services.plugin_execution import execute_plugin
from shu.services.providers.events import ProviderStreamEvent

logger = get_logger(__name__)


@dataclass
class ProviderAdapterContext:
    """Execution context passed to adapters.

    Attributes:
        provider: LLMProvider ORM row
        model_configuration: ModelConfiguration ORM row
        encryption_key: Fernet key used to decrypt provider API keys
        knowledge_base_ids: Optional list of knowledge base IDs scoped to this
            request. When set, plugin tool calls will be restricted to these
            knowledge bases.

    """

    db_session: AsyncSession
    provider: LLMProvider | None = None
    conversation_owner_id: str | None = None
    knowledge_base_ids: list[str] | None = None


@dataclass
class ProviderInformation:
    key: str
    display_name: str


@dataclass
class ProviderCapabilities:
    streaming: bool = False
    tools: bool = False
    vision: bool = False

    def to_dict(self, include_disabled: bool = False, supported_mask: ProviderCapabilities | None = None):
        capabilities = {
            "streaming": {"value": self.streaming, "label": "Supports Streaming"},
            "tools": {"value": self.tools, "label": "Supports Tool Calling"},
            "vision": {"value": self.vision, "label": "Supports Vision"},
        }

        if supported_mask:
            if not supported_mask.streaming:
                capabilities.pop("streaming", None)
            if not supported_mask.tools:
                capabilities.pop("tools", None)
            if not supported_mask.vision:
                capabilities.pop("vision", None)

        if include_disabled:
            return capabilities

        # Hide capabilities whose value is false
        return {k: v for k, v in capabilities.items() if v.get("value")}

    @classmethod
    def from_request_dict(cls, request_dict) -> Self:
        return ProviderCapabilities(
            streaming=request_dict.get("streaming", {}).get("value", False),
            tools=request_dict.get("tools", {}).get("value", False),
            vision=request_dict.get("vision", {}).get("value", False),
        )


@dataclass
class ToolCallInstructions:
    plugin_name: str
    operation: str
    args_dict: dict[str, Any]


@dataclass
class ProviderEventResult:
    type: str
    content: Any


@dataclass(kw_only=True)
class ProviderToolCallEventResult(ProviderEventResult):
    tool_calls: list[ToolCallInstructions]
    additional_messages: list[ChatMessage]
    content: Any | None
    type: str = "function_call"

    def to_provider_stream_event(self, model, provider, metadata) -> ProviderStreamEvent:
        return ProviderStreamEvent(
            type=self.type,
            content={
                "tool_calls": self.tool_calls,
                "additional_messages": self.additional_messages,
                "content": self.content,
            },
            model_name=model,
            provider_name=provider,
            metadata=metadata,
        )


@dataclass(kw_only=True)
class ProviderContentDeltaEventResult(ProviderEventResult):
    content: str
    type: str = "content_delta"


@dataclass(kw_only=True)
class ProviderReasoningDeltaEventResult(ProviderEventResult):
    content: str
    type: str = "reasoning_delta"


@dataclass(kw_only=True)
class ProviderFinalEventResult(ProviderEventResult):
    content: str
    type: str = "final_message"
    metadata: dict[str, Any] | None = None


@dataclass(kw_only=True)
class ProviderErrorEventResult(ProviderEventResult):
    content: str
    type: str = "error"


class BaseProviderAdapter:
    """Base class for provider adapters.

    Subclasses override helpers to translate Shu requests into provider-specific
    payloads and to parse streaming/tool outputs.

    """

    def __init__(self, context: ProviderAdapterContext) -> None:
        self.provider = context.provider
        self.conversation_owner_id = context.conversation_owner_id
        self.knowledge_base_ids = context.knowledge_base_ids
        self.settings = get_settings_instance()
        self.encryption_key = self.settings.llm_encryption_key
        self.api_key = None
        self.usage: dict[str, int] = {}

        self.db_session = context.db_session

        if self.provider and self.encryption_key:
            self.api_key = (
                self.__decrypt_api_key(self.provider.api_key_encrypted) if self.provider.api_key_encrypted else None
            )

    def __decrypt_api_key(self, encrypted_key: str) -> str:
        """Decrypt stored API key."""
        try:
            fernet = Fernet(self.encryption_key.encode())
            return fernet.decrypt(encrypted_key.encode()).decode()
        except Exception as e:
            logger.error(f"Failed to decrypt API key for provider {self.provider.name}: {e}")
            raise LLMConfigurationError(f"Failed to decrypt API key: {e}")

    async def _call_plugin(self, plugin_name: str, operation: str, args_dict: dict[str, Any]) -> str:
        """Invoke a plugin operation and return the JSON-serialised result.

        When knowledge base IDs are bound to this adapter instance (via
        ``self.knowledge_base_ids``), they are merged into the ``__host.kb``
        section of *args_dict* so that KB-aware plugins receive the correct
        scope.  Any pre-existing keys in ``__host`` (e.g. ``auth``, ``exec``)
        are preserved.

        Args:
            plugin_name: Registered name of the plugin to call.
            operation: The specific operation within the plugin to invoke.
            args_dict: Arguments provided by the LLM for this tool call.

        Returns:
            JSON string of the plugin's return value.

        """
        if self.knowledge_base_ids:
            host = dict(args_dict.get("__host") or {})
            kb = dict(host.get("kb") or {})
            kb["knowledge_base_ids"] = self.knowledge_base_ids
            host["kb"] = kb
            args_dict = {**args_dict, "__host": host}

        logger.info("Calling plugin | %s - %s - %s", plugin_name, operation, args_dict)

        return json.dumps(
            await execute_plugin(self.db_session, plugin_name, operation, args_dict, self.conversation_owner_id)
        )

    def _get_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
    ) -> dict[str, int]:
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
        }

    def _aggregate_usage(self, first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
        return {k: first.get(k, 0) + second.get(k, 0) for k in set(first) | set(second)}

    def _flatten_chat_context(
        self,
        ctx: ChatContext,
        message_modifier: Callable = lambda x: {
            "role": getattr(x, "role", ""),
            "content": getattr(x, "content", ""),
        },
    ) -> list[dict[str, Any]]:
        """Convert ChatContext into a provider-agnostic list of role/content dicts."""
        messages: list[dict[str, Any]] = []
        if ctx.system_prompt:
            messages.append({"role": "system", "content": ctx.system_prompt})
        for m in ctx.messages:
            messages.append(message_modifier(m))
        return messages

    def _read_attachment_base64(self, attachment: Attachment) -> str | None:
        """Read attachment content from disk and return as base64 string.

        Validates that the path is within the configured attachment storage directory
        to prevent arbitrary file reads via tampered attachment records.
        """
        if not attachment.storage_path:
            return None
        try:
            path = Path(attachment.storage_path)

            # Resolve to absolute path - use strict=False since we check existence separately
            try:
                resolved_path = path.resolve()
            except (OSError, ValueError):
                logger.warning(f"Invalid attachment path: {attachment.storage_path}")
                return None

            if not resolved_path.exists():
                logger.warning(f"Attachment file not found: {attachment.storage_path}")
                return None

            # Validate path is within the configured attachment storage directory
            storage_dir = Path(self.settings.chat_attachment_storage_dir).resolve()
            try:
                resolved_path.relative_to(storage_dir)
            except ValueError:
                logger.warning(f"Path traversal blocked for attachment {attachment.id}: {resolved_path}")
                return None

            # Reject symlinks
            if path.is_symlink():
                logger.warning(f"Symlink access blocked for attachment {attachment.id}")
                return None

            content = resolved_path.read_bytes()
            return base64.b64encode(content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read attachment {attachment.id}: {e}")
            return None

    def _is_image_attachment(self, attachment: Attachment) -> bool:
        """Check if an attachment is an image based on mime type."""
        return attachment.mime_type.startswith("image/") if attachment.mime_type else False

    def _attachment_to_data_uri(self, attachment: Attachment) -> str | None:
        """Convert attachment to data URI format (data:mime;base64,...)."""
        b64 = self._read_attachment_base64(attachment)
        if not b64:
            return None
        return f"data:{attachment.mime_type};base64,{b64}"

    def _attachment_to_text_fallback(self, attachment: Attachment) -> dict[str, Any] | None:
        """Convert attachment to text fallback format using extracted_text (Completions API format).

        Returns None if no extracted_text is available.
        Used when native document support is disabled or file read fails.
        """
        if not attachment.extracted_text:
            return None
        return {
            "type": "text",
            "text": f"[Attached: {attachment.original_filename}]\n{attachment.extracted_text}",
        }

    def _attachment_to_input_text_fallback(self, attachment: Attachment) -> dict[str, Any] | None:
        """Convert attachment to input_text fallback format (Responses API format).

        Returns None if no extracted_text is available.
        """
        if not attachment.extracted_text:
            return None
        return {
            "type": "input_text",
            "text": f"[Attached: {attachment.original_filename}]\n{attachment.extracted_text}",
        }

    def supports_native_documents(self) -> bool:
        """Return True if provider supports native document uploads (PDFs, etc).

        Override in child adapters that support native document formats.
        When False, non-image attachments fall back to extracted_text.
        """
        return False

    def _update_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
    ) -> None:
        usage_dict = self._get_usage(
            input_tokens,
            output_tokens,
            cached_tokens,
            reasoning_tokens,
            total_tokens,
        )
        if not self.usage:
            self.usage = usage_dict
        else:
            self.usage = self._aggregate_usage(self.usage, usage_dict)

    def get_field_with_override(self, field_name):
        cfg = self.provider.config if isinstance(getattr(self.provider, "config", None), dict) else {}
        if field_name in cfg:
            value = cfg.get(field_name)
            if field_name == "get_capabilities":
                caps = ProviderCapabilities.from_request_dict(value if isinstance(value, dict) else {})
                return caps.to_dict(include_disabled=True, supported_mask=self.get_capabilities())
            return value

        func = getattr(self, field_name, None)
        value = None
        if func:
            value = func()

        if isinstance(value, ProviderCapabilities):
            value = value.to_dict()

        return value

    def get_endpoint_settings(self) -> dict[str, Any]:
        return {
            "chat": {
                "path": self.get_field_with_override("get_chat_endpoint"),
                "label": "The main chat endpoint to send messages to.",
                "options": {
                    # "get_response_streaming_path": {"value": self.get_field_with_override("get_response_streaming_path"), "label": "Path to extract the streaming deltas from."},
                    # "get_reasoning_streaming_path": {"value": self.get_field_with_override("get_reasoning_streaming_path"), "label": "Path to extract the reasoning deltas from."},
                    # "get_response_completion_path": {"value": self.get_field_with_override("get_response_completion_path"), "label": "Path to extract the completion (non-streaming) response from."},
                    # "get_function_call_completion_path": {"value": self.get_field_with_override("get_function_call_completion_path"), "label": "Path to extract the completion function call responses from."},
                    # "get_function_call_streaming_path": {"value": self.get_field_with_override("get_function_call_streaming_path"), "label": "Path to extract the reasoning function call responses from."},
                },
            },
            "models": {
                "path": self.get_field_with_override("get_models_endpoint"),
                "label": "The endpoint at which the provider exposes the available models.",
                "options": {
                    "get_model_information_path": {
                        "value": self.get_field_with_override("get_model_information_path"),
                        "label": "Path to extract the model names and IDs from.",
                    },
                },
            },
        }

    def normalize_request_dict(self, api_endpoint, payload):
        endpoints = payload.pop("endpoints", {}) or {}
        if not isinstance(endpoints, dict):
            endpoints = {}

        chat_endpoint = endpoints.get("chat", {}) or {}
        # chat_endpoint_options = chat_endpoint.get("options", {}) or {}
        models_endpoint = endpoints.get("models", {}) or {}
        models_endpoint_options = models_endpoint.get("options", {}) or {}
        return {
            "get_api_base_url": api_endpoint,
            "get_chat_endpoint": chat_endpoint.get("path"),
            # "get_response_streaming_path": chat_endpoint_options.get("get_response_streaming_path", {}).get("value"),
            # "get_reasoning_streaming_path": chat_endpoint_options.get("get_reasoning_streaming_path", {}).get("value"),
            # "get_response_completion_path": chat_endpoint_options.get("get_response_completion_path", {}).get("value"),
            # "get_function_call_completion_path": chat_endpoint_options.get("get_function_call_completion_path", {}).get("value"),
            # "get_function_call_streaming_path": chat_endpoint_options.get("get_function_call_streaming_path", {}).get("value"),
            "get_models_endpoint": models_endpoint.get("path"),
            "get_model_information_path": models_endpoint_options.get("get_model_information_path", {}).get("value"),
        }

    # GENERAL PROVIDER SETTINGS
    def get_provider_information(self) -> ProviderInformation:
        raise NotImplementedError("Function get_provider_information is not implemented.")

    def get_capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError("Function get_capabilities is not implemented.")

    def get_api_base_url(self) -> str:
        raise NotImplementedError("Function get_api_base_url is not implemented.")

    def get_chat_endpoint(self) -> str:
        raise NotImplementedError("Function get_chat_endpoint is not implemented.")

    def get_models_endpoint(self) -> str:
        raise NotImplementedError("Function get_models_endpoint is not implemented.")

    def get_authorization_header(self) -> dict[str, Any]:
        raise NotImplementedError("Function get_authorization_header is not implemented.")

    def get_parameter_mapping(self) -> dict[str, Any]:
        return {}

    # PROVIDER HANDLERS
    async def handle_provider_event(self, chunk: dict[str, Any]) -> ProviderEventResult | None:
        raise NotImplementedError("Function handle_provider_event is not implemented.")

    async def handle_provider_completion(self, data: dict[str, Any]) -> list[ProviderEventResult]:
        raise NotImplementedError("Function handle_provider_completion is not implemented.")

    async def finalize_provider_events(self) -> list[ProviderEventResult]:
        return []

    async def set_messages_in_payload(self, messages: ChatContext, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Function set_messages_in_payload is not implemented.")

    async def inject_tool_payload(self, tools: list[CallableTool], payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Function inject_tool_payload is not implemented.")

    async def inject_model_parameter(self, model_value: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload["model"] = model_value
        return payload

    async def inject_streaming_parameter(self, should_stream: bool, payload: dict[str, Any]) -> dict[str, Any]:
        payload["stream"] = should_stream
        return payload

    def inject_override_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        return params

    async def post_process_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


# Registry mapping adapter name -> class
_ADAPTERS: dict[str, type[BaseProviderAdapter]] = {}


def register_adapter(name: str, cls: type[BaseProviderAdapter]) -> None:
    _ADAPTERS[name] = cls


def get_adapter(name: str, context: ProviderAdapterContext) -> BaseProviderAdapter:
    if name not in _ADAPTERS:
        raise KeyError(f"Unknown provider adapter '{name}'")
    return _ADAPTERS[name](context)


def get_adapter_from_provider(
    db_session: AsyncSession,
    provider: LLMProvider,
    conversation_owner_id: str | None = None,
    knowledge_base_ids: list[str] | None = None,
) -> BaseProviderAdapter:
    """Instantiate the correct provider adapter for *provider*.

    Args:
        db_session: Active async database session.
        provider: LLMProvider ORM row identifying the target provider.
        conversation_owner_id: Optional user ID of the conversation owner,
            used for per-user plugin authorisation.
        knowledge_base_ids: Optional list of knowledge base IDs to scope
            plugin tool calls (e.g. KB search) to this conversation's KBs.

    Returns:
        Configured ``BaseProviderAdapter`` subclass instance.

    """
    return get_adapter(
        provider.provider_definition.provider_adapter_name,
        ProviderAdapterContext(
            db_session=db_session,
            provider=provider,
            conversation_owner_id=conversation_owner_id,
            knowledge_base_ids=knowledge_base_ids,
        ),
    )
