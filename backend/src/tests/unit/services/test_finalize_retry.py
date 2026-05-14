"""Unit tests for the regen retry-on-conflict logic in
``EnsembleStreamingHelper._finalize_variant_phase`` (SHU-759 / AC #N10).

Concurrent regenerates of the same target message race to claim the
next ``variant_index``. The UNIQUE constraint on
``(parent_message_id, variant_index)`` from migration r009_0001 rejects
the loser; finalize retries up to ``REGEN_MAX_ATTEMPTS`` times,
re-reading siblings on each retry. These tests pin the retry-count
boundary so a future change can't silently turn ``MAX_ATTEMPTS = 3``
into 1 (would lose races) or into a high number (would mask a real
runaway).

Integration tests can't reliably reproduce the race (concurrent timing
in test) so the unit-test layer is the right place per Shu's TESTING.md
gate: this catches retry-count off-by-one and non-regen-rethrow bugs
that an integration test couldn't reliably exercise.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from shu.services.chat_service import (
    ModelExecutionInputs,
    RegenLineageInfo,
    VariantStreamResult,
)
from shu.services.chat_streaming import REGEN_MAX_ATTEMPTS, EnsembleStreamingHelper


class _FakeSessionFactory:
    """Reusable async-context factory.

    `__call__` returns ``self`` so ``async with session_factory() as session``
    enters this object's ``__aenter__`` and yields the bound mock session.
    Each enter increments ``entered_count`` so the test can assert the
    retry loop opened a fresh session per attempt.
    """

    def __init__(self, mock_session) -> None:
        self._session = mock_session
        self.entered_count = 0

    def __call__(self) -> "_FakeSessionFactory":
        return self

    async def __aenter__(self):
        self.entered_count += 1
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


def _build_mock_session(commit_side_effects: list) -> MagicMock:
    """Mock session with the methods _finalize_variant_phase calls.

    ``commit_side_effects`` is a list passed to ``session.commit.side_effect``;
    each entry is either ``None`` (success) or an exception to raise.
    """
    session = MagicMock()
    session.add = MagicMock(return_value=None)
    session.flush = AsyncMock(return_value=None)
    session.commit = AsyncMock(side_effect=commit_side_effects)

    # Generic execute mock — returns the same Result-shaped object for every
    # SELECT/UPDATE. Real call sites are:
    #   - SELECT target Message       -> result.scalar_one_or_none()
    #   - SELECT siblings.variant_idx -> result.all()
    #   - UPDATE Conversation         -> no return needed
    #   - SELECT reload Message       -> result.scalar_one()
    target_row = MagicMock()
    target_row.id = "target-id"
    target_row.parent_message_id = "root-id"  # already backfilled, no legacy update needed
    target_row.variant_index = 0

    reload_row = MagicMock()
    reload_row.id = "new-msg-id"
    reload_row.variant_index = 1

    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=target_row)
    result_mock.scalar_one = MagicMock(return_value=reload_row)
    result_mock.all = MagicMock(return_value=[(0,)])  # one existing sibling at idx 0

    session.execute = AsyncMock(return_value=result_mock)
    return session


def _build_inputs() -> ModelExecutionInputs:
    """Minimal ModelExecutionInputs sufficient for finalize's success branch."""
    model_config = MagicMock()
    model_config.id = "mc-id"
    model_config.name = "Test Config"
    model_config.functionalities = {}

    model = MagicMock()
    model.id = "model-id"
    model.provider_id = "provider-id"
    model.model_name = "test-model"

    return ModelExecutionInputs(
        model_configuration=model_config,
        provider_id="provider-id",
        model=model,
        context_messages=MagicMock(),
        source_metadata=[],
        knowledge_base_ids=None,
        conversation_owner_id="user-id",
    )


