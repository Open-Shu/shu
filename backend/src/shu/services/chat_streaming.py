import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.exceptions import LLMProviderError, LLMTimeoutError, LLMRateLimitError, LLMError
from shu.core.config import get_settings_instance
from shu.services.providers.events import ProviderStreamEvent
from shu.services.providers.adapter_base import ProviderContentDeltaEventResult, ProviderErrorEventResult, ProviderEventResult, ProviderFinalEventResult, ProviderReasoningDeltaEventResult, ProviderToolCallEventResult, get_adapter_from_provider
from shu.core.config import ConfigurationManager
from shu.services.message_context_builder import MessageContextBuilder
from shu.llm.client import UnifiedLLMClient

from ..models.llm_provider import LLMProvider
from ..services.message_utils import serialize_message_for_sse

if TYPE_CHECKING:  # pragma: no cover
    from .chat_service import ChatService, ModelExecutionInputs
    
else:
    ChatService = Any
    ModelExecutionInputs = Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderResponseEvent:
    """Normalized chat completion stream event."""

    type: Literal["content_delta", "reasoning_delta", "final_message", "user_message", "error"]
    content: Any
    variant_index: Optional[int] = None
    model_configuration_id: Optional[str] = None
    model_configuration: Optional[Dict[str, Any]] = None
    model_name: Optional[str] = None
    model_display_name: Optional[str] = None
    client_temp_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def from_provider_event_result(cls, event: ProviderEventResult, variant_index: int, model_configuration_id: str, model_configuration: Dict[str, Any], model_display_name: str, model_name: str):
        return ProviderResponseEvent(
            type=event.type,
            variant_index=variant_index,
            model_configuration_id=model_configuration_id,
            model_configuration=model_configuration,
            model_name=model_name,
            model_display_name=model_display_name,
            content=event.content,
            metadata=getattr(event, "metadata", None)
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "event": self.type,
            "variant_index": self.variant_index,
            "model_configuration_id": self.model_configuration_id,
            "model_configuration": self.model_configuration,
            "model_name": self.model_name,
            "model_display_name": self.model_display_name,
        }
        if self.client_temp_id is not None:
            data["client_temp_id"] = self.client_temp_id
        if self.content is not None:
            data["content"] = self.content
        return data


