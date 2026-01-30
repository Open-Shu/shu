"""Database-backed conversation locking utilities.

Locks are stored in Conversation.meta so they persist across workers/devices.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ConversationNotFoundError, ShuException
from ..models.llm_provider import Conversation

LOCK_META_KEY = "ensemble_lock"
LOCK_TTL_SECONDS = 60  # seconds


async def acquire_conversation_lock(
    db_session: AsyncSession,
    conversation_id: str,
    lock_id: str,
    owner_user_id: str | None = None,
) -> None:
    """Attempt to acquire a conversation lock; raise if another task holds it."""
    manage_tx = not db_session.in_transaction()
    if manage_tx:
        async with db_session.begin():
            convo = await _get_conversation_for_update(db_session, conversation_id)
            _set_lock_or_raise(convo, lock_id, owner_user_id)
    else:
        convo = await _get_conversation_for_update(db_session, conversation_id)
        _set_lock_or_raise(convo, lock_id, owner_user_id)
        await db_session.flush()


async def release_conversation_lock(
    db_session: AsyncSession,
    conversation_id: str,
    lock_id: str,
) -> None:
    """Release the lock when owned by the caller."""
    manage_tx = not db_session.in_transaction()
    if manage_tx:
        async with db_session.begin():
            convo = await _get_conversation_for_update(db_session, conversation_id, allow_missing=True)
            if convo:
                _clear_lock_if_owned(convo, lock_id)
    else:
        convo = await _get_conversation_for_update(db_session, conversation_id, allow_missing=True)
        if convo:
            _clear_lock_if_owned(convo, lock_id)
            await db_session.flush()


async def _get_conversation_for_update(
    db_session: AsyncSession,
    conversation_id: str,
    allow_missing: bool = False,
) -> Conversation | None:
    stmt = select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    result = await db_session.execute(stmt)
    convo = result.scalar_one_or_none()
    if not convo and not allow_missing:
        raise ConversationNotFoundError(f"Conversation with ID '{conversation_id}' not found")
    return convo


def _set_lock_or_raise(convo: Conversation, lock_id: str, owner_user_id: str | None) -> None:
    meta = dict(convo.meta or {})
    existing = meta.get(LOCK_META_KEY)
    now_ts = datetime.now(UTC)

    if isinstance(existing, dict):
        acquired_raw = existing.get("acquired_at")
        try:
            acquired_ts = datetime.fromisoformat(acquired_raw) if acquired_raw else None
        except Exception:
            acquired_ts = None

        if acquired_ts and now_ts - acquired_ts < timedelta(seconds=LOCK_TTL_SECONDS):
            raise ShuException(
                "Conversation is currently processing another request.",
                "CONVERSATION_LOCKED",
                status_code=423,
            )

    meta[LOCK_META_KEY] = {
        "lock_id": lock_id,
        "owner_user_id": owner_user_id,
        "acquired_at": now_ts.isoformat(),
    }
    convo.meta = meta


def _clear_lock_if_owned(convo: Conversation, lock_id: str) -> None:
    meta = dict(convo.meta or {})
    existing = meta.get(LOCK_META_KEY)
    if isinstance(existing, dict) and existing.get("lock_id") == lock_id:
        meta.pop(LOCK_META_KEY, None)
        convo.meta = meta if meta else None