def _build_helper() -> EnsembleStreamingHelper:
    """Construct a helper with the minimal mocks finalize touches."""
    chat_service = MagicMock()
    chat_service.db_session = None  # required by the drift-guard assertion
    chat_service._build_model_configuration_metadata = MagicMock(
        return_value={"model_configuration": {"id": "mc-id", "name": "Test Config"}}
    )

    return EnsembleStreamingHelper(
        chat_service=chat_service,
        message_context_builder=MagicMock(),
        config_manager=MagicMock(),
    )


async def _run_finalize_with_n_conflicts(n_conflicts: int) -> tuple[_FakeSessionFactory, MagicMock, MagicMock]:
    """Drive finalize with ``n_conflicts`` IntegrityErrors on commit before success.

    Returns (factory, session, queue) so the caller can assert on
    attempt counts and queue events.
    """
    integrity_err = IntegrityError(statement="INSERT", params={}, orig=Exception("UNIQUE violation"))
    commit_side_effects: list = [integrity_err] * n_conflicts + [None]
    session = _build_mock_session(commit_side_effects)
    factory = _FakeSessionFactory(session)

    helper = _build_helper()
    inputs = _build_inputs()
    result = VariantStreamResult(
        success=True,
        full_content="hello",
        final_source_metadata=[],
        metadata={"response_time_ms": 1.0},
        usage={"input_tokens": 1, "output_tokens": 1, "cost": "0"},
        model_name_for_event="test-model",
        final_event_type="final_message",
    )
    queue = MagicMock()
    queue.put = AsyncMock(return_value=None)

    fake_recorder = MagicMock()
    fake_recorder.record = AsyncMock(return_value=None)

    # Patch get_async_session_local at the call site (chat_streaming module)
    # so the factory we built is what _finalize_variant_phase sees. Also
    # patch get_usage_recorder so the recorder.record(session=...) call
    # doesn't try to touch a real recorder.
    with (
        patch("shu.services.chat_streaming.get_async_session_local", return_value=factory),
        patch("shu.services.chat_streaming.get_usage_recorder", return_value=fake_recorder),
    ):
        await helper._finalize_variant_phase(
            variant_index=1,
            inputs=inputs,
            result=result,
            queue=queue,
            conversation_id="conv-id",
            parent_message_id="root-id",
            use_parent_as_message_id=False,
            regen_lineage=RegenLineageInfo(target_message_id="target-id", root_id="root-id"),
        )

    return factory, session, queue


