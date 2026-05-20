"""Tests for shu.services.usage_recording.

Covers:
- ``CostResolver`` — pure-function two-tier cost contract (SHU-715).
  Provider-authoritative when total_cost > 0, DB-rate fallback when
  total_cost == Decimal(0), all-zero when neither path produces a value.
- ``UsageRecorder`` — snapshot-column capture (SHU-727) and end-to-end
  row construction. Uses an injected ``FakeResolver`` to drive contract
  edge cases without depending on the real resolver's internals.

Deliberately not tested here (unit-scope):
- ON DELETE SET NULL cascade behaviour on the actual postgres table. That
  is enforced by the migration itself (008_0010) and the schema-level
  test that asserts the FK's ondelete posture; covering it in a Python
  unit test would require a real DB session and just re-prove framework
  behaviour.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.models.llm_provider import LLMModel, LLMProvider
from shu.services.usage_recording import CostResolver, UsageRecorder


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeModel:
    def __init__(
        self,
        model_name: str,
        *,
        cost_per_input_unit: Decimal | None = None,
        cost_per_output_unit: Decimal | None = None,
    ) -> None:
        self.model_name = model_name
        self.cost_per_input_unit = cost_per_input_unit
        self.cost_per_output_unit = cost_per_output_unit


def _make_session(
    *,
    provider: _FakeProvider | None,
    model: _FakeModel | None,
) -> MagicMock:
    """Build a MagicMock AsyncSession whose .get returns provider/model by type."""
    session = MagicMock()

    async def _get(cls, obj_id):  # noqa: ARG001 — mirrors session.get signature
        # Identity dispatch on the imported class — failing loud on an
        # unexpected class is better than silently returning None and
        # letting snapshot-field assertions read "None" without surfacing
        # the mis-dispatch.
        if cls is LLMProvider:
            return provider
        if cls is LLMModel:
            return model
        raise AssertionError(f"Unexpected session.get lookup for {cls!r}")

    session.get = AsyncMock(side_effect=_get)
    session.add = MagicMock()
    session.flush = AsyncMock()

    nested_cm = MagicMock()
    nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
    nested_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested_cm)

    return session


# ---------------------------------------------------------------------------
# CostResolver — pure contract, no session or I/O.
# ---------------------------------------------------------------------------


class TestCostResolver:
    """Two-tier cost resolution. Provider-authoritative wins when total_cost
    > 0; DB-rate fallback fires when total_cost == Decimal(0); unpriced
    models return inputs unchanged (caller defaults are zeros)."""

    def test_provider_authoritative_recorded_verbatim(self):
        """Wire cost lands on total_cost; input/output stay at 0 even when
        the model has rates."""
        resolver = CostResolver()
        model = _FakeModel(
            "openai/gpt-4o",
            cost_per_input_unit=Decimal("0.0000025"),
            cost_per_output_unit=Decimal("0.00001"),
        )

        ic, oc, tc = resolver.resolve(
            model=model,
            input_tokens=1000,
            output_tokens=500,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0.0125"),
        )

        assert tc == Decimal("0.0125")
        assert ic == Decimal("0")
        assert oc == Decimal("0")

    def test_db_rate_fallback_for_chat_splits_cost(self):
        """total_cost=Decimal(0) → multiply tokens × model rates."""
        resolver = CostResolver()
        model = _FakeModel(
            "openai/gpt-4o",
            cost_per_input_unit=Decimal("0.0000025"),
            cost_per_output_unit=Decimal("0.00001"),
        )

        ic, oc, tc = resolver.resolve(
            model=model,
            input_tokens=1000,
            output_tokens=500,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("0.0025")
        assert oc == Decimal("0.005")
        assert tc == Decimal("0.0075")
        # Fallback rows satisfy input + output == total.
        assert ic + oc == tc

    def test_db_rate_fallback_for_ocr_uses_per_page_rate(self):
        """OCR callers pass input_tokens=page_count; rate column is per-page."""
        resolver = CostResolver()
        model = _FakeModel(
            "mistral-ocr-latest",
            cost_per_input_unit=Decimal("0.001"),  # $0.001 per page
            cost_per_output_unit=None,
        )

        ic, oc, tc = resolver.resolve(
            model=model,
            input_tokens=42,  # pages
            output_tokens=0,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("0.042")
        assert oc == Decimal("0")
        assert tc == Decimal("0.042")

    def test_db_rate_fallback_for_embedding_when_wire_cost_missing(self):
        """Closes SHU-715's latent gap: a direct-API embedding model with DB
        rates but no wire ``usage.cost`` now falls back to rate math instead
        of silently recording $0."""
        resolver = CostResolver()
        model = _FakeModel(
            "text-embedding-3-large",
            cost_per_input_unit=Decimal("0.00013"),
            cost_per_output_unit=None,
        )

        ic, _, tc = resolver.resolve(
            model=model,
            input_tokens=1000,
            output_tokens=0,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("0.13")
        assert tc == Decimal("0.13")

    def test_free_output_side_does_not_collapse_input_cost(self):
        """Regression guard for the SHU-700 bug: a legitimate Decimal(0) rate
        on one side must not null out the other side. Uses ``is not None``
        per-side guards, not truthy checks."""
        resolver = CostResolver()
        model = _FakeModel(
            "example/free-output",
            cost_per_input_unit=Decimal("0.01"),
            cost_per_output_unit=Decimal("0"),  # legitimate free output
        )

        ic, oc, tc = resolver.resolve(
            model=model,
            input_tokens=100,
            output_tokens=200,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("1.00")
        assert oc == Decimal("0")
        assert tc == Decimal("1.00")

    def test_no_rates_returns_inputs_unchanged(self):
        """Local/self-hosted model with no rates: returns caller's defaults."""
        resolver = CostResolver()
        model = _FakeModel(
            "local/llama",
            cost_per_input_unit=None,
            cost_per_output_unit=None,
        )

        ic, oc, tc = resolver.resolve(
            model=model,
            input_tokens=1000,
            output_tokens=500,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("0")
        assert oc == Decimal("0")
        assert tc == Decimal("0")

    def test_missing_model_returns_inputs_unchanged(self):
        """Unresolved model (FK target deleted): returns caller's defaults."""
        resolver = CostResolver()

        ic, oc, tc = resolver.resolve(
            model=None,
            input_tokens=1000,
            output_tokens=500,
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

        assert ic == Decimal("0")
        assert oc == Decimal("0")
        assert tc == Decimal("0")


# ---------------------------------------------------------------------------
# UsageRecorder — coordinates lookup, cost resolution, and INSERT.
# ---------------------------------------------------------------------------


class _FakeResolver(CostResolver):
    """Stand-in CostResolver that returns a fixed tuple and records call kwargs.

    Inherits from CostResolver so static type checks accept it where
    ``cost_resolver: CostResolver`` is declared.
    """

    def __init__(self, ret: tuple[Decimal, Decimal, Decimal]) -> None:
        self._ret = ret
        self.calls: list[dict] = []

    def resolve(self, **kwargs) -> tuple[Decimal, Decimal, Decimal]:  # type: ignore[override]
        self.calls.append(kwargs)
        return self._ret


class TestUsageRecorderSnapshot:
    """UsageRecorder captures provider.name / model.model_name at insert
    (SHU-727). Row still writes with FK ids even when snapshot is None."""

    @pytest.mark.asyncio
    async def test_snapshots_both_names_when_fk_targets_exist(self):
        session = _make_session(
            provider=_FakeProvider("Shu Curated: OpenAI Compatible"),
            model=_FakeModel("openai/gpt-4o"),
        )
        recorder = UsageRecorder(
            cost_resolver=_FakeResolver((Decimal("0"), Decimal("0"), Decimal("0.01"))),
        )

        await recorder.record(
            provider_id="prov-1",
            model_id="model-1",
            request_type="chat",
            user_id="user-1",
            input_tokens=100,
            output_tokens=50,
            total_cost=Decimal("0.01"),
            session=session,
        )

        session.add.assert_called_once()
        record = session.add.call_args.args[0]
        assert record.provider_name == "Shu Curated: OpenAI Compatible"
        assert record.model_name == "openai/gpt-4o"
        assert record.provider_id == "prov-1"
        assert record.model_id == "model-1"

    @pytest.mark.asyncio
    async def test_leaves_snapshots_null_when_fk_targets_missing(self):
        session = _make_session(provider=None, model=None)
        recorder = UsageRecorder(
            cost_resolver=_FakeResolver((Decimal("0"), Decimal("0"), Decimal("0"))),
        )

        await recorder.record(
            provider_id="prov-missing",
            model_id="model-missing",
            request_type="chat",
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.provider_name is None
        assert record.model_name is None

    @pytest.mark.asyncio
    async def test_partial_resolution_snapshots_what_it_can(self):
        session = _make_session(
            provider=_FakeProvider("Shu Curated: Mistral"),
            model=None,
        )
        recorder = UsageRecorder(
            cost_resolver=_FakeResolver((Decimal("0"), Decimal("0"), Decimal("0"))),
        )

        await recorder.record(
            provider_id="prov-1",
            model_id="model-missing",
            request_type="ocr",
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.provider_name == "Shu Curated: Mistral"
        assert record.model_name is None


class TestUsageRecorderCostDelegation:
    """UsageRecorder hands cost resolution to the injected CostResolver and
    writes whatever the resolver returns onto the LLMUsage row."""

    @pytest.mark.asyncio
    async def test_recorder_uses_resolver_output(self):
        """Whatever the resolver returns is what lands on the row."""
        session = _make_session(
            provider=_FakeProvider("Shu Curated: Example"),
            model=_FakeModel("example/model"),
        )
        fake = _FakeResolver((Decimal("0.5"), Decimal("0.25"), Decimal("0.75")))
        recorder = UsageRecorder(cost_resolver=fake)

        await recorder.record(
            provider_id="prov-1",
            model_id="model-1",
            request_type="chat",
            input_tokens=1000,
            output_tokens=500,
            total_cost=Decimal("0"),
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.input_cost == Decimal("0.5")
        assert record.output_cost == Decimal("0.25")
        assert record.total_cost == Decimal("0.75")

        # Resolver received the token counts and the caller's costs.
        assert len(fake.calls) == 1
        assert fake.calls[0]["input_tokens"] == 1000
        assert fake.calls[0]["output_tokens"] == 500
        assert fake.calls[0]["total_cost"] == Decimal("0")


class TestUsageRecorderTotalTokens:
    """total_tokens is a derived column: if the caller leaves it at 0,
    the recorder computes it from input_tokens + output_tokens so admin
    token-consumption reports don't show 0 for every chat row.
    """

    @pytest.mark.asyncio
    async def test_total_tokens_auto_computed_when_caller_omits_it(self):
        """Chat callers only pass input/output; total_tokens defaults to 0.
        The recorder fills it in as the sum."""
        session = _make_session(
            provider=_FakeProvider("Shu Curated: OpenAI Compatible"),
            model=_FakeModel("openai/gpt-4o"),
        )
        recorder = UsageRecorder(
            cost_resolver=_FakeResolver((Decimal("0"), Decimal("0"), Decimal("0.01"))),
        )

        await recorder.record(
            provider_id="prov-1",
            model_id="model-1",
            request_type="chat",
            input_tokens=100,
            output_tokens=50,
            # total_tokens not passed — default 0
            total_cost=Decimal("0.01"),
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.total_tokens == 150

    @pytest.mark.asyncio
    async def test_caller_supplied_total_tokens_wins(self):
        """When the caller passes a non-zero total_tokens (e.g. embedding
        API returns its own total), the recorder preserves it verbatim."""
        session = _make_session(
            provider=_FakeProvider("Shu Curated: OpenAI Compatible"),
            model=_FakeModel("text-embedding-3-large"),
        )
        recorder = UsageRecorder(
            cost_resolver=_FakeResolver((Decimal("0"), Decimal("0"), Decimal("0"))),
        )

        await recorder.record(
            provider_id="prov-1",
            model_id="model-1",
            request_type="embedding",
            input_tokens=1000,
            output_tokens=0,
            total_tokens=1234,  # API-reported, doesn't match sum
            total_cost=Decimal("0"),
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.total_tokens == 1234


class TestUsageRecorderFailureContract:
    """Failure semantics depend on transaction ownership (SHU-759).

    Before SHU-759 these branches shared a single fire-and-forget
    contract (introduced in SHU-715): every failure was logged and
    swallowed. That worked because no caller composed our write into a
    larger transaction. SHU-759's chat finalize started passing
    ``session=`` to achieve Message + LLMUsage atomicity (AC#3), and
    the unified swallow silently violated that — the surrounding
    transaction would commit the Message while our LLMUsage row had
    been rolled back via the nested savepoint. The contract now
    bifurcates:

    - ``session=None`` (fire-and-forget) — legacy callers stay
      decoupled from billing reliability. Failures logged, swallowed.
    - ``session=<AsyncSession>`` (caller-owned transaction) — failures
      propagate so the caller can roll back atomically.
    """

    @pytest.mark.asyncio
    async def test_session_path_propagates_failures(self):
        """When the caller passes ``session=``, ``_insert`` failures must
        propagate so the caller's outer transaction can roll back. This
        is the SHU-759 behavior; pre-fix the exception was swallowed and
        the caller would commit half a unit of work."""
        session = MagicMock()
        # Simulates any in-transaction failure: degenerate provider row,
        # cost-resolver math error, nested-savepoint flush failure, etc.
        session.get = AsyncMock(side_effect=RuntimeError("simulated savepoint failure"))
        recorder = UsageRecorder(cost_resolver=CostResolver())

        with pytest.raises(RuntimeError, match="simulated savepoint failure"):
            await recorder.record(
                provider_id="prov-1",
                model_id="model-1",
                request_type="chat",
                session=session,
            )

    @pytest.mark.asyncio
    async def test_fresh_session_path_still_swallows_failures(self, monkeypatch):
        """When the caller omits ``session=``, the legacy fire-and-forget
        semantic is preserved: billing failures must not crash callers
        like external embedding or the side-call service."""
        new_session = MagicMock()
        new_session.get = AsyncMock(side_effect=RuntimeError("DB down"))

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=new_session)
        session_cm.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(
            "shu.services.usage_recording.get_async_session_local",
            lambda: lambda: session_cm,
        )

        recorder = UsageRecorder(cost_resolver=CostResolver())

        # Must not raise — fire-and-forget callers depend on this.
        await recorder.record(
            provider_id="prov-1",
            model_id="model-1",
            request_type="embedding",
        )


class TestGetUsageRecorder:
    """The singleton accessor returns the same instance and matches the
    codebase's ``get_X()`` pattern for services."""

    def test_returns_singleton(self):
        from shu.services.usage_recording import get_usage_recorder

        a = get_usage_recorder()
        b = get_usage_recorder()
        assert a is b
        assert isinstance(a, UsageRecorder)
