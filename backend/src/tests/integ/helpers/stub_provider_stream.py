"""SHU-803 AC10: test helper that stubs the upstream LLM stream.

The integration tests for the real-partial-usage capture path (the
SHU-803 abuse-vector close-out) need to feed deterministic chunk
sequences shaped exactly like each provider's wire protocol
(Anthropic ``message_start`` / ``message_delta``, Gemini cumulative
``usageMetadata``, OpenAI Chat Completions end-only ``usage``,
OpenAI Responses ``response.completed``) to the REAL adapter's
``handle_provider_event`` method. Mocking handle_provider_event would
defeat the point of these tests — they exist to validate the
capture logic against the actual chunk shapes.

The implementation strategy: monkey-patch
:meth:`UnifiedLLMClient._stream_response` (the inner streaming method
that does the upstream HTTP request and iterates SSE lines) with a
stub that yields events from the test's chunk fixture. Everything
above ``_stream_response`` (the ``chat_completion`` plumbing, the
retry layer, ``_call_provider``'s drain loop, ``_finalize_variant_phase``'s
LLMUsage writes) runs unchanged — the test validates the entire chain
minus the HTTP transport.

Per-chunk delays let tests position the terminate POST precisely
between chunks (e.g., AFTER ``message_start`` but BEFORE the first
``message_delta`` for the Anthropic pre-delta scenario).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import patch

from shu.llm.client import UnifiedLLMClient
from shu.services.providers.adapter_base import (
    ProviderContentDeltaEventResult,
    ProviderErrorEventResult,
    ProviderEventResult,
    ProviderFinalEventResult,
    ProviderReasoningDeltaEventResult,
)


@dataclass
class StubChunk:
    """A single upstream chunk yielded to the adapter's ``handle_provider_event``.

    Attributes:
        data: The raw chunk dict — shape determined by the provider's
            streaming protocol (e.g. Anthropic's ``{"type": "message_start",
            "message": {"usage": {...}}}``).
        delay_seconds: Wall-clock delay BEFORE this chunk is fed. Used to
            position a terminate POST between specific chunks. Default
            ``0.0`` means no pause; tests typically set ~0.3s on the
            chunk immediately AFTER the position they want terminate to
            land at (the test's poll-and-terminate cycle takes ~50ms, so
            a 300ms gap is comfortable).
    """

    data: dict[str, Any]
    delay_seconds: float = 0.0


@dataclass
class StubStreamFixture:
    """Configured chunk sequence + optional per-fixture knobs.

    The stub patches ``_stream_response`` to iterate ``chunks`` and feed
    each one to the adapter's ``handle_provider_event``. After the chunk
    loop the stub also calls ``finalize_provider_events`` to mimic the
    real ``_stream_response`` flow (tool-call follow-ups, late final
    events, etc.).
    """

    chunks: list[StubChunk] = field(default_factory=list)


@contextmanager
def stub_provider_stream(fixture: StubStreamFixture):
    """Install the chunk-based stub for the duration of the with-block.

    Patches :meth:`UnifiedLLMClient._stream_response` at the class level
    so any ``UnifiedLLMClient`` instance constructed inside the with-block
    uses the stub. Tests using this helper MUST drive a single stream
    per fixture — concurrent variants in an ensemble would share the
    same chunks (probably not what you want; SHU-803 tests use
    single-variant configurations).

    Yields ``None``. The fixture is closure-captured by the stub method.
    """

    async def stub_stream_response(
        self: UnifiedLLMClient,
        payload: dict[str, Any],
        model: str,
        start_time: datetime,
        request_timeout: float | None = None,
    ) -> AsyncGenerator[ProviderEventResult, None]:
        """Faithful replica of :meth:`UnifiedLLMClient._stream_response`
        minus the HTTP transport. Feeds each fixture chunk to the real
        adapter's ``handle_provider_event``, then runs
        ``finalize_provider_events`` after the loop. Mirrors the
        content-delta-yields-immediately / final-yields-at-end ordering
        the production method uses so ``_call_provider`` upstream sees
        an indistinguishable event sequence.
        """
        final_event: ProviderEventResult | None = None

        for chunk in fixture.chunks:
            if chunk.delay_seconds > 0:
                await asyncio.sleep(chunk.delay_seconds)
            else:
                # Yield control even on zero-delay chunks so the event
                # loop can interleave the terminate POST polling task.
                await asyncio.sleep(0)

            provider_event = await self.provider_adapter.handle_provider_event(chunk.data)
            if provider_event:
                if isinstance(
                    provider_event,
                    (ProviderContentDeltaEventResult, ProviderReasoningDeltaEventResult),
                ):
                    yield provider_event
                elif isinstance(provider_event, (ProviderFinalEventResult, ProviderErrorEventResult)):
                    # Captured for end-of-stream emission, matching the
                    # production semantics in _stream_response.
                    final_event = provider_event

        # Mirror production: drain finalize events after the chunk loop.
        # Tool-call results land here; late finals captured too.
        finalize_events = await self.provider_adapter.finalize_provider_events() or []
        for event in finalize_events:
            if isinstance(event, (ProviderFinalEventResult, ProviderErrorEventResult)):
                final_event = event
                continue
            yield event

        if final_event is not None:
            yield final_event

    with patch.object(UnifiedLLMClient, "_stream_response", new=stub_stream_response):
        yield


# -----------------------------------------------------------------------------
# Per-provider canned chunk shapes for the SHU-803 AC10 integration tests.
# Each helper returns a StubStreamFixture targeting one of the four protocols.
# Tests typically inject a 300ms delay on the chunk BEFORE the natural
# terminate-fire point so the test's terminate POST lands between chunks
# rather than racing the next handle_provider_event.
# -----------------------------------------------------------------------------


def anthropic_message_start_then_delta_fixture(
    *,
    input_tokens: int,
    output_tokens: int,
    text_content: str = "Hello, world.",
    delay_before_message_delta_s: float = 0.3,
) -> StubStreamFixture:
    """Anthropic shape: message_start (nested usage with input_tokens),
    content_block_start, content_block_delta with text, message_delta
    (top-level usage with output_tokens), message_stop.

    The ``delay_before_message_delta_s`` parameter positions the
    terminate POST. By default (0.3s) the POST has comfortable time to
    land AFTER the first content_block_delta but BEFORE message_delta —
    so the test asserting "terminate AFTER message_delta" needs to
    structure its timing accordingly (see the AC10 tests).
    """
    return StubStreamFixture(
        chunks=[
            StubChunk(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stub",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-stub",
                        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                    },
                },
                delay_seconds=0.0,
            ),
            StubChunk(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                delay_seconds=0.0,
            ),
            StubChunk(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text_content}},
                delay_seconds=0.0,
            ),
            StubChunk(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                },
                delay_seconds=delay_before_message_delta_s,
            ),
            StubChunk({"type": "message_stop"}, delay_seconds=0.05),
        ]
    )


def anthropic_pre_delta_fixture(*, input_tokens: int, delay_before_first_content_s: float = 0.4) -> StubStreamFixture:
    """Anthropic shape positioned so terminate fires AFTER message_start
    but BEFORE the first content_block_delta lands. The pre-delta gap
    is the load-bearing window for asserting input_tokens > 0 with
    output_tokens == 0.
    """
    return StubStreamFixture(
        chunks=[
            StubChunk(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stub",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-stub",
                        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                    },
                },
                delay_seconds=0.0,
            ),
            # Big pause here — terminate POST lands during this gap.
            StubChunk(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                delay_seconds=delay_before_first_content_s,
            ),
            StubChunk(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi."}},
                delay_seconds=0.0,
            ),
            # No message_delta here — fixture ends before output_tokens
            # would have been emitted. Drain sees end-of-stream and exits.
        ]
    )


def gemini_cumulative_usage_fixture(
    *,
    final_prompt_tokens: int,
    final_candidates_tokens: int,
    text_content: str = "Hi.",
    delay_before_final_chunk_s: float = 0.3,
) -> StubStreamFixture:
    """Gemini shape: every chunk carries ``usageMetadata`` cumulative-to-date.
    Tests asserting the snapshot reflects the LAST-seen counts.
    """
    return StubStreamFixture(
        chunks=[
            StubChunk(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": text_content[:1]}], "role": "model"},
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": final_prompt_tokens,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": final_prompt_tokens + 1,
                    },
                },
                delay_seconds=0.0,
            ),
            StubChunk(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": text_content[1:]}], "role": "model"},
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": final_prompt_tokens,
                        "candidatesTokenCount": final_candidates_tokens,
                        "totalTokenCount": final_prompt_tokens + final_candidates_tokens,
                    },
                },
                delay_seconds=delay_before_final_chunk_s,
            ),
        ]
    )


def openai_completions_end_usage_fixture(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    text_content: str = "Final answer.",
    wire_cost: str | None = None,
    delay_before_usage_chunk_s: float = 0.3,
) -> StubStreamFixture:
    """OpenAI Chat Completions shape (also OpenRouter / LM Studio / Ollama):
    content chunks, then a final ``usage`` chunk before ``[DONE]``.

    The ``wire_cost`` argument controls whether the usage chunk carries
    ``usage.cost`` (the OpenRouter authoritative-wire-cost path) — pass
    a stringified Decimal to populate it, or None to omit (the DB-rate
    fallback path AC9d covers).
    """
    usage_chunk_usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if wire_cost is not None:
        usage_chunk_usage["cost"] = wire_cost

    return StubStreamFixture(
        chunks=[
            StubChunk(
                {
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant", "content": text_content[:5]}, "finish_reason": None}
                    ],
                },
                delay_seconds=0.0,
            ),
            StubChunk(
                {
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {"content": text_content[5:]}, "finish_reason": None}
                    ],
                },
                delay_seconds=0.0,
            ),
            StubChunk(
                {
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
                delay_seconds=delay_before_usage_chunk_s,
            ),
            StubChunk(
                {
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": usage_chunk_usage,
                },
                delay_seconds=0.0,
            ),
        ]
    )


@contextmanager
def stub_responses_cancel_transport(handler):
    """Pre-install an ``httpx.MockTransport``-backed ``_cancel_client`` on
    every ``ResponsesAdapter`` instance constructed inside the with-block.

    Used by the AC10 ``test_terminate_responses_cancel_called_via_mock_transport``
    integration test to assert the cancel POST URL ends in
    ``/responses/{id}/cancel``. The handler closure captures each
    intercepted request so the test can introspect them.

    This patches ``ResponsesAdapter.__init__`` rather than ``httpx.AsyncClient``
    so the test framework's own httpx clients (used for SSE streaming etc.)
    are unaffected — only adapter-owned cancel clients get the mock transport.
    """
    from shu.services.providers.adapters.responses_adapter import ResponsesAdapter
    import httpx as _httpx

    transport = _httpx.MockTransport(handler)
    original_init = ResponsesAdapter.__init__

    def patched_init(self, context):
        original_init(self, context)
        # Pre-install — cancel() short-circuits the lazy init when
        # self._cancel_client is already set.
        self._cancel_client = _httpx.AsyncClient(transport=transport, timeout=2.0)

    with patch.object(ResponsesAdapter, "__init__", patched_init):
        yield


def openai_responses_response_completed_fixture(
    *,
    input_tokens: int,
    output_tokens: int,
    response_id: str = "resp_stub",
    text_content: str = "Final answer.",
    wire_cost: str | None = None,
    delay_before_completed_s: float = 0.3,
) -> StubStreamFixture:
    """OpenAI Responses API shape: response.created (with response.id),
    response.output_text.delta chunks, then response.completed with
    usage in response.usage."""
    completed_usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if wire_cost is not None:
        completed_usage["cost"] = wire_cost

    return StubStreamFixture(
        chunks=[
            StubChunk(
                {"type": "response.created", "response": {"id": response_id}},
                delay_seconds=0.0,
            ),
            StubChunk(
                {"type": "response.output_text.delta", "delta": text_content[:5]},
                delay_seconds=0.0,
            ),
            StubChunk(
                {"type": "response.output_text.delta", "delta": text_content[5:]},
                delay_seconds=0.0,
            ),
            StubChunk(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": text_content}],
                            }
                        ],
                        "usage": completed_usage,
                    },
                },
                delay_seconds=delay_before_completed_s,
            ),
        ]
    )
