"""SHU-803 follow-up: terminate-time early-persist integration tests.

The base SHU-803 suite ([test_chat_force_terminate_real_usage_integration](test_chat_force_terminate_real_usage_integration.py))
proves drain captures usage correctly per provider. This suite proves
the orthogonal guarantee that the **partial assistant Message row is
committed at terminate-signal time, not drain-finish time** — the fix
for two pre-fix bugs:

(a) **Vanishing content during drain.** A navigation refetch landing
    while the backend was still consuming upstream silently returned
    zero assistant rows (the DB write only happened at drain-finish).
    The user saw the partial answer disappear, then reappear after a
    later refresh ~90s later on OpenRouter.

(b) **Chronological ordering race.** If the user clicked Stop on a slow
    stream and immediately sent a follow-up, the follow-up would
    finalize first (created_at=now). The terminated stream's
    drain-finish commit landed seconds-to-minutes later with
    created_at=drain-finish, sorting AFTER the follow-up. A refetch
    ordered by created_at showed messages out of conversational order.

Coverage:

- :meth:`test_terminate_then_followup_preserves_chronological_ordering`
  — the headline race fix. Terminate stream A, send stream B in the
  same conversation; assert ``user_A < assistant_A_terminated <
  user_B < assistant_B_complete`` by created_at.

- :meth:`test_terminate_during_regenerate_assigns_correct_variant_index`
  — proves the early-persist callback's lifted regen-aware INSERT
  block (sibling-max variant_index + regenerated metadata) works. The
  whole point of the Plan agent's option (B refined) was to make
  regen+terminate work; this is the test that proves it.

- :meth:`test_natural_completion_does_not_take_early_persist_path` —
  sanity / no-regression. Stream a chat to natural end, assert the
  single Message has ``stream_state=complete``.
"""

import asyncio
import logging

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.auth import cleanup_framework_test_admin, cleanup_test_user, create_active_user_with_id
from integ.helpers.stub_provider_stream import (
    anthropic_message_start_then_delta_fixture,
    anthropic_pre_delta_fixture,
    stub_provider_stream,
)
from integ.helpers.wait_for_message import wait_for_message_persisted
from integ.response_utils import extract_data
from integ.test_chat_force_terminate_integration import _capture_stream_id_and_terminate
from integ.test_chat_force_terminate_real_usage_integration import (
    _cleanup_provider_resources,
    _create_provider_model_conversation,
    _ensure_local_provider_type,
)
from shu.core.logging import get_logger

logger = get_logger(__name__)


async def _send_message_to_completion(
    client,
    *,
    conv_id: str,
    user_headers: dict,
    message_text: str,
) -> None:
    """Send a chat message and drain the SSE stream to its natural end.

    Counterpart to :func:`_capture_stream_id_and_terminate` for the
    "no terminate" case. Used by the chronological-ordering test to
    drive the second (post-terminate) stream to completion so its
    Message row's ``stream_state`` is ``"complete"``.
    """
    async with client.stream(
        "POST",
        f"/api/v1/chat/conversations/{conv_id}/send",
        json={"message": message_text, "rag_rewrite_mode": "no_rag"},
        headers=user_headers,
    ) as response:
        assert response.status_code == 200, response.text
        async for _line in response.aiter_lines():
            pass


