import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Self

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from shu.core.config import ConfigurationManager, get_settings_instance
from shu.core.database import get_async_session_local
from shu.core.exceptions import LLMError, LLMProviderError, LLMRateLimitError
from shu.core.rate_limiting import get_rate_limit_service
from shu.core.safe_decimal import safe_decimal
from shu.llm.client import UnifiedLLMClient
from shu.services.message_context_builder import MessageContextBuilder
from shu.services.providers.adapter_base import (
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderReasoningDeltaEventResult,
    ProviderToolCallEventResult,
)
from shu.services.providers.events import ProviderStreamEvent

from ..models.llm_provider import Conversation, Message
from ..services.chat_types import ChatContext, ChatMessage
from ..services.message_utils import serialize_message_for_sse
from ..services.usage_recording import get_usage_recorder

if TYPE_CHECKING:  # pragma: no cover
    from .chat_service import ChatService, ModelExecutionInputs, RegenLineageInfo, VariantStreamResult

else:
    ChatService = Any
    ModelExecutionInputs = Any
    VariantStreamResult = Any
    RegenLineageInfo = Any


# SHU-759: max retry attempts when a regen variant_index INSERT trips the
# UNIQUE (parent_message_id, variant_index) constraint added in r009_0001.
# Three attempts is sufficient to ride out realistic concurrency on the
# same regen target — siblings are re-read on each retry so contention
# resolves quickly.
REGEN_MAX_ATTEMPTS = 3

logger = logging.getLogger(__name__)


