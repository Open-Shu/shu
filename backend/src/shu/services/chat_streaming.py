import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Self

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from shu.core.config import ConfigurationManager, get_settings_instance
from shu.core.database import get_async_session_local
from shu.core.exceptions import LLMError, LLMProviderError, LLMRateLimitError
from shu.core.logging import get_logger
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

logger = get_logger(__name__)


# SHU-784: lifecycle reasons. First-writer-wins ordering matters here —
# `user_terminated` and `shutdown` are intentional server-side stops that
# must win over a `client_disconnected` that races them. The lifecycle's
# `signal()` enforces that without depending on event-firing order.
StreamLifecycleReason = Literal["complete", "client_disconnected", "user_terminated", "shutdown"]


@dataclass
class StreamLifecycle:
    """SHU-784: process-local signal channel for an in-flight SSE chat stream.

    Created once per ``send_message`` / ``regenerate_message`` call and
    shared across all ensemble variants in that call. Carries three
    pieces of cross-task state:

    - **identification** (``stream_id``) — the key under which this
      lifecycle is registered in ``app.state.in_flight_streams``, and
      the path parameter for the terminate endpoint.
    - **authz context** (``user_id``) — the terminate endpoint enforces
      ``lifecycle.user_id == current_user.id`` before honoring a stop
      request, so the lifecycle has to carry it.
    - **cross-task signal** (``event`` + ``reason``) — fired by the SSE
      wrapper on client disconnect, by the terminate endpoint on user
      stop, and by the lifespan shutdown handler on SIGTERM. Observed
      by ``_stream_variant_phase``'s provider consumer loop (to
      short-circuit cleanly) and read by ``_finalize_variant_phase`` at
      commit time (to stamp ``Message.message_metadata["stream_state"]``).

    ``signal()`` is first-writer-wins: a later signal does NOT overwrite
    the first reason. This preserves the intent ordering where an
    intentional stop (``user_terminated`` / ``shutdown``) wins over an
    incidental ``client_disconnected`` that races it, regardless of the
    actual firing order on the event loop.

    Lifetime: registered in ``app.state.in_flight_streams`` when the
    stream starts; removed by the ``stream_variant`` task's ``finally``
    when its variant completes. Process-local — never serialized, never
    shared across pods. The terminate endpoint is single-pod-scoped by
    construction (an SSE stream is anchored to the pod that opened it).
    """

    stream_id: str
    user_id: str
    conversation_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: StreamLifecycleReason | None = None
    # SHU-784: cleanup callback. Set by the endpoint when it registers the
    # lifecycle in `app.state.in_flight_streams`; invoked by the per-stream
    # supervisor when all variant tasks have completed (registry pop). The
    # supervisor uses `fire_on_complete()` which guards against missing /
    # raising callbacks so a bookkeeping failure can't break the supervisor.
    # `repr=False` keeps lifecycle log lines from dumping closures.
    on_complete: Callable[[], None] | None = field(default=None, repr=False)
    # SHU-784: the per-stream supervisor task. Set by
    # `stream_ensemble_responses` once the supervisor is spawned. The
    # lifespan shutdown drain (step 10) iterates the registry and awaits
    # each entry's supervise_task so all in-flight finalizes can land
    # before the process exits. `repr=False` to keep log lines tidy.
    supervise_task: "asyncio.Task[None] | None" = field(default=None, repr=False)

    def signal(self, reason: StreamLifecycleReason) -> bool:
        """Set ``reason`` and fire ``event`` atomically. First-writer-wins.

        Returns True if this caller's reason was accepted (no prior
        signal had fired); False if the lifecycle was already signalled
        and this call is a no-op. The event is fired regardless — a
        second caller observing ``False`` still wants the wakeup.

        Safe to call from any coroutine on the same event loop;
        not safe to call from a threadpool worker. See the
        ``app.state.in_flight_streams`` docstring.
        """
        accepted = self.reason is None
        if accepted:
            self.reason = reason
        # Always set the event so consumers blocked on `await event.wait()`
        # wake up even when a prior signal already won the reason race.
        self.event.set()
        return accepted

    def resolved_reason(self) -> StreamLifecycleReason:
        """Return the lifecycle reason or ``"complete"`` if none fired.

        Called by ``_finalize_variant_phase`` at commit time to stamp
        ``Message.message_metadata["stream_state"]``. ``"complete"`` is
        the default for the happy path: stream finished naturally, no
        disconnect / terminate / shutdown signal ever fired.
        """
        return self.reason or "complete"

    def fire_on_complete(self) -> None:
        """Invoke the cleanup callback (registry pop) exactly once.

        Defensive — the supervisor calls this in a ``finally`` after
        gathering all variant tasks. A raising callback or a missing
        callback both no-op cleanly; the registry's cleanup contract
        survives downstream test stubs that don't set ``on_complete``.
        """
        cb = self.on_complete
        if cb is None:
            return
        self.on_complete = None  # idempotent — only run once
        try:
            cb()
        except Exception as e:
            logger.warning("StreamLifecycle on_complete failed: %s", e, exc_info=True)