async def _capture_regen_stream_id_and_terminate(  # noqa: C901  # mirrors the parent helper's structure
    client,
    *,
    target_assistant_message_id: str,
    conv_id: str,
    user_headers: dict,
) -> tuple[str | None, dict | None]:
    """Counterpart to :func:`_capture_stream_id_and_terminate` for the
    ``/api/v1/chat/messages/{id}/regenerate`` endpoint.

    Drives the regenerate stream and POSTs terminate concurrently. The
    polling strategy — peek ``app.state.in_flight_streams`` — is
    identical to the /send-endpoint helper; only the request shape
    changes. See the parent helper's docstring for the rationale.
    """
    stream_id_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def _consume_regen() -> None:
        try:
            async with client.stream(
                "POST",
                f"/api/v1/chat/messages/{target_assistant_message_id}/regenerate",
                json={},
                headers=user_headers,
            ) as response:
                if response.status_code != 200:
                    if not stream_id_future.done():
                        stream_id_future.set_exception(
                            AssertionError(f"regenerate failed: {response.status_code}")
                        )
                    return
                async for _line in response.aiter_lines():
                    pass
        except Exception as exc:
            if not stream_id_future.done():
                stream_id_future.set_exception(exc)

    async def _poll_registry(known_ids: set[str]) -> None:
        from shu.main import app as _app

        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            registry = getattr(_app.state, "in_flight_streams", {}) or {}
            matching = sorted(
                stream_id
                for stream_id, lifecycle in registry.items()
                if stream_id not in known_ids
                and getattr(lifecycle, "conversation_id", None) == conv_id
            )
            if matching:
                stream_id_future.set_result(matching[0])
                return
            if asyncio.get_running_loop().time() >= deadline:
                if not stream_id_future.done():
                    stream_id_future.set_exception(
                        AssertionError("no new regen stream_id appeared in app.state within 5s")
                    )
                return
            await asyncio.sleep(0.005)

    from shu.main import app as _app

    pre_existing = set((getattr(_app.state, "in_flight_streams", {}) or {}).keys())
    consumer = asyncio.create_task(_consume_regen())
    poller = asyncio.create_task(_poll_registry(pre_existing))
    try:
        stream_id = await asyncio.wait_for(stream_id_future, timeout=5.0)
        terminate_response = await client.post(
            f"/api/v1/chat/streams/{stream_id}/terminate",
            headers=user_headers,
        )
        return stream_id, terminate_response
    finally:
        for task in (poller, consumer):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=10.0)
                except TimeoutError:
                    task.cancel()
                except Exception:
                    pass


class _EarlyPersistLogCapture:
    """Context manager that captures `phase=early_persist_terminated`
    log records emitted by the chat-streaming module during the test
    body. Used as the load-bearing evidence that the early-persist
    callback fired (the Message-row state alone can't distinguish
    early-persist from the legacy drain-finish INSERT path on
    fast-drain stub providers, since both end up with the same
    stream_state).
    """

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self._handler: logging.Handler | None = None

    def __enter__(self) -> "_EarlyPersistLogCapture":
        class _Capture(logging.Handler):
            def __init__(self, sink: list[logging.LogRecord]) -> None:
                super().__init__(level=logging.INFO)
                self._sink = sink

            def emit(self, record: logging.LogRecord) -> None:
                phase = getattr(record, "phase", None)
                if phase == "early_persist_terminated":
                    self._sink.append(record)

        self._handler = _Capture(self.records)
        get_logger("services.chat_streaming").addHandler(self._handler)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._handler is not None:
            get_logger("services.chat_streaming").removeHandler(self._handler)
            self._handler = None