@dataclass
class ProviderResponseEvent:
    """Normalized chat completion stream event."""

    type: Literal["content_delta", "reasoning_delta", "final_message", "user_message", "error"]
    content: Any
    variant_index: int | None = None
    model_configuration_id: str | None = None
    model_configuration: dict[str, Any] | None = None
    model_name: str | None = None
    model_display_name: str | None = None
    client_temp_id: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_provider_event_result(
        cls,
        event: ProviderEventResult,
        variant_index: int,
        model_configuration_id: str,
        model_configuration: dict[str, Any],
        model_display_name: str,
        model_name: str,
    ) -> Self:
        return ProviderResponseEvent(
            type=event.type,
            variant_index=variant_index,
            model_configuration_id=model_configuration_id,
            model_configuration=model_configuration,
            model_name=model_name,
            model_display_name=model_display_name,
            content=event.content,
            metadata=getattr(event, "metadata", None),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
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

    def __init__(
        self,
        chat_service: "ChatService",
        message_context_builder: MessageContextBuilder,
        config_manager: ConfigurationManager,
    ) -> None:
        self.chat_service = chat_service
        self.message_context_builder = message_context_builder
        self.config_manager = config_manager

    @staticmethod
    def _create_error_event(
        error_content: str,
        variant_index: int,
        inputs: "ModelExecutionInputs",
        model_snapshot: dict[str, Any] | None,
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

    async def _check_provider_rate_limits(
        self,
        user_id: str,
        provider_id: str,
        rpm_limit: int,
        tpm_limit: int,
        estimated_tokens: int = 100,
    ) -> None:
        """Enforces per-provider requests-per-minute (RPM) and tokens-per-minute (TPM) limits for a user before making an LLM call.

        Checks the configured provider limits and, when rate limiting is enabled, queries the rate limit service to verify both RPM and TPM allowances. A limit value of 0 disables that specific check.

        Parameters
        ----------
            user_id (str): ID of the conversation owner / user making the request.
            provider_id (str): Provider identifier to check limits against.
            rpm_limit (int): Requests-per-minute limit for the provider; 0 means no RPM check.
            tpm_limit (int): Tokens-per-minute limit for the provider; 0 means no TPM check.
            estimated_tokens (int): Estimated number of tokens the pending request will consume (used for TPM calculation).

        Raises
        ------
            LLMRateLimitError: If the RPM or TPM check fails; error details include provider_id, limit_type ("rpm" or "tpm"), limit, and retry_after.

        """
        rate_limit_service = get_rate_limit_service()
        logger.debug(
            "Rate limit service enabled=%s, rpm_limit=%d, tpm_limit=%d",
            rate_limit_service.enabled,
            rpm_limit,
            tpm_limit,
        )
        if not rate_limit_service.enabled:
            logger.debug("Rate limiting is disabled, skipping checks")
            return

        # Check RPM with provider-specific limit (0 means no limit)
        if rpm_limit > 0:
            logger.debug(
                "Checking RPM limit: user=%s, provider=%s, limit=%d",
                user_id,
                provider_id,
                rpm_limit,
            )
            rpm_result = await rate_limit_service.check_llm_rpm_limit(
                user_id, provider_id=provider_id, rpm_override=rpm_limit
            )
            logger.debug(
                "RPM check result: allowed=%s, remaining=%d, limit=%d",
                rpm_result.allowed,
                rpm_result.remaining,
                rpm_result.limit,
            )
            if not rpm_result.allowed:
                logger.warning(
                    "RPM limit exceeded: user=%s, provider=%s, limit=%d",
                    user_id,
                    provider_id,
                    rpm_limit,
                )
                raise LLMRateLimitError(
                    f"Provider rate limit exceeded ({rpm_limit} RPM). Retry after {rpm_result.retry_after_seconds}s.",
                    details={
                        "provider_id": provider_id,
                        "limit_type": "rpm",
                        "limit": rpm_limit,
                        "retry_after": rpm_result.retry_after_seconds,
                    },
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
                    },
                )

    async def _call_provider(
        self,
        *,
        client: UnifiedLLMClient,
        messages: ChatContext,
        inputs: "ModelExecutionInputs",
        model_snapshot: dict[str, Any],
        model_display_name: str | None,
        allowed_to_stream: bool,
        queue: asyncio.Queue,
        variant_index: int,
        tools_enabled: bool,
    ) -> tuple[ProviderResponseEvent | None, list[ChatMessage] | None]:
        """Stream provider responses for a single model variant and enqueue interim events to the provided queue.

        Parameters
        ----------
            client (UnifiedLLMClient): LLM client to call for chat completions.
            messages (ChatContext): Conversation context to send to the provider.
            inputs (ModelExecutionInputs): Execution inputs including model and configuration.
            model_snapshot (Dict[str, Any]): Metadata snapshot of the model configuration to attach to events.
            model_display_name (Optional[str]): Human-readable model name for events.
            allowed_to_stream (bool): Whether the provider call should request streaming responses.
            queue (asyncio.Queue): Queue to which intermediate ProviderResponseEvent objects are put.
            variant_index (int): Index of the current model variant within the ensemble.
            tools_enabled (bool): Whether tool-calling is enabled for this run.

        Returns
        -------
            tuple[Optional[ProviderResponseEvent], Optional[List[ChatMessage]]]:
                final_message_event: The provider's final event (content or error) if produced, otherwise None.
                followup_messages: Additional messages emitted by the provider (e.g., from a tool call) to be sent back for follow-up, or None.

        """
        final_message_event: ProviderFinalEventResult | None = None
        followup_messages: list[ChatMessage] | None = None
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
            stream_event: ProviderEventResult = stream_event  # noqa: PLW0127, PLW2901 # typing for easier understanding
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
            elif (
                isinstance(stream_event, (ProviderContentDeltaEventResult, ProviderReasoningDeltaEventResult))
                and stream_event.content
            ):
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

    async def _stream_variant_phase(  # noqa: PLR0915
        self,
        *,
        variant_index: int,
        inputs: "ModelExecutionInputs",
        queue: asyncio.Queue,
        conversation_id: str,
        force_no_streaming: bool,
        start_time: datetime,
    ) -> "VariantStreamResult":
        """SHU-759: stream-only portion of per-variant processing.

        Sets up the provider client, runs rate-limit checks, calls the LLM,
        enqueues content/reasoning deltas to the shared queue, and
        post-processes references. Returns a `VariantStreamResult` capturing
        either the successful outcome (full content + metadata + usage) or a
        failure (error message + type). No DB writes happen here — those are
        the responsibility of `_finalize_variant_phase`.

        The provider is read from the prepare-time snapshot
        (inputs.provider) rather than re-fetched mid-stream, so this method
        holds zero pool checkouts on the simple path. Conditional DB work
        — _build_tool_context (when tools enabled) and KB rag-config lookup
        — opens its own short-lived session at the point of use.
        """
        # Late import to avoid circular dependency with chat_service.
        from .chat_service import VariantStreamResult as _VariantStreamResult

        # SHU-759 drift guard: the prepare phase must have detached the
        # request session and nulled chat_service.db_session before this
        # method runs. If this fires, someone re-introduced a mid-stream
        # access path that bypasses the prepared snapshot — fix that, don't
        # remove the assert.
        assert self.chat_service.db_session is None, (
            "_stream_variant_phase must run after prepare detached the request "
            "session; chat_service.db_session is still set"
        )

        stream_phase_start = datetime.now(UTC)
        logger.info(
            "Variant stream phase start",
            extra={
                "phase": "stream_start",
                "conversation_id": conversation_id,
                "variant_index": variant_index,
            },
        )

        service = self.chat_service
        conversation_owner_id = inputs.conversation_owner_id  # SHU-759 prepare snapshot
        kb_ids = inputs.knowledge_base_ids or getattr(inputs.model_configuration, "knowledge_base_ids", None) or None
        # SHU-759: use the snapshotted provider and construct the client directly,
        # avoiding the mid-stream get_provider_by_id query in LLMService.get_client.
        if inputs.provider is None:
            raise LLMProviderError(
                "ModelExecutionInputs.provider snapshot is missing; prepare phase did not populate it"
            )
        client = UnifiedLLMClient(
            db_session=service.db_session,
            provider=inputs.provider,
            conversation_owner_id=conversation_owner_id,
            knowledge_base_ids=kb_ids,
        )
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
                    conversation_owner_id,
                    inputs.provider_id,
                    inputs.rate_limit_rpm,
                    inputs.rate_limit_tpm,
                    estimated_tokens,
                )
                await self._check_provider_rate_limits(
                    user_id=str(conversation_owner_id),
                    provider_id=inputs.provider_id,
                    rpm_limit=inputs.rate_limit_rpm,
                    tpm_limit=inputs.rate_limit_tpm,
                    estimated_tokens=estimated_tokens,
                )

            # SHU-759: tools_enabled is a prepare-snapshot field on inputs,
            # not a mid-stream adapter capability lookup.
            tools_enabled = inputs.tools_enabled
            if tools_enabled:
                logger.info("Tool calling enabled for this call")

            allowed_to_stream = (
                not force_no_streaming
                and client.provider.supports_streaming
                and (inputs.model_configuration.functionalities or {}).get("supports_streaming", False)
            )

            final_message_event: ProviderResponseEvent | None = None
            call_messages = inputs.context_messages

            # We loop until the agents are done pulling what they need to pull.
            while True:
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
                # Break as soon as we have a final answer — regardless of whether tool calls also
                # occurred in the same cycle. Reasoning models (e.g. Grok 4) can emit function
                # call items alongside their final message; continuing the loop would feed those
                # results back and cause infinite cycling.
                if final_message_event and final_message_event.content:
                    break
                # Safety: if the model produced neither a final answer nor any tool calls, stop
                # to avoid spinning indefinitely.
                if not additional_messages:
                    break
                call_messages.messages += additional_messages

            if final_message_event is None:
                raise LLMProviderError(
                    "AI provider response incomplete. Please try again.",
                    details={"error_type": "NoFinalMessage"},
                )
            if final_message_event.type == "error":
                raise LLMProviderError(final_message_event.content)

            full_content, final_source_metadata = await self.message_context_builder._post_process_references(
                final_message_event.content,
                inputs.source_metadata or [],
                inputs.knowledge_base_ids,
                kb_include_references_map=inputs.kb_include_references_map,
            )

            if full_content != final_message_event.content and len(full_content) > len(final_message_event.content):
                added = full_content[len(final_message_event.content) :]
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

            metadata = await self._adjust_event_metadata(
                final_message_event, final_source_metadata, config_metadata, service, start_time
            )
            usage_from_event = metadata.get("usage") or {}

            logger.info(
                "Variant stream phase complete",
                extra={
                    "phase": "stream_complete",
                    "conversation_id": conversation_id,
                    "variant_index": variant_index,
                    "success": True,
                    "elapsed_ms": (datetime.now(UTC) - stream_phase_start).total_seconds() * 1000,
                },
            )
            return _VariantStreamResult(
                success=True,
                full_content=full_content,
                final_source_metadata=final_source_metadata,
                metadata=metadata,
                usage=usage_from_event,
                model_name_for_event=final_message_event.model_name,
                final_event_type=final_message_event.type,
            )

        except LLMError as exc:
            logger.error(
                "LLM streaming failed: %s (type=%s, details=%s)",
                exc.message,
                type(exc).__name__,
                getattr(exc, "details", None),
                exc_info=True,
            )
            logger.info(
                "Variant stream phase complete",
                extra={
                    "phase": "stream_complete",
                    "conversation_id": conversation_id,
                    "variant_index": variant_index,
                    "success": False,
                    "error_type": type(exc).__name__,
                    "elapsed_ms": (datetime.now(UTC) - stream_phase_start).total_seconds() * 1000,
                },
            )
            return _VariantStreamResult(
                success=False,
                error_message=exc.message,
                error_type=type(exc).__name__,
                error_details=getattr(exc, "details", None),
            )
        except Exception as exc:
            logger.exception(
                "Unexpected streaming error: %s (type=%s)",
                exc,
                type(exc).__name__,
                stack_info=True,
            )
            # For unknown errors, expose details only in development
            settings_obj = get_settings_instance()
            if settings_obj.environment == "development":
                error_content = f"An unexpected error occurred: {type(exc).__name__}: {exc}"
            else:
                error_content = "An unexpected error occurred. Please contact the admin for assistance."
            logger.info(
                "Variant stream phase complete",
                extra={
                    "phase": "stream_complete",
                    "conversation_id": conversation_id,
                    "variant_index": variant_index,
                    "success": False,
                    "error_type": type(exc).__name__,
                    "elapsed_ms": (datetime.now(UTC) - stream_phase_start).total_seconds() * 1000,
                },
            )
            return _VariantStreamResult(
                success=False,
                error_message=error_content,
                error_type=type(exc).__name__,
                error_details=None,
            )

    async def _finalize_variant_phase(  # noqa: PLR0915
        self,
        *,
        variant_index: int,
        inputs: "ModelExecutionInputs",
        result: "VariantStreamResult",
        queue: asyncio.Queue,
        conversation_id: str,
        parent_message_id: str,
        use_parent_as_message_id: bool,
        regen_lineage: "RegenLineageInfo | None" = None,
    ) -> None:
        """SHU-759: writes assistant Message + LLMUsage in one fresh-session transaction.

        Opens a short-lived session via ``async with get_async_session_local()()``.
        On success, persists the assistant message with the LLM response
        content and records a `success=True` LLMUsage row in the same
        transaction. On failure, persists an apology error message and a
        `success=False` LLMUsage row in the same transaction. In both cases,
        bumps the conversation's `updated_at` and enqueues the final SSE
        event with the persisted-and-reloaded message.

        When `regen_lineage` is provided, the same transaction also:
        - re-fetches the original target message by ID and backfills its
          `parent_message_id` / `variant_index` if they are NULL (the
          legacy backfill semantics from the removed `regen_stream` wrapper),
        - computes the new assistant's `variant_index` from the current
          sibling set, retrying up to REGEN_MAX_ATTEMPTS times if the
          UNIQUE constraint trips (SHU-759 N10 race fix),
        - stamps `regenerated=True` and `regenerated_from_message_id` onto
          the new assistant message's metadata.
        """
        # SHU-759 drift guard: see matching assert in _stream_variant_phase.
        # This runs on every call (not just type-checking) and trips at the
        # exact line that breaks the contract if a future change reintroduces
        # a mid-stream request-session dependency.
        assert self.chat_service.db_session is None, (
            "_finalize_variant_phase must run after prepare detached the request "
            "session; chat_service.db_session is still set"
        )

        finalize_phase_start = datetime.now(UTC)
        service = self.chat_service
        config_metadata = service._build_model_configuration_metadata(inputs.model_configuration, inputs.model)
        model_snapshot = dict(config_metadata.get("model_configuration") or {})
        model_display_name = getattr(inputs.model_configuration, "name", None)
        conversation_owner_id = inputs.conversation_owner_id  # prepare-snapshot

        session_factory = get_async_session_local()

        if result.success:
            assert result.full_content is not None, "VariantStreamResult.success requires full_content"
            now = datetime.now(UTC)

            # Retry loop only matters for the regen path; for the non-regen
            # path the variant_index is fixed by the ensemble loop counter
            # and there is no risk of a UNIQUE collision. We use a single
            # retry budget either way to keep the control flow simple.
            attempt = 0
            assistant_msg_loaded: Message | None = None
            while True:
                attempt += 1
                try:
                    async with session_factory() as session:
                        # Regen-only: legacy backfill of the original target's lineage
                        # plus computation of the new variant's variant_index from
                        # the current sibling set.
                        if regen_lineage:
                            target_row = (
                                await session.execute(
                                    select(Message).where(Message.id == regen_lineage.target_message_id)
                                )
                            ).scalar_one_or_none()
                            if target_row and target_row.parent_message_id is None:
                                target_row.parent_message_id = regen_lineage.root_id
                                if target_row.id == regen_lineage.root_id:
                                    target_row.variant_index = 0

                            sibling_rows = (
                                await session.execute(
                                    select(Message.variant_index).where(
                                        Message.parent_message_id == regen_lineage.root_id
                                    )
                                )
                            ).all()
                            existing_indices = [vi for (vi,) in sibling_rows if vi is not None]
                            assigned_variant_index = (max(existing_indices) + 1) if existing_indices else 1
                            assigned_parent_message_id = regen_lineage.root_id
                            assigned_message_id = str(uuid.uuid4())
                            metadata_dict = {
                                **dict(result.metadata or {}),
                                "regenerated": True,
                                "regenerated_from_message_id": regen_lineage.target_message_id,
                            }
                        else:
                            assigned_variant_index = variant_index
                            assigned_parent_message_id = parent_message_id
                            assigned_message_id = (
                                parent_message_id
                                if use_parent_as_message_id and variant_index == 0
                                else str(uuid.uuid4())
                            )
                            metadata_dict = dict(result.metadata or {})

                        assistant_msg = Message(
                            id=assigned_message_id,
                            conversation_id=conversation_id,
                            role="assistant",
                            content=result.full_content.strip(),
                            model_id=inputs.model.id,
                            message_metadata=metadata_dict,
                            parent_message_id=assigned_parent_message_id,
                            variant_index=assigned_variant_index,
                        )
                        session.add(assistant_msg)
                        await session.flush()

                        # Bump the conversation's updated_at without re-fetching the ORM row.
                        await session.execute(
                            update(Conversation).where(Conversation.id == conversation_id).values(updated_at=now)
                        )

                        # Record usage on the same session so Message + LLMUsage commit atomically.
                        await get_usage_recorder().record(
                            provider_id=inputs.model.provider_id,
                            model_id=inputs.model.id,
                            request_type="chat",
                            input_tokens=result.usage.get("input_tokens", 0),
                            output_tokens=result.usage.get("output_tokens", 0),
                            total_cost=safe_decimal(result.usage.get("cost")),
                            user_id=str(conversation_owner_id) if conversation_owner_id else None,
                            response_time_ms=result.metadata.get("response_time_ms"),
                            success=True,
                            session=session,
                        )

                        await session.commit()

                        # Re-fetch with relationships eager-loaded so serialize_message_for_sse
                        # doesn't trip MissingGreenlet on Message.model / .conversation / .attachments.
                        stmt = (
                            select(Message)
                            .where(Message.id == assigned_message_id)
                            .options(
                                selectinload(Message.model),
                                selectinload(Message.conversation),
                                selectinload(Message.attachments),
                            )
                        )
                        assistant_msg_loaded = (await session.execute(stmt)).scalar_one()
                    # exit retry loop on success
                    break
                except IntegrityError as ie:
                    # Concurrent regenerate on the same target races to claim the
                    # same variant_index. The UNIQUE constraint rejects the second
                    # writer; we re-read siblings and try again. Non-regen path
                    # should never hit this — the variant_index is fixed by the
                    # ensemble loop — so a non-regen IntegrityError is unexpected
                    # and is re-raised after exhausting retries.
                    if not regen_lineage or attempt >= REGEN_MAX_ATTEMPTS:
                        logger.error(
                            "Finalize integrity error after %d attempts (regen=%s): %s",
                            attempt,
                            regen_lineage is not None,
                            ie,
                        )
                        raise
                    logger.info("Regen variant_index conflict on attempt %d; retrying", attempt)

            assert assistant_msg_loaded is not None
            await queue.put(
                ProviderResponseEvent(
                    type=result.final_event_type or "final_message",
                    content=serialize_message_for_sse(assistant_msg_loaded),
                    variant_index=assistant_msg_loaded.variant_index if regen_lineage else variant_index,
                    model_configuration_id=getattr(inputs.model_configuration, "id", None),
                    model_configuration=model_snapshot or None,
                    model_name=result.model_name_for_event,
                    model_display_name=model_display_name,
                )
            )
        else:
            # Failure path — record an error Message + failed LLMUsage in one transaction.
            async with session_factory() as session:
                error_text = result.error_message or "An unexpected error occurred"
                error_msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation_id,
                    role="assistant",
                    content=f"I apologize, but I encountered an error: {error_text}",
                    model_id=inputs.model.id,
                    message_metadata={"error": result.error_details if result.error_details else error_text},
                )
                session.add(error_msg)
                await session.flush()

                try:
                    await get_usage_recorder().record(
                        provider_id=inputs.model.provider_id,
                        model_id=inputs.model.id,
                        request_type="chat",
                        input_tokens=0,
                        output_tokens=0,
                        total_cost=Decimal("0"),
                        user_id=str(conversation_owner_id) if conversation_owner_id else None,
                        success=False,
                        error_message=error_text,
                        session=session,
                    )
                except Exception as usage_error:
                    logger.warning("Failed to record failed-LLM usage: %s", usage_error)

                await session.commit()

            await queue.put(
                self._create_error_event(error_text, variant_index, inputs, model_snapshot, model_display_name)
            )

        logger.info(
            "Variant finalize phase complete",
            extra={
                "phase": "finalize_complete",
                "conversation_id": conversation_id,
                "variant_index": variant_index,
                "success": result.success,
                "elapsed_ms": (datetime.now(UTC) - finalize_phase_start).total_seconds() * 1000,
            },
        )

    # TODO: Refactor this function. It's too complex (number of branches and statements).
    async def stream_ensemble_responses(
        self,
        ensemble_inputs: list["ModelExecutionInputs"],
        conversation_id: str,
        parent_message_id_override: str | None = None,
        force_no_streaming: bool = False,
        regen_lineage: "RegenLineageInfo | None" = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream responses from multiple model configurations in parallel and emit multiplexed server-sent-event style events.

        This coroutine concurrently executes each provided ModelExecutionInputs variant, forwards intermediate deltas and final results into a shared queue, persists the final assistant message and usage, and yields each queued ProviderResponseEvent as a dictionary suitable for SSE delivery. The generator yields content and reasoning deltas as they arrive, final_message events when a variant completes, and error events when a variant fails.

        Parameters
        ----------
            ensemble_inputs (List[ModelExecutionInputs]): One entry per model/variant describing provider, model configuration, context, and rate limits to execute.
            conversation_id (str): Identifier of the conversation to which produced assistant messages will be appended.
            parent_message_id_override (Optional[str]): If provided, use this value as the parent message id for all produced messages; otherwise a new UUID is generated and the first variant may reuse it as the message id.
            force_no_streaming (bool): If True, force non-streaming mode regardless of provider/model configuration settings.

        Returns
        -------
            AsyncGenerator[Dict[str, Any], None]: An async generator that yields dictionaries representing ProviderResponseEvent objects (SSE-serializable events) with keys such as `event`/`type`, `content`, `variant_index`, and model/metadata fields.

        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        total_variants = len(ensemble_inputs)

        start_time = datetime.now(UTC)

        parent_message_id = parent_message_id_override or str(uuid.uuid4())
        use_parent_as_message_id = parent_message_id_override is None

        async def stream_variant(variant_index: int, inputs: "ModelExecutionInputs") -> None:
            """SHU-759: per-variant pipeline as the prepare → stream → finalize split.

            Stream phase runs without any DB writes and returns a
            VariantStreamResult capturing the outcome (success or failure).
            Finalize phase opens its own short-lived session, atomically
            writes the assistant Message + LLMUsage, and enqueues the
            final / error SSE event.
            """
            result = await self._stream_variant_phase(
                variant_index=variant_index,
                inputs=inputs,
                queue=queue,
                conversation_id=conversation_id,
                force_no_streaming=force_no_streaming,
                start_time=start_time,
            )
            await self._finalize_variant_phase(
                variant_index=variant_index,
                inputs=inputs,
                result=result,
                queue=queue,
                conversation_id=conversation_id,
                parent_message_id=parent_message_id,
                use_parent_as_message_id=use_parent_as_message_id,
                regen_lineage=regen_lineage,
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

    async def _adjust_event_metadata(
        self,
        final_message_event: ProviderStreamEvent,
        final_source_metadata: list[dict],
        config_metadata: dict[str, Any],
        service: ChatService,
        start_time: datetime,
    ) -> dict[str, Any]:
        metadata = final_message_event.metadata or {}
        metadata["response_time_ms"] = (datetime.now(UTC) - start_time).total_seconds() * 1000
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
