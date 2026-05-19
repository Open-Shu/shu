"""Poll for a persisted Message row (SHU-802).

After SHU-802 detached the chat variant tasks from the SSE generator
lifecycle, there's a small race window between the SSE response closing
(client sees ``[DONE]``) and the variant's shielded finalize transaction
committing. In practice the window is sub-200ms on the happy path because
the variant emits ``final_message`` AFTER the commit — but tests that
disconnect mid-stream and want to assert "the row landed anyway" need
a bounded retry to ride out the timing.

The helper polls for an assistant Message row matching the given filters,
re-querying every ``interval_seconds`` until either the row appears or
``timeout_seconds`` elapses. Use it in integration tests that:

1. Disconnect from the SSE stream before ``[DONE]`` (the new disconnect
   tests in [test_chat_disconnect_persistence_integration.py] do this
   via ``httpx.stream + break``); OR
2. Query the database immediately after a chat ``POST /send`` returns and
   want to be tolerant of finalize-after-response timing.

Existing chat integration tests pre-SHU-802 read the row synchronously
after the SSE response closes, which still works on the happy path —
finalize commits BEFORE emitting ``final_message``, and ``[DONE]`` only
lands after the consumer sees ``final_message``. So the helper is for
the new disconnect-test surface, not a blanket migration. Per AC16: only
tests that fail due to timing change need to adopt it.

Usage::

    from integ.helpers.wait_for_message import wait_for_message_persisted

    message_row = await wait_for_message_persisted(
        db, conversation_id=conv_id, role="assistant", timeout_seconds=2.0
    )
    assert message_row is not None, "finalize did not persist within budget"
    msg_id, content, metadata = message_row.id, message_row.content, message_row.message_metadata
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PersistedMessage:
    """Minimal projection of a Message row used by the disconnect-survival tests.

    Only the columns the tests assert on. Extending this shape later is
    a single-call-site change (the SELECT below). Kept as a dataclass
    rather than a Row tuple so test code can read ``msg.content`` /
    ``msg.message_metadata`` instead of positional indexing.
    """

    id: str
    role: str
    content: str
    model_id: str | None
    message_metadata: dict[str, Any] | None
    parent_message_id: str | None
    variant_index: int | None


async def wait_for_message_persisted(
    db: AsyncSession,
    *,
    conversation_id: str,
    role: str = "assistant",
    min_count: int = 1,
    timeout_seconds: float = 2.0,
    interval_seconds: float = 0.05,
) -> PersistedMessage | None:
    """Poll for an assistant Message row, returning the most recent on success.

    Return contract:

    - Returns the latest matching :class:`PersistedMessage` if at least one
      row exists for ``(conversation_id, role)`` at any point during the
      poll budget.
    - Returns ``None`` only if **zero** matching rows ever appeared within
      ``timeout_seconds``.
    - When ``min_count > 1`` and the budget expires with at least one but
      fewer than ``min_count`` rows persisted, the latest row is still
      returned — i.e. a **partial** result, NOT ``None``. The caller is
      responsible for verifying the row's identity / count if a strict
      threshold matters (the regenerate disconnect test does this by
      asserting ``persisted.id != target_message_id``, which catches
      "regen didn't land" with a more informative failure than a bare
      ``None``).

    On each poll iteration the test session is rolled back to release any
    read view so the next query sees committed-elsewhere rows.

    Args:
        db: Test ``AsyncSession``.
        conversation_id: Conversation to filter on.
        role: Message role (defaults to ``"assistant"`` — the SHU-802
            disconnect tests target the AI side).
        min_count: Early-exit threshold. When the number of matching rows
            reaches this value, returns immediately. On timeout with a
            partial match (one or more rows but fewer than ``min_count``),
            returns the latest row — see return contract above.
        timeout_seconds: Bounded budget. The happy path is sub-200ms;
            2.0s is comfortable headroom that still fails fast if the
            row genuinely doesn't land.
        interval_seconds: Poll cadence. 50ms is short enough that the
            test doesn't wait long after the row commits, while still
            cheap on the DB (a tight indexed query against the test
            conversation).

    """
    if timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_row: PersistedMessage | None = None
    while True:
        # Roll back the session view before querying. SQLAlchemy keeps an
        # implicit transaction open across queries; without the rollback,
        # a row committed by the in-flight finalize task on a different
        # connection won't be visible until we explicitly refresh the
        # snapshot. (Test framework sessions are configured with
        # `expire_on_commit=False` so the rollback only drops the read
        # view — no in-memory ORM state is affected.)
        await db.rollback()
        # Latest row AND total count from a single query via a window
        # function. `COUNT(*) OVER ()` computes the unfiltered total
        # before the LIMIT 1 is applied, so the row and the count come
        # from the same atomic snapshot — no race window between a
        # separate SELECT-LIMIT-1 and SELECT-COUNT where another finalize
        # could commit and make the returned row stale relative to the
        # threshold that triggered the return.
        result = await db.execute(
            text(
                "SELECT id, role, content, model_id, message_metadata, "
                "parent_message_id, variant_index, "
                "COUNT(*) OVER () AS total_count "
                "FROM messages "
                "WHERE conversation_id = :conv_id AND role = :role "
                "ORDER BY created_at DESC "
                "LIMIT 1"
            ),
            {"conv_id": conversation_id, "role": role},
        )
        row = result.first()
        if row is not None:
            last_row = PersistedMessage(
                id=row[0],
                role=row[1],
                content=row[2],
                model_id=row[3],
                message_metadata=row[4],
                parent_message_id=row[5],
                variant_index=row[6],
            )
            count = row[7]
            # Single-variant fast path — return as soon as the row exists.
            if min_count <= 1:
                return last_row
            # Ensemble path — return when the snapshot's count meets the
            # threshold (row and count are from the same query, no race).
            if count >= min_count:
                return last_row

        if asyncio.get_running_loop().time() >= deadline:
            logger.warning(
                "wait_for_message_persisted: timeout after %.3fs (conversation_id=%s, role=%s, min_count=%d)",
                timeout_seconds,
                conversation_id,
                role,
                min_count,
            )
            return last_row
        await asyncio.sleep(interval_seconds)
