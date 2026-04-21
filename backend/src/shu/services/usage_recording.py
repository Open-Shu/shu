"""Shared helper for recording LLM/API usage in llm_usage.

Best-effort: failures are logged, never raised, so callers don't
break when the DB is temporarily unreachable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..core.database import get_async_session_local
from ..core.logging import get_logger
from ..models.llm_provider import LLMUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


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
        record = LLMUsage(
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

        if session is not None:
            async with session.begin_nested():
                session.add(record)
                await session.flush()
        else:
            session_factory = get_async_session_local()
            async with session_factory() as new_session:
                new_session.add(record)
                await new_session.commit()
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
