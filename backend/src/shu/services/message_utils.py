"""Shared helper utilities for message preprocessing."""

from datetime import UTC, datetime
from typing import Any

from shu.models.llm_provider import Message


def collapse_assistant_variants(
    messages: list[Message],
    start_message_id: str | None = None,
) -> list[Message]:
    """Return history with only the latest assistant variant per turn.

    Args:
        messages: Conversation message list in chronological order.
        start_message_id: Optional message id indicating where to begin collapsing.
            When provided, all messages before and up to this id are ignored; collapsing begins
            after the matching message (exclusive).

    """
    if not messages:
        return messages

    latest_by_root: dict[str, Message] = {}

    def _variant_rank(message: Message) -> tuple[int, float]:
        idx = getattr(message, "variant_index", None)
        created_at = getattr(message, "created_at", None)
        idx_rank = idx if isinstance(idx, int) else -1
        ts = created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0
        return (idx_rank, ts)

    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        root_id = getattr(msg, "parent_message_id", None) or msg.id
        current_best = latest_by_root.get(root_id)
        if not current_best:
            latest_by_root[root_id] = msg
            continue
        best_rank = _variant_rank(current_best)
        msg_rank = _variant_rank(msg)
        if msg_rank >= best_rank:
            latest_by_root[root_id] = msg

    collapsed: list[Message] = []
    emitted_roots: set[str] = set()

    started = start_message_id is None
    if not started:
        # Gracefully fall back when the marker is no longer present, because of context size pruning
        marker_exists = any(getattr(msg, "id", None) == start_message_id for msg in messages)
        if not marker_exists:
            started = True

    for msg in messages:
        # Find the first relevant message and build the returned message set from there
        if not started:
            if getattr(msg, "id", None) == start_message_id:
                started = True
            continue

        if getattr(msg, "role", None) != "assistant":
            collapsed.append(msg)
            continue

        root_id = getattr(msg, "parent_message_id", None) or msg.id
        if root_id in emitted_roots:
            continue

        best_variant = latest_by_root.get(root_id, msg)
        if best_variant.id != msg.id:
            continue

        collapsed.append(msg)
        emitted_roots.add(root_id)

    return collapsed


def serialize_message_for_sse(msg: Message) -> dict[str, Any]:
    """Serialize a Message ORM object to a JSON-serializable dict for SSE.

    Keeps shape aligned with MessageResponse used by REST responses, but returns
    plain dict with ISO datetimes and simplified attachment info.
    """

    def _iso(dt):
        try:
            return dt.isoformat() if dt else None
        except Exception:
            return None

    # Attachments (assistant messages typically don't have any, but support it)
    attachments = []
    try:
        now = datetime.now(UTC)
        for a in getattr(msg, "attachments", []) or []:
            exp = getattr(a, "expires_at", None)
            is_ocr = getattr(a, "extraction_method", None) == "ocr"
            attachments.append(
                {
                    "id": getattr(a, "id", None),
                    "original_filename": getattr(a, "original_filename", None),
                    "mime_type": getattr(a, "mime_type", None),
                    "file_size": getattr(a, "file_size", None),
                    "extracted_text_length": getattr(a, "extracted_text_length", None),
                    "is_ocr": is_ocr,
                    "expires_at": _iso(exp),
                    "expired": (exp is not None and exp <= now),
                }
            )
    except Exception:
        # Be tolerant of relationship loading issues during streaming
        attachments = []

    metadata = getattr(msg, "message_metadata", None) or {}

    return {
        "id": getattr(msg, "id", None),
        "conversation_id": getattr(msg, "conversation_id", None),
        "role": getattr(msg, "role", None),
        "content": getattr(msg, "content", None),
        "model_id": getattr(msg, "model_id", None),
        "message_metadata": metadata,
        # Model configuration snippet is carried in metadata for streaming cases
        "model_configuration": metadata.get("model_configuration") if isinstance(metadata, dict) else None,
        "created_at": _iso(getattr(msg, "created_at", None)),
        "updated_at": _iso(getattr(msg, "updated_at", None)),
        "parent_message_id": getattr(msg, "parent_message_id", None),
        "variant_index": getattr(msg, "variant_index", None),
        "attachments": attachments,
    }