class EnsembleStreamingHelper:
    """Helper that encapsulates ensemble streaming and execution workflows."""

    def __init__(self, chat_service: "ChatService", message_context_builder: MessageContextBuilder, db_session: AsyncSession, config_manager: ConfigurationManager) -> None:
        self.chat_service = chat_service
        self.message_context_builder = message_context_builder
        self.db_session = db_session
        self.config_manager = config_manager

    @staticmethod
    def _create_error_event(
        error_content: str,
        variant_index: int,
        inputs: "ModelExecutionInputs",
        model_snapshot: Optional[Dict[str, Any]],
        model_display_name: str,
    ) -> ProviderResponseEvent:
        """Create a standardized error event for streaming errors."""
        return ProviderResponseEvent(
            type="error",
            content=error_content,
            variant_index=variant_index,
            model_configuration_id=getattr(inputs.model_configuration, "id", None),
            model_configuration=model_snapshot,
            model_name=model_display_name,
            model_display_name=model_display_name,
        )

    def _get_tools_enabled(self,  client: UnifiedLLMClient, inputs: "ModelExecutionInputs"):
        model_functionalities = getattr(inputs.model_configuration, "functionalities") or {}
        provider: LLMProvider = getattr(client, "provider", None)
        capabilities = get_adapter_from_provider(self.db_session, provider).get_field_with_override("get_capabilities")
        provider_and_model_support_tools = (
            capabilities.get("tools", {}).get("value", False)
            and (model_functionalities.get("supports_functions", False) or model_functionalities.get("supports_tools", False))  # We used to call it `supports_functions`
        )
        chat_plugins_enabled = getattr(self.config_manager.settings, 'chat_plugins_enabled', False)
        return provider_and_model_support_tools and chat_plugins_enabled
    
    async def _get_conversation_owner(self, conversation_id):
        conversation_owner_id = None
        try:
            conv_obj = await self.chat_service.get_conversation_by_id(conversation_id)
            conversation_owner_id = getattr(conv_obj, "user_id", None) if conv_obj else None
        except Exception:
            conversation_owner_id = None
        return conversation_owner_id

    async def _call_provider(
        self,
        *,
        client: UnifiedLLMClient,
        messages: List[Dict[str, Any]],
        inputs: "ModelExecutionInputs",
        model_snapshot: Dict[str, Any],
        model_display_name: Optional[str],
        allowed_to_stream: bool,
        queue: asyncio.Queue,
        variant_index: int,
        tools_enabled: bool,
    ) -> tuple[Optional[ProviderResponseEvent], Optional[List[Dict[str, Any]]]]:

        final_message_event: Optional[ProviderFinalEventResult] = None
        followup_messages: Optional[List[Dict[str, Any]]] = None
        llm_params = None

        async for stream_event in await client.chat_completion(
            messages=messages,
            model=inputs.model.model_name,
            stream=allowed_to_stream,
            model_overrides=inputs.model_configuration.parameter_overrides or None,
            llm_params=llm_params,
            return_as_stream=True,  # We always stream to our frontends
            tools_enabled=tools_enabled,
        ):
            stream_event: ProviderEventResult = stream_event
            logger.debug("EVENT %s", stream_event)
            if isinstance(stream_event, ProviderToolCallEventResult) and tools_enabled:
                followup_messages = stream_event.additional_messages
                if stream_event.content:
                    await queue.put(
                        ProviderResponseEvent(
                            type="reasoning_delta",
                            content=f"{stream_event.content}\n",
                            variant_index=variant_index,
                            model_configuration_id=getattr(inputs.model_configuration, "id", None),
                            model_configuration=model_snapshot,
                            model_name=model_display_name,
                            model_display_name=model_display_name,
                        )
                    )
            elif isinstance(stream_event, (ProviderContentDeltaEventResult, ProviderReasoningDeltaEventResult)) and stream_event.content:
                await queue.put(
                    ProviderResponseEvent.from_provider_event_result(
                        stream_event,
                        variant_index,
                        getattr(inputs.model_configuration, "id", None),
                        model_snapshot or None,
                        model_display_name,
                        inputs.model.model_name,
                    )
                )
            elif isinstance(stream_event, (ProviderFinalEventResult, ProviderErrorEventResult)):
                final_message_event = ProviderResponseEvent.from_provider_event_result(
                    stream_event,
                    variant_index,
                    getattr(inputs.model_configuration, "id", None),
                    model_snapshot or None,
                    model_display_name,
                    inputs.model.model_name,
                )

        return final_message_event, followup_messages

    async def stream_ensemble_responses(
        self,
        ensemble_inputs: List["ModelExecutionInputs"],
        conversation_id: str,
        parent_message_id_override: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream multiple model configurations concurrently and emit multiplexed SSE events."""
        service = self.chat_service
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        total_variants = len(ensemble_inputs)

        start_time = datetime.now(timezone.utc)

        parent_message_id = parent_message_id_override or str(uuid.uuid4())
        use_parent_as_message_id = parent_message_id_override is None

        conversation_owner_id = await self._get_conversation_owner(conversation_id)

        async def stream_variant(variant_index: int, inputs: "ModelExecutionInputs"):

            client = await service.llm_service.get_client(inputs.provider_id, conversation_owner_id)
            config_metadata = service._build_model_configuration_metadata(inputs.model_configuration, inputs.model)
            model_snapshot = dict(config_metadata.get("model_configuration") or {})
            model_display_name = getattr(inputs.model_configuration, "name", None)
            config_metadata["model_configuration"] = model_snapshot

            try:

                # Create the tool callign parameters, if applicable
                tools_enabled = self._get_tools_enabled(client, inputs)
                if tools_enabled:
                    logger.info("Tool calling enabled for this call")

                allowed_to_stream = (
                    client.provider.supports_streaming and
                    (getattr(inputs.model_configuration, "functionalities") or {}).get("supports_streaming", False)
                )

                final_message_event: Optional[ProviderResponseEvent] = None
                call_messages = inputs.context_messages

                # We loop until the agens are done pulling what they need to pull.
                while True:
                    # If there is nothing new, we don't need to actually call the provider anymore, exit.
                    final_message_event, additional_messages = await self._call_provider(
                        client=client,
                        messages=call_messages,
                        inputs=inputs, 
                        model_snapshot=model_snapshot,
                        model_display_name=model_display_name,
                        allowed_to_stream=allowed_to_stream,
                        queue=queue,
                        variant_index=variant_index,
                        tools_enabled=tools_enabled,
                    )
                    # We only break if the adapters returned a final messages and no additional messages that need to be processed in another cycle.
                    if final_message_event and final_message_event.content and not additional_messages:
                        break
                    if additional_messages:
                        call_messages += additional_messages

                if final_message_event is None:
                    # This should not happen now that _stream_response raises exceptions,
                    # but keep as a fallback for edge cases
                    raise LLMProviderError(
                        "AI provider response incomplete. Please try again.",
                        details={"error_type": "NoFinalMessage"}
                    )

                if final_message_event.type == "error":
                    raise LLMProviderError(final_message_event.content)

                full_content, final_source_metadata = await self.message_context_builder._post_process_references(
                    final_message_event.content,
                    inputs.source_metadata or [],
                    inputs.knowledge_base_id,
                )

                if full_content != final_message_event.content and len(full_content) > len(final_message_event.content):
                    added = full_content[len(final_message_event.content):]
                    await queue.put(
                        ProviderResponseEvent(
                            type="content_delta",
                            content=added,
                            variant_index=variant_index,
                            model_configuration_id=getattr(inputs.model_configuration, "id", None),
                            model_configuration=model_snapshot,
                            model_name=model_display_name,
                            model_display_name=model_display_name,
                        )
                    )

                metadata = await self._adjust_event_metadata(final_message_event, final_source_metadata, config_metadata, service, start_time)

                # TODO: We currently only retain the final assistant message and loose track of the tool calls and reasoning messages. We should fix this at some point.
                assistant_message = await service.add_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    model_id=inputs.model.id,
                    metadata=metadata,
                    variant_index=variant_index,
                    parent_message_id=parent_message_id,
                    message_id=parent_message_id if use_parent_as_message_id and variant_index == 0 else None,
                )

                usage_from_event = metadata.get("usage") or {}
                await service.llm_service.record_usage(
                    provider_id=inputs.model.provider_id,
                    model_id=inputs.model.id,
                    request_type="chat",
                    input_tokens=usage_from_event.get("input_tokens", 0),
                    output_tokens=usage_from_event.get("output_tokens", 0),
                    total_cost=Decimal("0"),
                    response_time_ms=metadata.get("response_time_ms"),
                    success=True,
                )

                logger.info("Final event: %s", final_message_event)
                await queue.put(
                    ProviderResponseEvent(
                        type=final_message_event.type,
                        content=serialize_message_for_sse(assistant_message),
                        variant_index=variant_index,
                        model_configuration_id=getattr(inputs.model_configuration, "id", None),
                        model_configuration=model_snapshot or None,
                        model_name=final_message_event.model_name,
                        model_display_name=model_display_name,
                    )
                )

            except LLMError as exc:
                # LLM-specific errors have user-friendly messages
                logger.error(
                    "LLM streaming failed: %s (type=%s, details=%s)",
                    exc.message,
                    type(exc).__name__,
                    getattr(exc, 'details', None),
                    exc_info=True
                )
                await service._handle_exception(conversation_id, inputs.model, exc)
                # Use the exception's message directly - it's already user-friendly
                await queue.put(
                    self._create_error_event(exc.message, variant_index, inputs, model_snapshot, model_display_name)
                )
            except Exception as exc:
                # Unknown errors - log with full details for debugging
                logger.exception(
                    "Unexpected streaming error: %s (type=%s)",
                    exc,
                    type(exc).__name__,
                    stack_info=True
                )
                await service._handle_exception(conversation_id, inputs.model, exc)
                # For unknown errors, show details only in development
                settings = get_settings_instance()
                if settings.environment == "development":
                    error_content = f"An unexpected error occurred: {type(exc).__name__}: {exc}"
                else:
                    error_content = "An unexpected error occurred. Please contact the admin for assistance."
                await queue.put(
                    self._create_error_event(error_content, variant_index, inputs, model_snapshot, model_display_name)
                )

        tasks = [loop.create_task(stream_variant(idx, inputs)) for idx, inputs in enumerate(ensemble_inputs)]
        completed = 0

        try:
            while completed < total_variants:
                event = await queue.get()
                if event.type in {"reasoning_delta", "content_delta"}:
                    yield event
                elif event.type == "final_message":
                    completed += 1
                    yield event
                elif event.type == "error":
                    # Yield error event to client so they see the error message,
                    # then count as completed so we exit the loop
                    completed += 1
                    yield event
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _adjust_event_metadata(self, final_message_event: ProviderStreamEvent, final_source_metadata: List[Dict], config_metadata: Dict[str, Any], service: ChatService, start_time: datetime) -> Dict[str, Any]:
        
        metadata = final_message_event.metadata or {}
        metadata["response_time_ms"] = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        metadata["streamed"] = True

        # add citation metadata
        if final_source_metadata:
            metadata["sources"] = final_source_metadata
            metadata["has_citations"] = True
            citations = []
            for meta in final_source_metadata:
                citation = {
                    "title": meta["document_title"],
                    "url": meta.get("source_url", ""),
                    "source_id": meta.get("source_id", ""),
                    "document_id": meta.get("document_id", ""),
                    "similarity_score": meta.get("similarity_score", 0.0),
                }
                citations.append(citation)
            metadata["citations"] = citations
        else:
            metadata["has_citations"] = False

        metadata.update(config_metadata)

        # add RAG diagnostics metadata
        if hasattr(service, "_pending_rag_diagnostics") and service._pending_rag_diagnostics:
            metadata["rag"] = service._pending_rag_diagnostics
            service._pending_rag_diagnostics = None

        return metadata