@pytest.mark.asyncio
async def test_finalize_regen_succeeds_first_attempt_no_conflicts() -> None:
    """0 conflicts → 1 attempt, no retry."""
    factory, session, queue = await _run_finalize_with_n_conflicts(0)
    assert factory.entered_count == 1, f"expected 1 session open, got {factory.entered_count}"
    assert session.commit.await_count == 1
    queue.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_regen_succeeds_after_one_conflict() -> None:
    """1 IntegrityError → 2 total attempts, then success."""
    factory, session, queue = await _run_finalize_with_n_conflicts(1)
    assert factory.entered_count == 2, f"expected 2 session opens, got {factory.entered_count}"
    assert session.commit.await_count == 2
    queue.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_regen_succeeds_after_two_conflicts() -> None:
    """2 IntegrityErrors → 3 total attempts (= REGEN_MAX_ATTEMPTS), then success.

    Boundary case: the 3rd attempt is the last allowed and it succeeds.
    """
    assert REGEN_MAX_ATTEMPTS == 3, (
        f"This test pins behavior at REGEN_MAX_ATTEMPTS=3; "
        f"current value is {REGEN_MAX_ATTEMPTS}. If you intentionally changed "
        f"the retry budget, update this assertion and the test cases below."
    )
    factory, session, queue = await _run_finalize_with_n_conflicts(2)
    assert factory.entered_count == 3, f"expected 3 session opens, got {factory.entered_count}"
    assert session.commit.await_count == 3
    queue.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_regen_emits_terminal_error_after_max_attempts() -> None:
    """REGEN_MAX_ATTEMPTS IntegrityErrors → terminal error event, no raise.

    Concrete hang scenario this guards: 4+ concurrent regenerates of the
    same target. Each one races to claim the next variant_index; the
    UNIQUE constraint rejects all but one per attempt. After
    REGEN_MAX_ATTEMPTS, the loser bails out — but it MUST enqueue a
    terminal error event before exiting. ``stream_ensemble_responses``'
    ``queue.get()`` loop only increments completed on ``final_message``
    or ``error`` events; a bare ``raise`` here would leave the task
    dead with the exception stored, the parent loop blocked on
    ``queue.get()`` forever (never reaching the ``finally``-block
    ``asyncio.gather()`` that would have reaped the dead task), and
    the SSE consumer hung until client timeout.

    Pre-fix this method raised IntegrityError; pre-fix that bug existed
    silently because no integration test exercised 4+ concurrent
    regenerates. Codex review surfaced it.
    """
    integrity_err = IntegrityError(statement="INSERT", params={}, orig=Exception("persistent UNIQUE violation"))
    session = _build_mock_session([integrity_err, integrity_err, integrity_err, None])
    factory = _FakeSessionFactory(session)

    helper = _build_helper()
    inputs = _build_inputs()
    result = VariantStreamResult(
        success=True,
        full_content="hello",
        metadata={"response_time_ms": 1.0},
        usage={"input_tokens": 1, "output_tokens": 1, "cost": "0"},
        model_name_for_event="test-model",
        final_event_type="final_message",
    )
    queue = MagicMock()
    queue.put = AsyncMock(return_value=None)
    fake_recorder = MagicMock()
    fake_recorder.record = AsyncMock(return_value=None)

    with (
        patch("shu.services.chat_streaming.get_async_session_local", return_value=factory),
        patch("shu.services.chat_streaming.get_usage_recorder", return_value=fake_recorder),
    ):
        # No exception propagates — the IntegrityError handler converts to a
        # terminal error event so the parent SSE loop can complete.
        await helper._finalize_variant_phase(
            variant_index=1,
            inputs=inputs,
            result=result,
            queue=queue,
            conversation_id="conv-id",
            parent_message_id="root-id",
            use_parent_as_message_id=False,
            regen_lineage=RegenLineageInfo(target_message_id="target-id", root_id="root-id"),
        )

    # All three attempts consumed; the fourth commit side-effect (None /
    # success) is never reached.
    assert factory.entered_count == REGEN_MAX_ATTEMPTS
    assert session.commit.await_count == REGEN_MAX_ATTEMPTS

    # Terminal error event was enqueued so the SSE consumer doesn't hang.
    queue.put.assert_awaited_once()
    enqueued_event = queue.put.call_args[0][0]
    assert enqueued_event.type == "error", (
        f"expected terminal error event after exhausted retries, got {enqueued_event.type!r}"
    )
    # User-facing message must be generic. IntegrityError reprs leak SQL
    # statement, params, and constraint names; the underlying exception is
    # logged server-side with exc_info=True but must not reach the client.
    assert "Could not save the response" in enqueued_event.content
    assert "persistent UNIQUE violation" not in enqueued_event.content, (
        f"raw IntegrityError detail leaked to client: {enqueued_event.content!r}"
    )


