"""Tests for shu.services.usage_recording.

Focus: the snapshot behaviour introduced in SHU-727 — record_llm_usage captures
provider.name and model.model_name at INSERT time so billing/audit rows remain
readable after FK targets are deleted.

Deliberately not tested here (unit-scope):
- ON DELETE SET NULL cascade behaviour on the actual postgres table. That is
  enforced by the migration itself (008_0010) and the schema-level test that
  asserts the FK's ondelete posture; covering it in a Python unit test would
  require a real DB session and just re-prove framework behaviour.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.services.usage_recording import record_llm_usage


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeModel:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


def _make_session(
    *,
    provider: _FakeProvider | None,
    model: _FakeModel | None,
) -> MagicMock:
    """Build a MagicMock AsyncSession whose .get returns provider/model by type."""
    session = MagicMock()

    async def _get(cls, obj_id):  # noqa: ARG001 — mirrors session.get signature
        # Cheap type dispatch — the real session.get keys on the model class.
        if cls.__name__ == "LLMProvider":
            return provider
        if cls.__name__ == "LLMModel":
            return model
        return None

    session.get = AsyncMock(side_effect=_get)
    session.add = MagicMock()
    session.flush = AsyncMock()

    # begin_nested returns an async context manager.
    nested_cm = MagicMock()
    nested_cm.__aenter__ = AsyncMock(return_value=nested_cm)
    nested_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin_nested = MagicMock(return_value=nested_cm)

    return session


class TestSnapshotNames:
    """record_llm_usage captures provider.name / model.model_name at insert."""

    @pytest.mark.asyncio
    async def test_snapshots_both_names_when_fk_targets_exist(self):
        """Happy path — the helper stamps provider_name and model_name on the row."""
        session = _make_session(
            provider=_FakeProvider("Shu Curated: OpenAI Compatible"),
            model=_FakeModel("openai/gpt-4o"),
        )

        await record_llm_usage(
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
        """If either FK target can't be resolved, the snapshot is left NULL.

        The row still inserts with the FK ids — the snapshot is best-effort
        audit context. Nothing in the billing path should break on a NULL name.
        """
        session = _make_session(provider=None, model=None)

        await record_llm_usage(
            provider_id="prov-missing",
            model_id="model-missing",
            request_type="chat",
            session=session,
        )

        session.add.assert_called_once()
        record = session.add.call_args.args[0]
        assert record.provider_name is None
        assert record.model_name is None

    @pytest.mark.asyncio
    async def test_partial_resolution_snapshots_what_it_can(self):
        """Provider resolves but model doesn't (or vice versa) — snapshot what's available."""
        session = _make_session(
            provider=_FakeProvider("Shu Curated: Mistral"),
            model=None,
        )

        await record_llm_usage(
            provider_id="prov-1",
            model_id="model-missing",
            request_type="ocr",
            session=session,
        )

        record = session.add.call_args.args[0]
        assert record.provider_name == "Shu Curated: Mistral"
        assert record.model_name is None
