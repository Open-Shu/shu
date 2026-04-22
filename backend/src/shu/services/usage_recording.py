"""Shared helper for recording LLM/API usage in llm_usage.

Best-effort: failures are logged, never raised, so callers don't
break when the DB is temporarily unreachable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..core.database import get_async_session_local
from ..core.logging import get_logger
from ..models.llm_provider import LLMModel, LLMProvider, LLMUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def _snapshot_names(
    session: AsyncSession,
    provider_id: str,
    model_id: str,
) -> tuple[str | None, str | None]:
    """Look up provider.name and model.model_name for the snapshot columns.

    Returns (None, None) for any id that doesn't resolve — the row still
    inserts with the FK ids; the snapshot is best-effort audit context.
    """
    provider = await session.get(LLMProvider, provider_id)
    model = await session.get(LLMModel, model_id)
    return (
        provider.name if provider else None,
        model.model_name if model else None,
    )


async def _insert_with_snapshot(
    session: AsyncSession,
    *,
    commit: bool,
    provider_id: str,
    model_id: str,
    user_id: str | None,
    request_type: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    input_cost: Decimal,
    output_cost: Decimal,
    total_cost: Decimal,
    success: bool,
    request_metadata: dict[str, Any] | None,
) -> None:
    """Snapshot provider/model names, build the LLMUsage row, and insert it.

    `commit=True` commits the session (fresh-session path); `commit=False`
    flushes inside a nested savepoint (caller-owned session).
    """
    provider_name, model_name = await _snapshot_names(session, provider_id, model_id)
    record = LLMUsage(
        provider_id=provider_id,
        model_id=model_id,
        provider_name=provider_name,
        model_name=model_name,
        user_id=user_id,
        request_type=request_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        success=success,
        request_metadata=request_metadata,
    )
    if commit:
        session.add(record)
        await session.commit()
    else:
        async with session.begin_nested():
            session.add(record)
            await session.flush()


async def record_llm_usage(
    *,
    provider_id: str,
    model_id: str,
    request_type: str,
    user_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    input_cost: Decimal = Decimal("0"),
    output_cost: Decimal = Decimal("0"),
    total_cost: Decimal = Decimal("0"),
    success: bool = True,
    request_metadata: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Insert a single LLMUsage row. Swallows all exceptions.

    Pass an existing `session` to reuse a connection already checked out
    (e.g. when the caller ran a query in the same unit of work).
    When omitted, a fresh session is created and committed automatically.

    `user_id` should be populated wherever the originating user is
    identifiable (chat conversation owner, ingestion job user, etc.) so
    billing can attribute usage per user. It is nullable only for genuinely
    user-less surfaces (none exist today).
    """
    try:
        if session is not None:
            await _insert_with_snapshot(
                session,
                commit=False,
                provider_id=provider_id,
                model_id=model_id,
                user_id=user_id,
                request_type=request_type,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                success=success,
                request_metadata=request_metadata,
            )
        else:
            session_factory = get_async_session_local()
            async with session_factory() as new_session:
                await _insert_with_snapshot(
                    new_session,
                    commit=True,
                    provider_id=provider_id,
                    model_id=model_id,
                    user_id=user_id,
                    request_type=request_type,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    total_cost=total_cost,
                    success=success,
                    request_metadata=request_metadata,
                )
    except Exception as e:
        # If we hit this we are in trouble, we'll probably want to try to aggregate this in whatever the hosting's CloudWatch equivalent is
        logger.error(
            "Failed to record usage: %s - %s",
            request_type,
            e,
            extra={
                "provider_id": provider_id,
                "model_id": model_id,
                "user_id": user_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "input_cost": str(input_cost),
                "output_cost": str(output_cost),
                "total_cost": str(total_cost),
            },
        )
