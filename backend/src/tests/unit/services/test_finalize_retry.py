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
async def test_finalize_regen_raises_after_max_attempts() -> None:
    """3 IntegrityErrors → IntegrityError propagates after REGEN_MAX_ATTEMPTS.

    The retry loop must not silently swallow an unresolvable conflict
    (e.g., a real bug in variant_index computation that would loop
    forever). After ``REGEN_MAX_ATTEMPTS`` attempts the error escapes.
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
        with pytest.raises(IntegrityError):
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

    # Three attempts consumed, then the error propagated. The fourth commit
    # side-effect (None / success) is never reached.
    assert factory.entered_count == REGEN_MAX_ATTEMPTS
    assert session.commit.await_count == REGEN_MAX_ATTEMPTS
    queue.put.assert_not_called()  # no final event enqueued on terminal failure


@pytest.mark.asyncio
async def test_finalize_non_regen_re_raises_integrity_error_immediately() -> None:
    """Non-regen IntegrityError is unexpected — no retry, propagates on first hit.

    Non-regen variants assign ``variant_index`` from the ensemble loop
    counter (a fixed value per variant), so the UNIQUE constraint should
    never trip. If it does, something is structurally wrong and the
    retry would loop forever — so we re-raise immediately rather than
    burning ``REGEN_MAX_ATTEMPTS`` attempts on a non-recoverable error.
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
        with pytest.raises(IntegrityError):
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