async def drain_in_flight_streams(
    registry: dict[str, StreamLifecycle],
    timeout_seconds: float,
) -> int:
    """SHU-784: signal ``shutdown`` on every lifecycle and await supervisors.

    Used by the lifespan shutdown hook to ensure in-flight chat streams
    land their finalize transactions before the process exits. Returns
    the number of supervisor tasks that did NOT complete within the
    drain budget — those will be killed by the surrounding asyncio
    teardown and their shielded finalize transactions roll back. Per
    Phase 2.3 scenario S2 / decision option (c): we accept this
    trade-off and surface it via the return value so the caller can log
    a loud ERROR for ops to grep.

    Args:
        registry: ``app.state.in_flight_streams`` (live dict). The
            function snapshots ``.values()`` before iteration so a
            supervisor completing during the signal phase (which would
            ``fire_on_complete`` and mutate the registry) doesn't crash
            the iteration.
        timeout_seconds: Max wall-clock seconds to wait for supervisors.
            Validated as ``>=1`` by the Pydantic settings model — calling
            with sub-1s values here is allowed (tests use 0.1s) but
            production should respect the validator.

    Returns:
        Count of supervisor tasks still incomplete when the drain
        timeout fired. Zero means clean shutdown; non-zero means N
        in-flight messages will not land.

    """
    lifecycles = list(registry.values())
    if not lifecycles:
        return 0

    for lc in lifecycles:
        lc.signal("shutdown")

    supervise_tasks = [lc.supervise_task for lc in lifecycles if lc.supervise_task is not None]
    if not supervise_tasks:
        return 0

    try:
        await asyncio.wait_for(
            asyncio.gather(*supervise_tasks, return_exceptions=True),
            timeout=timeout_seconds,
        )
        return 0
    except TimeoutError:
        # `t.done()` returns True for cancelled tasks too, so we'd
        # undercount the kills if we relied on `not t.done()` alone.
        # `wait_for`'s timeout cancels the inner gather, which cancels
        # the not-yet-finished supervisor tasks; those should count as
        # killed because their work didn't land. A supervisor that
        # raised (e.g. `fire_on_complete` crashed) is `done() and not
        # cancelled()` and is NOT counted — the variants underneath
        # presumably already finished; the bookkeeping failure is a
        # separate concern.
        return sum(1 for t in supervise_tasks if t.cancelled() or not t.done())