@pytest.mark.asyncio
async def test_finalize_non_regen_emits_terminal_error_on_integrity_failure() -> None:
    """Non-regen IntegrityError is unexpected (variant_index is fixed by
    the ensemble loop counter, so the UNIQUE constraint should never
    trip), but if it does we still need a terminal error event rather
    than a bare raise — same SSE-hang reasoning as the regen-exhausted
    case. Non-regen does NOT retry because retrying with the same fixed
    variant_index would just loop forever; one attempt then a terminal
    error.
    """
    integrity_err = IntegrityError(statement="INSERT", params={}, orig=Exception("unexpected"))
    session = _build_mock_session([integrity_err])
    factory = _FakeSessionFactory(session)

    helper = _build_helper()
    inputs = _build_inputs()
    result = VariantStreamResult(
        success=True,
        full_content="hello",
        metadata={"response_time_ms": 1.0},
        usage={"input_tokens": 1, "output_tokens": 1, "cost": "0"},
        model_name_for_event="test-model",
        final_event_type="final_message",
    )
    queue = MagicMock()
    queue.put = AsyncMock(return_value=None)
    fake_recorder = MagicMock()
    fake_recorder.record = AsyncMock(return_value=None)

    with (
        patch("shu.services.chat_streaming.get_async_session_local", return_value=factory),
        patch("shu.services.chat_streaming.get_usage_recorder", return_value=fake_recorder),
    ):
        # No exception propagates — the IntegrityError handler converts to a
        # terminal error event so the parent SSE loop can complete.
        await helper._finalize_variant_phase(
            variant_index=1,
            inputs=inputs,
            result=result,
            queue=queue,
            conversation_id="conv-id",
            parent_message_id="root-id",
            use_parent_as_message_id=False,
            regen_lineage=None,  # non-regen path
        )

    # Only one attempt; non-regen does not retry.
    assert factory.entered_count == 1
    assert session.commit.await_count == 1

    # Terminal error event enqueued, with generic user-facing copy (no
    # raw exception content — IntegrityError reprs leak SQL detail).
    queue.put.assert_awaited_once()
    enqueued_event = queue.put.call_args[0][0]
    assert enqueued_event.type == "error"
    assert "Could not save the response" in enqueued_event.content
    assert "unexpected" not in enqueued_event.content, (
        f"raw IntegrityError detail leaked to client: {enqueued_event.content!r}"
    )


@pytest.mark.asyncio
async def test_finalize_rolls_back_atomically_on_usage_recorder_failure() -> None:
    """SHU-759 AC#3: a recorder failure mid-transaction rolls back the
    whole unit of work and emits an error event.

    Pre-fix, ``UsageRecorder.record(session=...)`` wrapped its body in a
    blanket ``try/except`` that logged and swallowed. So when ``_insert``
    tripped its nested savepoint (cost resolver, degenerate provider row,
    flush failure, etc.), the savepoint rolled back the LLMUsage portion
    but the exception was discarded; finalize then ran the outer
    ``session.commit()`` and persisted the assistant Message *without* a
    matching LLMUsage row — the exact "Message exists, LLMUsage absent"
    state AC#3 forbids.

    Post-fix, ``record(session=...)`` propagates the failure. The outer
    ``async with session_factory()`` context manager rolls back the
    entire transaction via ``__aexit__`` (no Message, no LLMUsage, in
    line with AC#3). The catch-all in ``_finalize_variant_phase``
    converts the rollback into an error SSE event so
    ``stream_ensemble_responses`` doesn't block forever on a
    ``queue.get()`` waiting for a ``final_message`` that will never
    arrive.

    Locks both invariants in place so the failure-path coverage gap
    flagged in code review can't silently reopen.
    """
    # Simulate any in-transaction recorder failure. Pre-fix this was
    # swallowed inside record(); session.commit() then committed the
    # Message anyway. The assertion on commit.assert_not_called() below
    # is what pins the atomicity invariant.
    recorder_error = RuntimeError("simulated savepoint failure")

    session = _build_mock_session([None])  # commit never reached
    factory = _FakeSessionFactory(session)

    helper = _build_helper()
    inputs = _build_inputs()
    result = VariantStreamResult(
        success=True,
        full_content="hello",
        final_source_metadata=[],
        metadata={"response_time_ms": 1.0},
        usage={"input_tokens": 1, "output_tokens": 1, "cost": "0"},
        model_name_for_event="test-model",
        final_event_type="final_message",
    )
    queue = MagicMock()
    queue.put = AsyncMock(return_value=None)

    fake_recorder = MagicMock()
    fake_recorder.record = AsyncMock(side_effect=recorder_error)

    with (
        patch("shu.services.chat_streaming.get_async_session_local", return_value=factory),
        patch("shu.services.chat_streaming.get_usage_recorder", return_value=fake_recorder),
    ):
        # No exception escapes — the catch-all converts to an error event
        # so the SSE consumer (stream_ensemble_responses) can complete.
        await helper._finalize_variant_phase(
            variant_index=1,
            inputs=inputs,
            result=result,
            queue=queue,
            conversation_id="conv-id",
            parent_message_id="root-id",
            use_parent_as_message_id=False,
        )

    # Atomicity: session.commit() never ran. Message + Conversation
    # updates pending in the session at the point of failure are rolled
    # back by session.__aexit__. Neither row lands in the database.
    session.commit.assert_not_called()

    # The recorder failed on its single call — no retry on non-IntegrityError.
    fake_recorder.record.assert_awaited_once()

    # An error event must be enqueued so the SSE consumer doesn't hang.
    # User-facing copy is generic — raw exception text can leak DB /
    # savepoint / cost-resolver internals. The exception is logged
    # server-side with exc_info=True instead.
    queue.put.assert_awaited_once()
    enqueued_event = queue.put.call_args[0][0]
    assert enqueued_event.type == "error", (
        f"expected error event for rolled-back finalize, got {enqueued_event.type!r}"
    )
    assert "internal error prevented saving" in enqueued_event.content
    assert "simulated savepoint failure" not in enqueued_event.content, (
        f"raw exception text leaked to client: {enqueued_event.content!r}"
    )