class ChatEarlyPersistIntegrationTest(BaseIntegrationTestSuite):
    """SHU-803 follow-up — terminate-time early-persist coverage."""

    def get_suite_name(self) -> str:
        return "Chat Early Persist Integration"

    def get_suite_description(self) -> str:
        return (
            "SHU-803 follow-up: validates the partial assistant Message "
            "row is committed at terminate-signal time, not drain-finish "
            "time. Covers the chronological-ordering race fix, the "
            "regen+terminate variant_index assignment, and the no-"
            "regression sanity for natural completion."
        )

    def get_test_functions(self):
        return [
            self.test_terminate_then_followup_preserves_chronological_ordering,
            self.test_terminate_during_regenerate_assigns_correct_variant_index,
            self.test_natural_completion_does_not_take_early_persist_path,
            self.test_terminate_during_silent_provider_gap_fires_early_persist_immediately,
            self.test_zz_teardown_test_admin,
        ]

    async def test_zz_teardown_test_admin(self, client, db, auth_headers):
        await cleanup_framework_test_admin(db)

    async def _setup(self, client, db, auth_headers):
        """Provision a user + provider + model_config + conversation
        using the same anthropic stub shape the existing AC10 suite
        uses. Returns the bundle the tests need plus the per-test
        cleanup ids.
        """
        await _ensure_local_provider_type(db)
        admin_headers = auth_headers
        user_headers, user_id = await create_active_user_with_id(client, admin_headers)
        provider_id, model_id, model_config_id, conv_id = await _create_provider_model_conversation(
            client,
            admin_headers,
            user_headers,
            provider_type="anthropic",
            provider_name="shu803-early-persist",
        )
        return {
            "user_headers": user_headers,
            "user_id": user_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "model_config_id": model_config_id,
            "conv_id": conv_id,
        }

    async def _list_messages_chronological(self, db, conv_id: str) -> list:
        """Return the conversation's messages ordered by created_at
        ASC. The DB ORM session is rolled back before the query so we
        see committed rows from any in-flight transactions.
        """
        from sqlalchemy import select

        from shu.models.llm_provider import Message

        await db.rollback()
        result = await db.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at.asc())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 1. Chronological ordering race fix
    # ------------------------------------------------------------------
    async def test_terminate_then_followup_preserves_chronological_ordering(self, client, db, auth_headers):
        """The headline bug. Terminate stream A, send stream B; assert
        ``user_A.created_at < assistant_A_terminated.created_at <
        user_B.created_at < assistant_B_complete.created_at``.

        Pre-fix, assistant_A's created_at reflected drain-finish time
        — possibly minutes after Stop was clicked — so the timeline
        would interleave: user_A, user_B, assistant_B, then much later
        assistant_A. The early-persist contract guarantees
        assistant_A's row commits at signal time, restoring
        chronological order.
        """
        ctx = await self._setup(client, db, auth_headers)
        try:
            # Stream A — long-ish chunk delay so terminate POST lands
            # AFTER the first content_delta but BEFORE message_delta.
            fixture_a = anthropic_message_start_then_delta_fixture(
                input_tokens=11,
                output_tokens=22,
                text_content="partial answer A",
                delay_before_message_delta_s=0.3,
            )
            with _EarlyPersistLogCapture() as log_capture, stub_provider_stream(fixture_a):
                stream_id_a, terminate_resp_a = await _capture_stream_id_and_terminate(
                    client, conv_id=ctx["conv_id"], user_headers=ctx["user_headers"]
                )
            assert stream_id_a is not None
            assert terminate_resp_a is not None
            assert terminate_resp_a.status_code == 202

            # Load-bearing: the early-persist callback fired. Without
            # this, the test passes for the wrong reason — the
            # message_state ordering assertion below could be
            # satisfied by stub-driven drain completing within a
            # millisecond of finalize, masking a regression in the
            # callback wiring.
            assert len(log_capture.records) == 1, (
                f"expected exactly one early_persist_terminated log record, got {len(log_capture.records)}"
            )
            persist_record = log_capture.records[0]
            assert getattr(persist_record, "regen", None) is False
            assert getattr(persist_record, "variant_index", None) == 0

            persisted_a = await wait_for_message_persisted(
                db, conversation_id=ctx["conv_id"], role="assistant", timeout_seconds=5.0
            )
            assert persisted_a is not None
            assert (persisted_a.message_metadata or {}).get("stream_state") == "user_terminated"

            # Stream B — quick completion, no delay knob. Sends a
            # separate user message and lets the assistant finalize
            # naturally (stream_state=complete).
            fixture_b = anthropic_message_start_then_delta_fixture(
                input_tokens=3,
                output_tokens=5,
                text_content="Hi",
                delay_before_message_delta_s=0.0,
            )
            with stub_provider_stream(fixture_b):
                await _send_message_to_completion(
                    client,
                    conv_id=ctx["conv_id"],
                    user_headers=ctx["user_headers"],
                    message_text="Hello",
                )

            # Wait for B's assistant to land — `wait_for_message_persisted`
            # with min_count=2 keeps polling until two assistant rows
            # have committed (or the budget expires with a partial
            # result that we'll catch in the length assertion below).
            await wait_for_message_persisted(
                db, conversation_id=ctx["conv_id"], role="assistant", min_count=2, timeout_seconds=5.0
            )

            messages = await self._list_messages_chronological(db, ctx["conv_id"])
            # Expect exactly 4 rows: 2 user, 2 assistant, alternating.
            assert len(messages) == 4, (
                f"expected 4 messages (user_A, assistant_A, user_B, assistant_B), got {len(messages)}: "
                + ", ".join(f"{m.role}@{m.created_at}" for m in messages)
            )
            assert messages[0].role == "user"
            assert messages[1].role == "assistant"
            assert messages[2].role == "user"
            assert messages[3].role == "assistant"

            # Stream-state ordering — A is terminated, B is complete.
            assert (messages[1].message_metadata or {}).get("stream_state") == "user_terminated"
            assert (messages[3].message_metadata or {}).get("stream_state") == "complete"

            # The race fix — A's assistant was committed at signal time,
            # NOT at drain finish, so it sorts BEFORE B's user message.
            assert messages[1].created_at < messages[2].created_at, (
                f"assistant_A.created_at ({messages[1].created_at}) should be < "
                f"user_B.created_at ({messages[2].created_at}); the chronological-ordering race is back"
            )
        finally:
            await cleanup_test_user(client, auth_headers, ctx["user_id"])
            await _cleanup_provider_resources(
                client,
                auth_headers,
                model_config_id=ctx["model_config_id"],
                provider_id=ctx["provider_id"],
            )

    # ------------------------------------------------------------------
    # 2. Regen + terminate — the Plan agent's option (B) load-bearing test
    # ------------------------------------------------------------------
    async def test_terminate_during_regenerate_assigns_correct_variant_index(self, client, db, auth_headers):
        """The whole point of the Plan agent's option (B refined) was
        to make regen+terminate work — the regen-aware INSERT block
        (sibling-max variant_index, regenerated metadata stamp,
        IntegrityError retry) was lifted into the early-persist
        callback rather than duplicated as an extracted helper. This
        test exercises that lift end-to-end:

        1. Send an initial user message, let the assistant complete
           naturally → variant_index=0, stream_state=complete.
        2. POST regenerate on that assistant message, terminate
           concurrently → the early-persist callback fires INSIDE the
           regen branch, computes variant_index from sibling-max
           (==1), stamps regenerated=True + regenerated_from_message_id.
        3. Assert the new variant landed with the correct lineage.
        """
        ctx = await self._setup(client, db, auth_headers)
        try:
            # 1. Send initial message — natural completion.
            with stub_provider_stream(
                anthropic_message_start_then_delta_fixture(
                    input_tokens=4,
                    output_tokens=6,
                    text_content="original answer",
                    delay_before_message_delta_s=0.0,
                )
            ):
                await _send_message_to_completion(
                    client,
                    conv_id=ctx["conv_id"],
                    user_headers=ctx["user_headers"],
                    message_text="What is 2+2?",
                )

            # Locate the original assistant message via the messages list.
            list_resp = await client.get(
                f"/api/v1/chat/conversations/{ctx['conv_id']}/messages",
                headers=ctx["user_headers"],
            )
            assert list_resp.status_code == 200
            messages_payload = extract_data(list_resp)
            # The list endpoint may use different ordering conventions;
            # find the assistant row by role + variant_index==0.
            assistant_rows = [m for m in messages_payload if m["role"] == "assistant"]
            assert len(assistant_rows) == 1, f"expected 1 assistant row after natural completion, got {len(assistant_rows)}"
            original_assistant_id = assistant_rows[0]["id"]
            original_parent = assistant_rows[0].get("parent_message_id") or assistant_rows[0]["id"]
            assert assistant_rows[0].get("variant_index") in (0, None)

            # 2. Regenerate + terminate.
            fixture_regen = anthropic_message_start_then_delta_fixture(
                input_tokens=4,
                output_tokens=99,
                text_content="regen partial answer",
                delay_before_message_delta_s=0.3,
            )
            with _EarlyPersistLogCapture() as log_capture, stub_provider_stream(fixture_regen):
                stream_id, terminate_resp = await _capture_regen_stream_id_and_terminate(
                    client,
                    target_assistant_message_id=original_assistant_id,
                    conv_id=ctx["conv_id"],
                    user_headers=ctx["user_headers"],
                )
            assert stream_id is not None
            assert terminate_resp is not None
            assert terminate_resp.status_code == 202

            # Load-bearing: the early-persist callback fired in REGEN
            # mode. Pre-fix, the callback didn't run at all for the
            # regen path — the Plan agent's option (B) lift is what
            # this assertion proves.
            assert len(log_capture.records) == 1
            persist_record = log_capture.records[0]
            assert getattr(persist_record, "regen", None) is True
            # Sibling-max → 1 (one existing sibling at index 0).
            assert getattr(persist_record, "variant_index", None) == 1

            # 3. Verify the new variant landed in DB with the correct
            # lineage. Two assistant rows total, one terminated.
            await wait_for_message_persisted(
                db, conversation_id=ctx["conv_id"], role="assistant", min_count=2, timeout_seconds=5.0
            )
            assistants = [m for m in await self._list_messages_chronological(db, ctx["conv_id"]) if m.role == "assistant"]
            assert len(assistants) == 2, (
                f"expected 2 assistant rows (original + regen variant), got {len(assistants)}"
            )

            # Find the regen variant.
            regen_variant = next(
                (m for m in assistants if (m.message_metadata or {}).get("stream_state") == "user_terminated"),
                None,
            )
            original_row = next(
                (m for m in assistants if (m.message_metadata or {}).get("stream_state") == "complete"),
                None,
            )
            assert regen_variant is not None, "regen variant not persisted with user_terminated state"
            assert original_row is not None

            # Lineage assertions — the regen-aware INSERT block from
            # `_finalize_variant_phase` is what we lifted, so these
            # are the same invariants the legacy regen path enforced:
            assert regen_variant.parent_message_id == original_parent
            assert regen_variant.variant_index == 1
            regen_meta = regen_variant.message_metadata or {}
            assert regen_meta.get("regenerated") is True
            assert regen_meta.get("regenerated_from_message_id") == original_assistant_id
        finally:
            await cleanup_test_user(client, auth_headers, ctx["user_id"])
            await _cleanup_provider_resources(
                client,
                auth_headers,
                model_config_id=ctx["model_config_id"],
                provider_id=ctx["provider_id"],
            )

    # ------------------------------------------------------------------
    # 3. Natural completion — no early-persist path taken
    # ------------------------------------------------------------------
    async def test_natural_completion_does_not_take_early_persist_path(self, client, db, auth_headers):
        """Sanity / no-regression. A chat that completes naturally
        (no terminate signal) MUST NOT trigger the early-persist
        callback. The Message row is committed once, via the legacy
        INSERT path in `_finalize_variant_phase`, with
        ``stream_state="complete"``.

        Failure mode this guards against: a future refactor that
        accidentally invokes the early-persist callback on every
        terminate-detection check, including the no-signal case. The
        log-record assertion catches that regression before any
        user-visible symptom.
        """
        ctx = await self._setup(client, db, auth_headers)
        try:
            with _EarlyPersistLogCapture() as log_capture, stub_provider_stream(
                anthropic_message_start_then_delta_fixture(
                    input_tokens=2,
                    output_tokens=3,
                    text_content="ok",
                    delay_before_message_delta_s=0.0,
                )
            ):
                await _send_message_to_completion(
                    client,
                    conv_id=ctx["conv_id"],
                    user_headers=ctx["user_headers"],
                    message_text="Quick test",
                )

            # Load-bearing — no early-persist record fired.
            assert len(log_capture.records) == 0, (
                f"early-persist callback fired for a natural-completion stream; "
                f"got {len(log_capture.records)} record(s)"
            )

            persisted = await wait_for_message_persisted(
                db, conversation_id=ctx["conv_id"], role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "complete"
            assert (persisted.message_metadata or {}).get("partial_usage_unavailable") is None
        finally:
            await cleanup_test_user(client, auth_headers, ctx["user_id"])
            await _cleanup_provider_resources(
                client,
                auth_headers,
                model_config_id=ctx["model_config_id"],
                provider_id=ctx["provider_id"],
            )

    # ------------------------------------------------------------------
    # 4. Silent-provider gap — Codex review follow-up
    # ------------------------------------------------------------------
    async def test_terminate_during_silent_provider_gap_fires_early_persist_immediately(
        self, client, db, auth_headers
    ):
        """End-to-end coverage for ``_iter_with_signal_break``.

        Codex code review (May 2026) flagged that the base SHU-803
        early-persist callback was chunk-gated: the consumer loop's
        terminate-detection check only ran between provider chunks,
        so a silent provider after Stop delayed the partial-row commit
        until the next chunk arrived. In that gap a refetch returned
        no row and a follow-up message landed first — the same
        races early-persist was supposed to close.

        The fix wraps the provider stream in ``_iter_with_signal_break``,
        which yields a sentinel the moment ``lifecycle.event`` fires
        even if no chunk has arrived. This test exercises that path
        live: the stub fixture sits silent for 2.5 seconds between
        ``message_start`` and the first content delta, terminate fires
        ~50ms into that gap, and we assert the early-persist log lands
        well before the gap would have ended on its own.

        Without the wrapper, the log lands at ~2.5s elapsed
        (the next-chunk arrival). With the wrapper, it lands at
        ~100ms elapsed (terminate POST latency + asyncio scheduling).
        We pick a 1.0s threshold — well clear of both the
        with-wrapper expected timing AND the without-wrapper failure
        timing — so the assertion is decisive in either direction.
        """
        ctx = await self._setup(client, db, auth_headers)
        try:
            silent_gap_seconds = 2.5
            fixture = anthropic_pre_delta_fixture(
                input_tokens=11,
                delay_before_first_content_s=silent_gap_seconds,
            )

            # Use the same wall clock that `logging.LogRecord.created`
            # uses, so the elapsed-time math below is apples-to-apples.
            import time

            test_start_wall = time.time()
            with _EarlyPersistLogCapture() as log_capture, stub_provider_stream(fixture):
                stream_id, terminate_resp = await _capture_stream_id_and_terminate(
                    client, conv_id=ctx["conv_id"], user_headers=ctx["user_headers"]
                )
            assert stream_id is not None
            assert terminate_resp is not None
            assert terminate_resp.status_code == 202

            # Exactly one early-persist record (no double-fire).
            assert len(log_capture.records) == 1, (
                f"expected exactly one early_persist_terminated record, got {len(log_capture.records)}"
            )
            record = log_capture.records[0]

            # Load-bearing: the record landed well before the silent
            # gap would have ended naturally.
            wall_elapsed_at_persist = record.created - test_start_wall
            assert wall_elapsed_at_persist < (silent_gap_seconds * 0.5), (
                f"early-persist landed {wall_elapsed_at_persist:.3f}s into the {silent_gap_seconds}s silent gap; "
                f"expected sub-{silent_gap_seconds * 0.5:.1f}s. Without the wrapper, the chunk-gated check "
                f"would have delayed this to ~{silent_gap_seconds:.1f}s — the regression Codex flagged."
            )

            # Sanity: the persisted row landed with the expected state.
            persisted = await wait_for_message_persisted(
                db, conversation_id=ctx["conv_id"], role="assistant", timeout_seconds=5.0
            )
            assert persisted is not None
            assert (persisted.message_metadata or {}).get("stream_state") == "user_terminated"
        finally:
            await cleanup_test_user(client, auth_headers, ctx["user_id"])
            await _cleanup_provider_resources(
                client,
                auth_headers,
                model_config_id=ctx["model_config_id"],
                provider_id=ctx["provider_id"],
            )


if __name__ == "__main__":
    ChatEarlyPersistIntegrationTest().run()
