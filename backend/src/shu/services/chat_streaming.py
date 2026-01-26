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
from shu.core.rate_limiting import get_rate_limit_service
from shu.services.providers.events import ProviderStreamEvent
from shu.services.providers.adapter_base import ProviderContentDeltaEventResult, ProviderErrorEventResult, ProviderEventResult, ProviderFinalEventResult, ProviderReasoningDeltaEventResult, ProviderToolCallEventResult, get_adapter_from_provider
from shu.core.config import ConfigurationManager
from shu.services.message_context_builder import MessageContextBuilder
from shu.llm.client import UnifiedLLMClient

from ..models.llm_provider import LLMProvider
from ..services.message_utils import serialize_message_for_sse
from ..services.chat_types import ChatContext, ChatMessage

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
        """
        Fetches the owner user ID for a conversation by its ID.
        
        Parameters:
            conversation_id: Identifier of the conversation to look up.
        
        Returns:
            `str` user ID if the conversation exists and has a user_id, `None` if the conversation is missing, has no user_id, or an error occurs while fetching it.
        """
        conversation_owner_id = None
        try:
            conv_obj = await self.chat_service.get_conversation_by_id(conversation_id)
            conversation_owner_id = getattr(conv_obj, "user_id", None) if conv_obj else None
        except Exception:
            conversation_owner_id = None
        return conversation_owner_id

    async def _check_provider_rate_limits(
        self,
        user_id: str,
        provider_id: str,
        rpm_limit: int,
        tpm_limit: int,
        estimated_tokens: int = 100,
    ) -> None:
        """
        Enforces per-provider requests-per-minute (RPM) and tokens-per-minute (TPM) limits for a user before making an LLM call.
        
        Checks the configured provider limits and, when rate limiting is enabled, queries the rate limit service to verify both RPM and TPM allowances. A limit value of 0 disables that specific check.
        
        Parameters:
            user_id (str): ID of the conversation owner / user making the request.
            provider_id (str): Provider identifier to check limits against.
            rpm_limit (int): Requests-per-minute limit for the provider; 0 means no RPM check.
            tpm_limit (int): Tokens-per-minute limit for the provider; 0 means no TPM check.
            estimated_tokens (int): Estimated number of tokens the pending request will consume (used for TPM calculation).
        
        Raises:
            LLMRateLimitError: If the RPM or TPM check fails; error details include provider_id, limit_type ("rpm" or "tpm"), limit, and retry_after.
        """
        rate_limit_service = get_rate_limit_service()
        logger.debug(
            "Rate limit service enabled=%s, rpm_limit=%d, tpm_limit=%d",
            rate_limit_service.enabled, rpm_limit, tpm_limit
        )
        if not rate_limit_service.enabled:
            logger.debug("Rate limiting is disabled, skipping checks")
            return

        # Check RPM with provider-specific limit (0 means no limit)
        if rpm_limit > 0:
            logger.debug("Checking RPM limit: user=%s, provider=%s, limit=%d", user_id, provider_id, rpm_limit)
            rpm_result = await rate_limit_service.check_llm_rpm_limit(
                user_id, provider_id=provider_id, rpm_override=rpm_limit
            )
            logger.debug("RPM check result: allowed=%s, remaining=%d, limit=%d", rpm_result.allowed, rpm_result.remaining, rpm_result.limit)
            if not rpm_result.allowed:
                logger.warning("RPM limit exceeded: user=%s, provider=%s, limit=%d", user_id, provider_id, rpm_limit)
                raise LLMRateLimitError(
                    f"Provider rate limit exceeded ({rpm_limit} RPM). Retry after {rpm_result.retry_after_seconds}s.",
                    details={
                        "provider_id": provider_id,
                        "limit_type": "rpm",
                        "limit": rpm_limit,
                        "retry_after": rpm_result.retry_after_seconds,
                    }
                )
        else:
            logger.debug("RPM limit is 0, skipping RPM check")

        # Check TPM with provider-specific limit (0 means no limit)
        if tpm_limit > 0:
            tpm_result = await rate_limit_service.check_llm_tpm_limit(
                user_id, estimated_tokens, provider_id=provider_id, tpm_override=tpm_limit
            )
            if not tpm_result.allowed:
                raise LLMRateLimitError(
                    f"Provider token rate limit exceeded ({tpm_limit} TPM). Retry after {tpm_result.retry_after_seconds}s.",
                    details={
                        "provider_id": provider_id,
                        "limit_type": "tpm",
                        "limit": tpm_limit,
                        "retry_after": tpm_result.retry_after_seconds,
                    }
                )

    async def _call_provider(
        self,
        *,
        client: UnifiedLLMClient,
        messages: ChatContext,
        inputs: "ModelExecutionInputs",
        model_snapshot: Dict[str, Any],
        model_display_name: Optional[str],
        allowed_to_stream: bool,
        queue: asyncio.Queue,
        variant_index: int,
        tools_enabled: bool,
    ) -> tuple[Optional[ProviderResponseEvent], Optional[List[ChatMessage]]]:

        """
        Stream provider responses for a single model variant and enqueue interim events to the provided queue.
        
        Parameters:
            client (UnifiedLLMClient): LLM client to call for chat completions.
            messages (ChatContext): Conversation context to send to the provider.
            inputs (ModelExecutionInputs): Execution inputs including model and configuration.
            model_snapshot (Dict[str, Any]): Metadata snapshot of the model configuration to attach to events.
            model_display_name (Optional[str]): Human-readable model name for events.
            allowed_to_stream (bool): Whether the provider call should request streaming responses.
            queue (asyncio.Queue): Queue to which intermediate ProviderResponseEvent objects are put.
            variant_index (int): Index of the current model variant within the ensemble.
            tools_enabled (bool): Whether tool-calling is enabled for this run.
        
        Returns:
            tuple[Optional[ProviderResponseEvent], Optional[List[ChatMessage]]]:
                final_message_event: The provider's final event (content or error) if produced, otherwise None.
                followup_messages: Additional messages emitted by the provider (e.g., from a tool call) to be sent back for follow-up, or None.
        """
        final_message_event: Optional[ProviderFinalEventResult] = None
        followup_messages: Optional[List[ChatMessage]] = None
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
        force_no_streaming: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream responses from multiple model configurations in parallel and emit multiplexed server-sent-event style events.
        
        This coroutine concurrently executes each provided ModelExecutionInputs variant, forwards intermediate deltas and final results into a shared queue, persists the final assistant message and usage, and yields each queued ProviderResponseEvent as a dictionary suitable for SSE delivery. The generator yields content and reasoning deltas as they arrive, final_message events when a variant completes, and error events when a variant fails.
        
        Parameters:
            ensemble_inputs (List[ModelExecutionInputs]): One entry per model/variant describing provider, model configuration, context, and rate limits to execute.
            conversation_id (str): Identifier of the conversation to which produced assistant messages will be appended.
            parent_message_id_override (Optional[str]): If provided, use this value as the parent message id for all produced messages; otherwise a new UUID is generated and the first variant may reuse it as the message id.
            force_no_streaming (bool): If True, force non-streaming mode regardless of provider/model configuration settings.
        
        Returns:
            AsyncGenerator[Dict[str, Any], None]: An async generator that yields dictionaries representing ProviderResponseEvent objects (SSE-serializable events) with keys such as `event`/`type`, `content`, `variant_index`, and model/metadata fields.
        """
        service = self.chat_service
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        total_variants = len(ensemble_inputs)

        start_time = datetime.now(timezone.utc)

        parent_message_id = parent_message_id_override or str(uuid.uuid4())
        use_parent_as_message_id = parent_message_id_override is None

        conversation_owner_id = await self._get_conversation_owner(conversation_id)

        async def stream_variant(variant_index: int, inputs: "ModelExecutionInputs"):

            """
            Handle streaming for a single ensemble variant: call the LLM provider (with per-provider rate-limit checks), stream intermediate events into the shared queue, persist the final assistant message, record usage, and enqueue the final or error event.
            
            Parameters:
            	variant_index (int): Zero-based index of the variant within the ensemble.
            	inputs (ModelExecutionInputs): Execution inputs and configuration for this variant, including provider/model identifiers, context messages, and rate-limit settings.
            """
            client = await service.llm_service.get_client(inputs.provider_id, conversation_owner_id)
            config_metadata = service._build_model_configuration_metadata(inputs.model_configuration, inputs.model)
            model_snapshot = dict(config_metadata.get("model_configuration") or {})
            model_display_name = getattr(inputs.model_configuration, "name", None)
            config_metadata["model_configuration"] = model_snapshot

            try:
                # Check per-provider rate limits before calling LLM
                if conversation_owner_id:
                    # Token estimation heuristic: multiply word count by 2 to approximate tokens.
                    # English averages ~1.3 tokens/word, but code/JSON/non-Latin scripts can be
                    # higher; 2x provides a conservative pre-check buffer. This estimate is used
                    # only for rate limiting, not billing. A floor of 100 tokens is applied to
                    # handle empty or very short messages.
                    estimated_tokens = sum(
                        len(str(getattr(m, "content", "") or "").split()) * 2
                        for m in (inputs.context_messages.messages or [])
                    )
                    estimated_tokens = max(100, estimated_tokens)
                    logger.info(
                        "Checking rate limits: user=%s, provider=%s, rpm_limit=%d, tpm_limit=%d, est_tokens=%d",
                        conversation_owner_id, inputs.provider_id, inputs.rate_limit_rpm, inputs.rate_limit_tpm, estimated_tokens
                    )
                    await self._check_provider_rate_limits(
                        user_id=str(conversation_owner_id),
                        provider_id=inputs.provider_id,
                        rpm_limit=inputs.rate_limit_rpm,
                        tpm_limit=inputs.rate_limit_tpm,
                        estimated_tokens=estimated_tokens,
                    )

                # Create the tool callign parameters, if applicable
                tools_enabled = self._get_tools_enabled(client, inputs)
                if tools_enabled:
                    logger.info("Tool calling enabled for this call")

                allowed_to_stream = (
                    not force_no_streaming and
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
                        call_messages.messages += additional_messages

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
                # LLM-specific errors - log full details and pass technical message through
                logger.error(
                    "LLM streaming failed: %s (type=%s, details=%s)",
                    exc.message,
                    type(exc).__name__,
                    getattr(exc, 'details', None),
                    exc_info=True
                )
                await service._handle_exception(conversation_id, inputs.model, exc)
                # Pass through the technical error message - it will be sanitized by the endpoint
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