@pytest.mark.asyncio
async def test_finalize_failure_path_rolls_back_atomically_on_usage_recorder_failure() -> None:
    """SHU-759 AC#N7: the failure-path Message + failed-LLMUsage write
    is also atomic — symmetric with the success-path test above.

    Pre-fix, the failure path wrapped ``record(session=...)`` in a
    ``try/except Exception`` that logged at WARNING and continued. The
    sibling ``session.commit()`` would then commit the error Message
    *without* a matching LLMUsage row — the same "Message exists,
    LLMUsage absent" state AC#N7 was supposed to prevent, just on the
    other branch. The wrapper was correct under the SHU-715 unified-
    swallow contract, but stopped being correct once SHU-759 changed
    ``record(session=...)`` to propagate.

    Post-fix, dropping the wrapper lets ``record()`` failures propagate
    out of the ``async with session_factory()`` block; ``__aexit__``
    rolls back the whole transaction (no error Message, no failed
    LLMUsage row), and the outer catch-all logs at
    ``phase=finalize_rollback`` and enqueues the error event so the
    SSE consumer still completes.

    The user-visible SSE event uses the original LLM ``error_text`` —
    the persistence failure is an internal concern.
    """
    recorder_error = RuntimeError("simulated savepoint failure in failure branch")

    session = _build_mock_session([None])  # commit never reached
    factory = _FakeSessionFactory(session)

    helper = _build_helper()
    inputs = _build_inputs()
    result = VariantStreamResult(
        success=False,
        error_message="upstream LLM blew up: connection refused",
        error_type="LLMProviderError",
        error_details={"status_code": 503},
    )
    queue = MagicMock()
    queue.put = AsyncMock(return_value=None)

    fake_recorder = MagicMock()
    fake_recorder.record = AsyncMock(side_effect=recorder_error)

    with (
        patch("shu.services.chat_streaming.get_async_session_local", return_value=factory),
        patch("shu.services.chat_streaming.get_usage_recorder", return_value=fake_recorder),
    ):
        # No exception escapes — the catch-all converts to an error event.
        await helper._finalize_variant_phase(
            variant_index=0,
            inputs=inputs,
            result=result,
            queue=queue,
            conversation_id="conv-id",
            parent_message_id="root-id",
            use_parent_as_message_id=False,
        )

    # Atomicity: session.commit() never ran. The error Message + Conversation
    # update pending in the session at the point of failure are rolled back
    # by session.__aexit__. Neither row lands in the database.
    session.commit.assert_not_called()

    fake_recorder.record.assert_awaited_once()

    # Error event MUST be enqueued — same SSE-hang reasoning as the success
    # branch's rollback path. The event carries the original LLM error_text,
    # not the persistence failure (the latter is an internal concern).
    queue.put.assert_awaited_once()
    enqueued_event = queue.put.call_args[0][0]
    assert enqueued_event.type == "error"
    assert "upstream LLM blew up" in enqueued_event.content, (
        f"expected the original LLM error_text in the SSE event, got {enqueued_event.content!r}"
    )


