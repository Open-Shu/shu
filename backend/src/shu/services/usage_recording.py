"""Service for recording LLM/API usage in llm_usage.

Exposes two classes and a singleton accessor:

- ``CostResolver`` — applies the two-tier cost contract. Pure, no I/O.
- ``UsageRecorder`` — coordinates cost resolution, snapshot capture, and the
  llm_usage INSERT. Composes a ``CostResolver``; swallows failures so callers
  are never broken by a missing billing row.
- ``get_usage_recorder()`` — module-level singleton. Follows the same
  ``get_X()`` pattern used by ``get_billing_settings``, ``get_async_session_local``,
  and the other service accessors in this codebase.

Cost-resolution contract (SHU-715): every write path goes through the same
two-tier rule.

1. **Provider-authoritative** — caller passes ``total_cost > 0``. The value
   is recorded verbatim; ``input_cost`` and ``output_cost`` stay at
   ``Decimal(0)`` because providers return a single total, not a split.
   Hot path for OpenRouter (returns ``usage.cost`` on the wire).

2. **DB-rate fallback** — caller passes ``total_cost = Decimal(0)`` (the
   "no wire cost" sentinel). The resolver reads ``cost_per_input_unit`` /
   ``cost_per_output_unit`` from the model row and computes
   ``input_cost = input_tokens * input_rate``,
   ``output_cost = output_tokens * output_rate``,
   ``total_cost = input_cost + output_cost``. On this path
   ``input_cost + output_cost == total_cost`` holds.

When neither path produces a cost (no wire cost, no DB rates — e.g. a
local/self-hosted model), all three cost columns land as ``Decimal(0)``.

Unit disambiguation is implicit: ``llm_models.model_type`` determines
whether the rate columns carry per-token (chat, embedding) or per-page
(OCR) pricing — same semantic as the column rename in SHU-700. OCR
callers pass ``input_tokens=page_count, output_tokens=0``.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from ..core.database import get_async_session_local
from ..core.logging import get_logger
from ..models.llm_provider import LLMModel, LLMProvider, LLMUsage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class CostResolver:
    """Applies the two-tier cost contract. Pure function dressed as a class
    so ``UsageRecorder`` can compose a replaceable strategy — tests inject
    a fake resolver to drive edge cases without touching the DB.
    """

    def resolve(
        self,
        *,
        model: LLMModel | None,
        input_tokens: int,
        output_tokens: int,
        input_cost: Decimal,
        output_cost: Decimal,
        total_cost: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return (input_cost, output_cost, total_cost) after applying the contract.

        See the module docstring for the full rule. Provider-authoritative
        rows short-circuit with ``total_cost`` untouched; fallback rows
        compute the two sides from the model rates and sum them into
        total. Unresolved / unpriced models return the inputs unchanged
        (callers default input_cost / output_cost to Decimal(0)).
        """
        if total_cost > Decimal("0"):
            # Provider-authoritative — record total verbatim and force the
            # split to zero. The module invariant says input_cost == 0 and
            # output_cost == 0 on this path; don't trust caller-supplied
            # splits (no current caller passes them, but the invariant is
            # what aggregators key off to distinguish this tier).
            return Decimal("0"), Decimal("0"), total_cost

        if model is None:
            return input_cost, output_cost, total_cost

        # Use `is not None` (not truthiness) so a legitimate Decimal(0) rate
        # on one side — e.g. a free-output model — doesn't collapse the
        # fallback and silently lose the other side's cost.
        if model.cost_per_input_unit is None and model.cost_per_output_unit is None:
            return input_cost, output_cost, total_cost

        input_rate = model.cost_per_input_unit if model.cost_per_input_unit is not None else Decimal(0)
        output_rate = model.cost_per_output_unit if model.cost_per_output_unit is not None else Decimal(0)
        resolved_input_cost = Decimal(str(input_tokens)) * input_rate
        resolved_output_cost = Decimal(str(output_tokens)) * output_rate
        return resolved_input_cost, resolved_output_cost, resolved_input_cost + resolved_output_cost