async def periodic_in_flight_streams_size_log(
    registry: dict[str, StreamLifecycle],
    interval_seconds: float,
) -> None:
    """SHU-784: log ``len(in_flight_streams)`` every ``interval_seconds``.

    A leak in the per-variant ``try/finally`` registry cleanup would
    grow the dict monotonically. Logging the size periodically makes
    the leak visible without waiting for memory pressure or pool
    exhaustion. Logged at ``INFO`` regardless of size so the absence
    of a log line is itself a signal that the size-log task crashed.
    """
    if interval_seconds <= 0:
        logger.info("in_flight_streams size log disabled (interval=%s)", interval_seconds)
        return
    logger.info("in_flight_streams size log started (interval=%ss)", interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("in_flight_streams size log cancelled")
            raise
        try:
            logger.info(
                "in_flight_streams size snapshot",
                extra={"in_flight_streams_size": len(registry)},
            )
        except Exception as e:
            logger.warning(f"in_flight_streams size log error: {e}")


@dataclass
class ProviderResponseEvent:
    """Normalized chat completion stream event."""

    type: Literal[
        "stream_start",
        "content_delta",
        "reasoning_delta",
        "final_message",
        "user_message",
        "error",
    ]
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
            # SHU-759 (code review): the shared SSE sanitizer in
            # core/streaming.py keys off `type` to find error events to
            # sanitize. Pre-fix this dict emitted only `event`, so chat
            # error events bypassed sanitization and raw exception text
            # (IntegrityError SQL detail, savepoint failures, raw
            # provider errors) leaked to clients. Experience SSE was
            # unaffected because experience_executor.to_dict emits
            # `type` already. We emit both keys so the frontend keeps
            # reading `event` while the sanitizer sees `type`.
            "type": self.type,
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
        lifecycle: "StreamLifecycle",
        content_accumulator: list[str],
    ) -> tuple[ProviderResponseEvent | None, list[ChatMessage] | None, bool]:
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
            lifecycle (StreamLifecycle): SHU-784. Observed between provider events for an early-exit
                signal (``user_terminated`` / ``shutdown``). ``client_disconnected`` does NOT
                short-circuit — disconnected streams are intentionally allowed to run to natural
                completion so the message lands.
            content_accumulator (list[str]): SHU-784. Mutable list the caller passes in;
                content-delta strings are appended in order. The list is the source of truth
                for partial content on early termination — `"".join(content_accumulator)` gives
                whatever the provider had emitted before the break.

        Returns
        -------
            tuple[Optional[ProviderResponseEvent], Optional[List[ChatMessage]], bool]:
                final_message_event: The provider's final event (content or error) if produced, otherwise None.
                followup_messages: Additional messages emitted by the provider (e.g., from a tool call) to be sent back for follow-up, or None.
                terminated: SHU-784. True if the loop exited early because the lifecycle was
                    signalled with ``user_terminated`` / ``shutdown``; the caller transitions
                    to finalize with the partial content from ``content_accumulator``.

        """
        final_message_event: ProviderFinalEventResult | None = None
        followup_messages: list[ChatMessage] | None = None
        llm_params = None
        terminated = False

        async for stream_event in await client.chat_completion(
            messages=messages,
            model=inputs.model.model_name,
            stream=allowed_to_stream,
            model_overrides=inputs.model_configuration.parameter_overrides or None,
            llm_params=llm_params,
            return_as_stream=True,  # We always stream to our frontends
            tools_enabled=tools_enabled,
        ):
            # SHU-784: between-events terminate check. Only intentional stops
            # short-circuit — `client_disconnected` lets the LLM run to its
            # natural end so the full response lands (the headline bug-fix
            # behavior). The check fires BEFORE event processing so we don't
            # enqueue a stale delta after the user has hit Stop. Sub-100ms
            # latency from terminate-POST → break is gated by the provider's
            # inter-chunk interval (the provider has to deliver the next chunk
            # before we can observe the event); this matches the design
            # decision around per-provider partial-usage accuracy in H4.
            if lifecycle.event.is_set() and lifecycle.reason in ("user_terminated", "shutdown"):
                terminated = True
                break

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
                # SHU-784: accumulate content-delta strings so finalize can
                # persist partial content if the stream is terminated. We
                # only track ContentDelta (not ReasoningDelta) since the
                # assistant Message.content field holds final answer text,
                # not reasoning traces.
                if isinstance(stream_event, ProviderContentDeltaEventResult):
                    content_accumulator.append(stream_event.content)
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

        return final_message_event, followup_messages, terminated

    async def _stream_variant_phase(  # noqa: PLR0912, PLR0915
        self,
        *,
        variant_index: int,
        inputs: "ModelExecutionInputs",
        queue: asyncio.Queue,
        conversation_id: str,
        force_no_streaming: bool,
        start_time: datetime,
        lifecycle: "StreamLifecycle",
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
        # method runs. Raised (not asserted) so `python -O` can't strip the
        # invariant. If this fires, someone re-introduced a mid-stream
        # access path that bypasses the prepared snapshot — fix that, don't
        # remove the check.
        if self.chat_service.db_session is not None:
            raise RuntimeError(
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
            # SHU-784: passed by reference into `_call_provider`; the consumer
            # loop appends content-delta strings here so we can persist
            # partial content on early termination. Survives across
            # tool-loop iterations so multi-round tool calls accumulate
            # the full assistant-visible content trail.
            content_accumulator: list[str] = []
            terminated = False

            # We loop until the agents are done pulling what they need to pull.
            while True:
                final_message_event, additional_messages, terminated = await self._call_provider(
                    client=client,
                    messages=call_messages,
                    inputs=inputs,
                    model_snapshot=model_snapshot,
                    model_display_name=model_display_name,
                    allowed_to_stream=allowed_to_stream,
                    queue=queue,
                    variant_index=variant_index,
                    tools_enabled=tools_enabled,
                    lifecycle=lifecycle,
                    content_accumulator=content_accumulator,
                )
                # SHU-784: user_terminated / shutdown short-circuits the tool
                # loop too — once the lifecycle is signalled we stop calling
                # the provider and transition straight to the terminated-
                # finalize path. The provider HTTP stream was already broken
                # in `_call_provider` via the inner-loop break.
                if terminated:
                    break
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

            # SHU-784: terminated short-circuit — package partial content and
            # return a VariantStreamResult with `terminated=True`. Finalize
            # (step 8) stamps the stream_state, writes the partial Message
            # + LLMUsage(success=False), and emits the terminal SSE event.
            if terminated:
                partial_content = "".join(content_accumulator).strip()
                metadata = dict(config_metadata)
                metadata["response_time_ms"] = (datetime.now(UTC) - start_time).total_seconds() * 1000
                metadata["streamed"] = True
                metadata["has_citations"] = False
                logger.info(
                    "Variant stream phase terminated",
                    extra={
                        "phase": "stream_complete",
                        "conversation_id": conversation_id,
                        "variant_index": variant_index,
                        "success": True,
                        "terminated": True,
                        "lifecycle_reason": lifecycle.reason,
                        "partial_content_chars": len(partial_content),
                        "elapsed_ms": (datetime.now(UTC) - stream_phase_start).total_seconds() * 1000,
                    },
                )
                return _VariantStreamResult(
                    success=True,
                    terminated=True,
                    partial_usage_unavailable=True,
                    full_content=partial_content,
                    final_source_metadata=[],
                    metadata=metadata,
                    usage={},
                    model_name_for_event=model_display_name,
                    final_event_type="final_message",
                )

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

    async def _finalize_variant_phase(  # noqa: PLR0912, PLR0915
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
        lifecycle: "StreamLifecycle | None" = None,
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
        # SHU-759 drift guard: see matching check in _stream_variant_phase.
        # Raised (not asserted) so `python -O` can't strip the invariant.
        if self.chat_service.db_session is not None:
            raise RuntimeError(
                "_finalize_variant_phase must run after prepare detached the request "
                "session; chat_service.db_session is still set"
            )

        finalize_phase_start = datetime.now(UTC)
        service = self.chat_service
        config_metadata = service._build_model_configuration_metadata(inputs.model_configuration, inputs.model)
        model_snapshot = dict(config_metadata.get("model_configuration") or {})
        model_display_name = getattr(inputs.model_configuration, "name", None)
        conversation_owner_id = inputs.conversation_owner_id  # prepare-snapshot

        # SHU-784: synthesize a stub lifecycle when the caller (e.g. a direct
        # unit test) doesn't provide one. The stub stamps `stream_state="complete"`
        # via `resolved_reason()` since no signal can fire on a lifecycle nothing
        # references — equivalent to the pre-SHU-784 behavior. Mirrors the same
        # pattern in `stream_ensemble_responses`.
        if lifecycle is None:
            lifecycle = StreamLifecycle(
                stream_id=str(uuid.uuid4()),
                user_id="",
                conversation_id=conversation_id,
            )

        session_factory = get_async_session_local()

        if result.success:
            assert result.full_content is not None, "VariantStreamResult.success requires full_content"

            # Retry loop only matters for the regen path; for the non-regen
            # path the variant_index is fixed by the ensemble loop counter
            # and there is no risk of a UNIQUE collision. We use a single
            # retry budget either way to keep the control flow simple.
            attempt = 0
            assistant_msg_loaded: Message | None = None
            while True:
                attempt += 1
                # Refreshed per-attempt so a retry stamps conversation.updated_at
                # with the actual commit time, not the time of the first attempt.
                now = datetime.now(UTC)
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

                        # SHU-784: stamp stream_state on every persisted Message.
                        # `resolved_reason()` returns `"complete"` if no lifecycle
                        # signal fired, otherwise one of `client_disconnected` /
                        # `user_terminated` / `shutdown`. The frontend can read
                        # this flag at message-render time to decide whether to
                        # show an "interrupted" indicator (deferred to a
                        # follow-up ticket per Phase 2.1 (b)).
                        metadata_dict["stream_state"] = lifecycle.resolved_reason()
                        if result.terminated and result.partial_usage_unavailable:
                            # SHU-784 (H4): honest flag — token counts are zero
                            # because the provider never emitted a usage event
                            # before the break, not because no tokens were used.
                            metadata_dict["partial_usage_unavailable"] = True

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
                        # SHU-784: terminated streams record success=False — the
                        # LLM did not produce a complete response — even though
                        # the result has `success=True` (meaning "we have
                        # content worth persisting"). The two dimensions are
                        # independent: result.success governs which finalize
                        # branch runs (apology Message vs. real content);
                        # llm_usage.success governs whether the row counts
                        # toward "successful chat completions" metrics.
                        if result.terminated:
                            usage_success = False
                            usage_error_message: str | None = f"Stream interrupted: {lifecycle.resolved_reason()}"
                        else:
                            usage_success = True
                            usage_error_message = None
                        await get_usage_recorder().record(
                            provider_id=inputs.model.provider_id,
                            model_id=inputs.model.id,
                            request_type="chat",
                            input_tokens=result.usage.get("input_tokens", 0),
                            output_tokens=result.usage.get("output_tokens", 0),
                            total_cost=safe_decimal(result.usage.get("cost")),
                            user_id=str(conversation_owner_id) if conversation_owner_id else None,
                            response_time_ms=result.metadata.get("response_time_ms"),
                            success=usage_success,
                            error_message=usage_error_message,
                            session=session,
                        )

                        await session.commit()

                        # SHU-759 (code review): the post-commit reload is wrapped
                        # in its own try/except so a reload failure does NOT route
                        # through the outer `finalize_rollback` handler. After
                        # `session.commit()` the Message + LLMUsage rows are
                        # already persisted; treating a reload error as a
                        # rolled-back finalize would emit a misleading error
                        # event and prompt the client to retry, producing
                        # duplicate Message rows + double LLM billing.
                        #
                        # Fallback uses the in-memory `assistant_msg` we built
                        # before the commit. `serialize_message_for_sse` is
                        # defensive about lazy-loaded relationships (attachments
                        # has its own try/except internally; the rest of the
                        # serialized fields are scalar `getattr` accesses), so
                        # the SSE event lands with at most degraded attachment
                        # metadata — never a false rollback.
                        try:
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
                        except Exception as reload_exc:
                            logger.warning(
                                "Post-commit reload failed; emitting final_message "
                                "with the flushed in-memory Message. Data was "
                                "persisted successfully — only relationship "
                                "metadata may be degraded in the SSE event.",
                                extra={
                                    "phase": "finalize_post_commit_reload_failed",
                                    "conversation_id": conversation_id,
                                    "variant_index": variant_index,
                                    "error_type": type(reload_exc).__name__,
                                },
                                exc_info=True,
                            )
                            assistant_msg_loaded = assistant_msg
                    # exit retry loop on success
                    break
                except IntegrityError as ie:
                    # Concurrent regenerate on the same target races to claim the
                    # same variant_index. The UNIQUE constraint rejects the second
                    # writer; we re-read siblings and try again. Non-regen path
                    # should never hit this — the variant_index is fixed by the
                    # ensemble loop — so a non-regen IntegrityError is unexpected.
                    # Either way, after exhausting retries we surface a terminal
                    # error event so `stream_ensemble_responses` can complete.
                    if not regen_lineage or attempt >= REGEN_MAX_ATTEMPTS:
                        logger.error(
                            "Finalize integrity error after %d attempts (regen=%s): %s",
                            attempt,
                            regen_lineage is not None,
                            ie,
                        )
                        # SHU-759: Must enqueue a terminal error event before
                        # returning. `stream_ensemble_responses`' `queue.get()`
                        # loop only increments its completed-counter on
                        # `final_message` or `error` events; a bare `raise`
                        # here would leave the task dead with the exception
                        # stored, but the parent loop has no signal — it sits
                        # on `queue.get()` forever (it never reaches the
                        # `finally: asyncio.gather(...)` that would have reaped
                        # the dead task). Concrete hang scenario: 4+ concurrent
                        # regenerates of the same target, the last exhausts
                        # REGEN_MAX_ATTEMPTS, SSE consumer waits until client
                        # timeout. The catch-all `except Exception` below
                        # uses the same pattern for the same reason.
                        # User-facing copy is generic — IntegrityError reprs
                        # leak SQL statement / params / constraint names.
                        # Full detail is already logged with `exc_info=True`
                        # above for server-side debugging.
                        await queue.put(
                            self._create_error_event(
                                "Could not save the response after multiple " "attempts. Please try again.",
                                variant_index,
                                inputs,
                                model_snapshot,
                                model_display_name,
                            )
                        )
                        return
                    logger.info("Regen variant_index conflict on attempt %d; retrying", attempt)
                except Exception as exc:
                    # SHU-759 AC#3: any in-transaction failure (UsageRecorder
                    # save-point flush, conversation update, Message insert)
                    # caused session.__aexit__ to roll back. Atomicity holds
                    # — neither the Message nor the LLMUsage row was
                    # committed. But `stream_ensemble_responses` is still
                    # awaiting `queue.get()` for our final event, so we must
                    # emit one before returning or the SSE consumer hangs
                    # until client timeout. Tagged with a distinct phase
                    # (`finalize_rollback`) so ops can grep this case.
                    logger.error(
                        "Variant finalize phase rolled back",
                        extra={
                            "phase": "finalize_rollback",
                            "conversation_id": conversation_id,
                            "variant_index": variant_index,
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "elapsed_ms": (datetime.now(UTC) - finalize_phase_start).total_seconds() * 1000,
                        },
                        exc_info=True,
                    )
                    # User-facing copy is generic — raw exception reprs can
                    # leak DB / internal detail (savepoint flush errors,
                    # cost-resolver state, etc.). Full detail is already
                    # logged with `exc_info=True` above for server-side
                    # debugging.
                    await queue.put(
                        self._create_error_event(
                            "An internal error prevented saving your response. " "Please try again.",
                            variant_index,
                            inputs,
                            model_snapshot,
                            model_display_name,
                        )
                    )
                    return

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
            # Failure path — record an error Message + failed LLMUsage in one
            # transaction. Hoisted out of the try so the outer catch-all can
            # still surface the original LLM error to the SSE consumer even
            # if the persistence transaction itself rolls back.
            error_text = result.error_message or "An unexpected error occurred"
            try:
                async with session_factory() as session:
                    # SHU-784: stamp stream_state on the apology Message too —
                    # if the LLM call failed AND the client had already left,
                    # the message persists with both `error` and `stream_state`
                    # set. The two metadata fields are independent: `error`
                    # describes WHY this is an apology row; `stream_state`
                    # describes the lifecycle outcome at commit time.
                    error_metadata: dict[str, Any] = {
                        "error": result.error_details if result.error_details else error_text,
                        "stream_state": lifecycle.resolved_reason(),
                    }
                    error_msg = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation_id,
                        role="assistant",
                        content=f"I apologize, but I encountered an error: {error_text}",
                        model_id=inputs.model.id,
                        message_metadata=error_metadata,
                    )
                    session.add(error_msg)
                    await session.flush()

                    # Match the success branch's conversation timestamp bump so a
                    # failed chat still sorts to the top of "recently updated" in
                    # the conversation list. Pre-refactor `_handle_exception` got
                    # this implicitly because `add_message` updated `updated_at`;
                    # the inline-Message-construction approach has to do it
                    # explicitly.
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation_id)
                        .values(updated_at=datetime.now(UTC))
                    )

                    # AC#N7 atomicity: the failed-LLMUsage write composes into
                    # the same transaction as the error Message. The SHU-759
                    # UsageRecorder contract change made record(session=...)
                    # propagate failures; we let them propagate out of this
                    # `async with` block so session.__aexit__ rolls the error
                    # Message back too — Message and LLMUsage land together or
                    # neither lands. (The pre-SHU-759 wrapper that caught and
                    # swallowed `record()` failures here actively defeated
                    # that atomicity once the contract change landed: it would
                    # commit the error Message while the savepoint had already
                    # rolled back the usage row.)
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

                    await session.commit()
            except Exception as exc:
                # Failure-path rollback: neither the error Message nor the
                # failed-LLMUsage row landed. We still need to signal the SSE
                # consumer or `stream_ensemble_responses` will block on its
                # `queue.get()` loop forever — same hang pattern the success
                # branch and IntegrityError handler protect against. The
                # original LLM `error_text` is the most useful payload to
                # surface; the persistence failure is an internal concern.
                logger.error(
                    "Variant finalize failure-path rolled back",
                    extra={
                        "phase": "finalize_rollback",
                        "conversation_id": conversation_id,
                        "variant_index": variant_index,
                        "branch": "failure",
                        "error_type": type(exc).__name__,
                        "elapsed_ms": (datetime.now(UTC) - finalize_phase_start).total_seconds() * 1000,
                    },
                    exc_info=True,
                )

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
                # SHU-784: lifecycle_reason gives ops a one-grep summary of
                # how streams ended. `grep finalize_complete | jq .lifecycle_reason
                # | sort | uniq -c` shows the distribution of complete /
                # client_disconnected / user_terminated / shutdown finals
                # without needing to join across separate disconnect log
                # lines. Read at log-emit time (after the commit), so it
                # reflects the same value stamped in message_metadata.
                "lifecycle_reason": lifecycle.resolved_reason(),
                "terminated": result.terminated,
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
        lifecycle: "StreamLifecycle | None" = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream responses from multiple model configurations in parallel and emit multiplexed server-sent-event style events.

        This coroutine concurrently executes each provided ModelExecutionInputs variant, forwards intermediate deltas and final results into a shared queue, persists the final assistant message and usage, and yields each queued ProviderResponseEvent as a dictionary suitable for SSE delivery. The generator yields content and reasoning deltas as they arrive, final_message events when a variant completes, and error events when a variant fails.

        Parameters
        ----------
            ensemble_inputs (List[ModelExecutionInputs]): One entry per model/variant describing provider, model configuration, context, and rate limits to execute.
            conversation_id (str): Identifier of the conversation to which produced assistant messages will be appended.
            parent_message_id_override (Optional[str]): If provided, use this value as the parent message id for all produced messages; otherwise a new UUID is generated and the first variant may reuse it as the message id.
            force_no_streaming (bool): If True, force non-streaming mode regardless of provider/model configuration settings.
            regen_lineage (RegenLineageInfo | None): SHU-759 regen-only lineage data threaded into finalize. When set, finalize backfills the original target's parent_message_id / variant_index, computes the new variant's variant_index from siblings, and stamps regenerated metadata.
            lifecycle (StreamLifecycle | None): SHU-784 stream-lifecycle handle. When provided, the supervisor stores its task on `lifecycle.supervise_task` (for the shutdown drain) and the per-variant consumer loop observes its event for early-termination signals. When None, a stub is synthesized internally so direct unit-test callers don't have to construct one.

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

        # SHU-784: downstream code (consumer-loop event check, finalize
        # stream_state stamp) treats lifecycle as non-optional. Synthesize a
        # stub when a caller (e.g., a direct unit test) doesn't supply one.
        # The stub is never registered in app.state.in_flight_streams, so it
        # carries no external behavior — finalize still reads its
        # resolved_reason() and stamps `"complete"`.
        if lifecycle is None:
            lifecycle = StreamLifecycle(
                stream_id=str(uuid.uuid4()),
                user_id="",
                conversation_id=conversation_id,
            )

        async def stream_variant(variant_index: int, inputs: "ModelExecutionInputs") -> None:
            """SHU-759: per-variant pipeline as the prepare → stream → finalize split.

            Stream phase runs without any DB writes and returns a
            VariantStreamResult capturing the outcome (success or failure).
            Finalize phase opens its own short-lived session, atomically
            writes the assistant Message + LLMUsage, and enqueues the
            final / error SSE event.

            Both phase methods catch their own failures internally and
            enqueue an error event before returning. The outer try/except
            here is defense-in-depth: ``stream_ensemble_responses`` below
            only increments its ``completed`` counter on ``final_message``
            or ``error`` events, so any exception that escapes the inner
            handlers (drift-guard ``RuntimeError``, future code added
            between the two awaits, a regression in an inner catch-all)
            would leave this task dead with its exception stored and the
            parent ``queue.get()`` blocked until the SSE client times
            out. Catching at the outer level guarantees the consumer
            always sees a terminal event for every variant, regardless
            of where in the pipeline the failure occurred.
            """
            try:
                result = await self._stream_variant_phase(
                    variant_index=variant_index,
                    inputs=inputs,
                    queue=queue,
                    conversation_id=conversation_id,
                    force_no_streaming=force_no_streaming,
                    start_time=start_time,
                    lifecycle=lifecycle,
                )
                # SHU-784: shield finalize. The supervisor is normally
                # disconnect-immune (detached from the SSE generator), but if
                # the shutdown drain (step 10) times out and cancels the
                # supervisor, that cancellation would propagate into the
                # variant tasks. Shielding the finalize await means an
                # incoming cancellation completes the finalize transaction
                # before unwinding — Message + LLMUsage land atomically or
                # neither lands. Without this, cancellation mid-finalize
                # rolls back the `async with session_factory()` block and
                # we lose the row. Belt-and-suspenders against the disconnect
                # bug class even with detachment in place.
                await asyncio.shield(
                    self._finalize_variant_phase(
                        variant_index=variant_index,
                        inputs=inputs,
                        result=result,
                        queue=queue,
                        conversation_id=conversation_id,
                        parent_message_id=parent_message_id,
                        use_parent_as_message_id=use_parent_as_message_id,
                        regen_lineage=regen_lineage,
                        lifecycle=lifecycle,
                    )
                )
            except Exception as exc:
                logger.error(
                    "stream_variant safety-net caught unhandled exception",
                    extra={
                        "phase": "stream_variant_safety_net",
                        "conversation_id": conversation_id,
                        "variant_index": variant_index,
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                model_display_name = getattr(inputs.model_configuration, "name", None) or "unknown"
                await queue.put(
                    self._create_error_event(
                        "An internal error occurred. Please try again.",
                        variant_index,
                        inputs,
                        None,
                        model_display_name,
                    )
                )

        tasks = [
            loop.create_task(
                stream_variant(idx, inputs),
                name=f"chat-stream-variant:{lifecycle.stream_id}:{idx}",
            )
            for idx, inputs in enumerate(ensemble_inputs)
        ]

        # SHU-784: per-stream supervisor. Runs detached from the SSE generator
        # so a client disconnect (which cancels the generator) cannot cancel
        # the variant tasks. The pre-SHU-784 pattern was
        # `await asyncio.gather(*tasks, return_exceptions=True)` inside the
        # generator's `finally`, which propagated cancellation into the
        # variants and rolled back their finalize transactions before commit
        # — the disconnect-persistence bug. The supervisor fires the
        # lifecycle's `on_complete` (registry pop) regardless of variant
        # outcome so a crashed variant doesn't leak its registry entry.
        async def supervise() -> None:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                lifecycle.fire_on_complete()

        lifecycle.supervise_task = loop.create_task(
            supervise(),
            name=f"chat-stream-supervise:{lifecycle.stream_id}",
        )

        completed = 0
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
        # No `finally: await gather(...)` here: variant tasks are detached
        # (see supervisor above). The generator returns once all variants
        # have emitted their terminal events; if the generator is closed
        # early via client disconnect, the variants keep running
        # independently on the event loop and the supervisor cleans up.

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