@pytest.mark.asyncio
async def test_stream_variant_safety_net_catches_unhandled_exception() -> None:
    """SHU-759: the defense-in-depth try/except in ``stream_variant``
    catches any exception that escapes the phase methods' internal
    handlers, so ``stream_ensemble_responses``' ``queue.get()`` loop
    never blocks forever.

    Concrete trigger this guards: the drift-guard ``RuntimeError`` at
    the top of ``_stream_variant_phase`` / ``_finalize_variant_phase``
    — the one explicit raise that bypasses both phases' own try blocks.
    Without the safety net at the ``stream_variant`` closure level, a
    misconfigured prepare phase (``chat_service.db_session`` not nulled)
    would propagate through the task, leave it dead with the exception
    stored, and hang the SSE consumer until client timeout.

    Beyond the drift guard, this is forward-protection against the
    SSE-hang bug class itself — every prior fix in this code-review
    cycle patched one inner escape path (IntegrityError raise, success-
    branch unhandled exception, failure-branch atomicity wrapper, etc.).
    The outer safety net closes the bug class at the choke point so
    future regressions in any inner handler can't reintroduce hangs.
    """
    helper = _build_helper()
    inputs = _build_inputs()

    # Simulate an exception that the inner handlers in
    # _stream_variant_phase wouldn't catch (e.g., the drift-guard
    # RuntimeError fires before the method's try block, or a future
    # bug raises before the handler-protected work).
    unhandled_error = RuntimeError(
        "_stream_variant_phase must run after prepare detached the request session"
    )

    mock_finalize = AsyncMock(return_value=None)
    with (
        patch.object(
            helper,
            "_stream_variant_phase",
            new=AsyncMock(side_effect=unhandled_error),
        ),
        patch.object(helper, "_finalize_variant_phase", new=mock_finalize),
    ):
        # Consume the full SSE stream — must complete cleanly, must not hang.
        events = []
        async for event in helper.stream_ensemble_responses(
            ensemble_inputs=[inputs],
            conversation_id="conv-id",
            parent_message_id_override="root-id",
            force_no_streaming=False,
        ):
            events.append(event)

        # _finalize_variant_phase must NOT have been called —
        # _stream_variant_phase raised before returning a
        # VariantStreamResult, so the second await is never reached.
        # Assert inside the `with` block so the mock is still bound.
        mock_finalize.assert_not_called()

    # Exactly one error event reached the consumer; the parent loop
    # incremented `completed` and exited normally.
    assert len(events) == 1, (
        f"expected exactly one terminal error event, got {len(events)}: {events!r}"
    )
    assert events[0].type == "error", (
        f"expected error event, got {events[0].type!r}"
    )
    assert "internal error" in events[0].content.lower(), (
        f"expected generic safety-net message, got {events[0].content!r}"
    )
    # User-facing copy must not carry raw exception text (lessons from the
    # SSE sanitization finding — generic copy in the safety net too).
    assert "drift" not in events[0].content.lower()
    assert "RuntimeError" not in events[0].content