class UsageRecorder:
    """Records LLM/API usage in llm_usage. Composes a ``CostResolver``.

    Failure semantics depend on the caller's transaction shape (see
    ``record``): fire-and-forget when no session is passed, propagate when
    one is — so callers composing our write into a unit of work can roll
    back atomically.
    """

    def __init__(self, cost_resolver: CostResolver | None = None) -> None:
        self._cost_resolver = cost_resolver or CostResolver()

    async def record(
        self,
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
        response_time_ms: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        request_metadata: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert a single LLMUsage row.

        Applies the two-tier cost contract via ``CostResolver`` and
        snapshots provider.name / model.model_name onto the row.

        Failure semantics:
        - ``session=None`` (fire-and-forget): a fresh session is opened
          and committed; any failure is logged but **not raised**, so
          legacy callers (external embedding, side-call service, etc.)
          stay decoupled from billing-record reliability.
        - ``session=<AsyncSession>`` (caller-owned transaction): the
          insert is flushed inside a nested savepoint on the caller's
          session and any failure is **propagated**, so the caller can
          roll back atomically. SHU-759 chat finalize and OCR finalize
          rely on this to keep Message + LLMUsage co-atomic; silently
          swallowing here would commit the caller's other writes while
          rolling back ours.

        ``user_id`` should be populated wherever the originating user is
        identifiable (chat conversation owner, ingestion job user, etc.) so
        billing can attribute usage per user. It is nullable only for
        genuinely user-less surfaces (none exist today).
        """
        if session is not None:
            # Caller-owned transaction path (SHU-759 chat finalize, OCR
            # finalize, etc.). The caller composed our write into a unit of
            # work and will commit it as part of that unit. Failures here
            # MUST propagate — silently swallowing would commit the
            # caller's other writes while rolling back ours via the nested
            # savepoint, violating the atomicity contract those call sites
            # depend on (AC#3 in SHU-759). Callers that want fire-and-forget
            # semantics should omit `session=` and use the fresh-session
            # branch below.
            await self._insert(
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
                response_time_ms=response_time_ms,
                success=success,
                error_message=error_message,
                request_metadata=request_metadata,
            )
            return

        # Fresh-session path: legacy fire-and-forget contract used by
        # external embeddings, side-call service, etc. Billing failures
        # here must never crash the caller; log the full payload so the
        # row can be reconstructed from logs.
        try:
            session_factory = get_async_session_local()
            async with session_factory() as new_session:
                await self._insert(
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
                    response_time_ms=response_time_ms,
                    success=success,
                    error_message=error_message,
                    request_metadata=request_metadata,
                )
        except Exception as e:
            # If we hit this we are in trouble — caller loses a billing row.
            # Log the raw payload plus traceback so the failure can be
            # reconstructed from logs even without the original DB write.
            logger.error(
                "Failed to record usage: %s - %s",
                request_type,
                e,
                exc_info=True,
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
                    # Upstream call outcome — lets ops correlate a dropped
                    # billing row with whether the LLM call itself succeeded
                    # or was already an error case.
                    "success": success,
                    "error_message": error_message,
                },
            )

    async def _insert(
        self,
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
        response_time_ms: int | None,
        success: bool,
        error_message: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        """Resolve provider/model, apply the cost contract, insert the row.

        One ``session.get`` pair serves both snapshot-name capture (SHU-727)
        and rate lookup for the DB-rate fallback (SHU-715). ``commit=True``
        commits the session (fresh-session path); ``commit=False`` flushes
        inside a nested savepoint (caller-owned session).
        """
        provider = await session.get(LLMProvider, provider_id)
        model = await session.get(LLMModel, model_id)

        input_cost, output_cost, total_cost = self._cost_resolver.resolve(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )

        # Derive total_tokens when the caller didn't supply it. Old
        # LLMService.record_usage did this automatically; dropping the
        # convenience silently zeroed the column for chat / side_call /
        # OCR rows whose callers don't report a tokenizer-specific total.
        # Caller-supplied non-zero values still win.
        if not total_tokens:
            total_tokens = input_tokens + output_tokens

        record = LLMUsage(
            provider_id=provider_id,
            model_id=model_id,
            provider_name=provider.name if provider else None,
            model_name=model.model_name if model else None,
            user_id=user_id,
            request_type=request_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            response_time_ms=response_time_ms,
            success=success,
            error_message=error_message,
            request_metadata=request_metadata,
        )
        if commit:
            session.add(record)
            await session.commit()
        else:
            async with session.begin_nested():
                session.add(record)
                await session.flush()


@lru_cache
def get_usage_recorder() -> UsageRecorder:
    """Return the singleton UsageRecorder instance.

    Matches the ``get_billing_settings`` / ``get_async_session_local`` pattern
    elsewhere in the codebase. Tests can replace the instance per-call by
    patching this function at the caller's binding, or bypass the singleton
    entirely by constructing ``UsageRecorder(cost_resolver=FakeResolver())``
    directly.
    """
    return UsageRecorder()
