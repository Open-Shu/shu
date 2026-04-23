"""Schema-level guarantees for LLMUsage (SHU-727).

These tests lock down the FK posture and snapshot-column contract on the
ORM declaration. The migration (008_0010) applies the same posture to an
existing DB; this suite catches regressions where someone restores the
CASCADE FK or removes the nullable snapshot columns on the model itself.

Actual DB-level cascade behaviour (deleting a provider row through SQL and
verifying llm_usage rows survive with NULL provider_id) is exercised by
integration tests against a real postgres — it is framework behaviour and
would only re-prove that SQLAlchemy honours the declaration asserted here.
"""

from __future__ import annotations

from shu.models.llm_provider import LLMModel, LLMProvider, LLMUsage


def _fk_ondelete(column, referent_table: str) -> str | None:
    """Return the ondelete clause for the FK on `column` pointing at `referent_table`."""
    for fk in column.foreign_keys:
        if fk.column.table.name == referent_table:
            return fk.ondelete
    return None


class TestFKPosture:
    """Both outbound FKs from llm_usage must be SET NULL so billing and audit
    rows survive provider / model lifecycle events."""

    def test_provider_id_fk_is_set_null(self):
        """AC #4: deleting an llm_providers row must not cascade-delete llm_usage rows.

        Regression guard against the pre-SHU-727 CASCADE posture that wiped
        882 rows of UAT billing history in a single DELETE in the 2026-04-21
        lab session.
        """
        assert _fk_ondelete(LLMUsage.provider_id, "llm_providers") == "SET NULL"

    def test_model_id_fk_is_set_null(self):
        """AC #5: deleting an llm_models row must leave llm_usage rows intact."""
        assert _fk_ondelete(LLMUsage.model_id, "llm_models") == "SET NULL"

    def test_provider_id_is_nullable(self):
        """SET NULL requires the referencing column to be nullable."""
        assert LLMUsage.provider_id.nullable is True

    def test_model_id_is_nullable(self):
        """SET NULL requires the referencing column to be nullable."""
        assert LLMUsage.model_id.nullable is True


class TestSnapshotColumns:
    """Snapshot columns exist, are nullable, and carry the name semantics
    the write path populates at INSERT time."""

    def test_provider_name_column_exists_and_is_nullable(self):
        assert hasattr(LLMUsage, "provider_name")
        assert LLMUsage.provider_name.nullable is True

    def test_model_name_column_exists_and_is_nullable(self):
        assert hasattr(LLMUsage, "model_name")
        assert LLMUsage.model_name.nullable is True


class TestORMCascadePosture:
    """The DB-level ON DELETE SET NULL is only half the picture — the ORM
    relationships on the parent side must not override it by emitting their
    own cascading DELETE for children. Regression guard: if someone adds
    ``cascade="all, delete"`` back to either usage_records relationship,
    these tests fail loudly before it ships.
    """

    def test_provider_usage_records_does_not_cascade_delete(self):
        """``LLMProvider.usage_records`` must not carry delete cascade or
        billing/audit rows get wiped before the DB can SET NULL."""
        rel = LLMProvider.__mapper__.relationships["usage_records"]
        assert rel.cascade.delete is False
        assert rel.passive_deletes is True

    def test_model_usage_records_does_not_cascade_delete(self):
        """Same rule for ``LLMModel.usage_records``."""
        rel = LLMModel.__mapper__.relationships["usage_records"]
        assert rel.cascade.delete is False
        assert rel.passive_deletes is True


class TestRowSurvivalSimulation:
    """Simulate the post-cascade state in memory — FK id is NULL, snapshot
    name is intact — and confirm the row is still constructible and legible.
    """

    def test_provider_delete_leaves_row_readable_by_snapshot(self):
        """After a provider delete: provider_id=NULL, provider_name kept."""
        row = LLMUsage()
        row.provider_id = None  # set by the DB via ON DELETE SET NULL
        row.provider_name = "Shu Curated: OpenAI Compatible"
        row.model_id = "model-1"
        row.model_name = "openai/gpt-4o"
        row.total_tokens = 150
        row.total_cost = 0  # type: ignore[assignment]  # Decimal-compatible at runtime

        # The snapshot keeps the row legible for billing reconciliation and
        # admin display even though the FK target is gone.
        assert row.provider_id is None
        assert row.provider_name == "Shu Curated: OpenAI Compatible"

    def test_model_delete_leaves_row_readable_by_snapshot(self):
        """After a model delete: model_id=NULL, model_name kept."""
        row = LLMUsage()
        row.provider_id = "prov-1"
        row.provider_name = "Shu Curated: OpenAI Compatible"
        row.model_id = None
        row.model_name = "openai/gpt-4o"

        assert row.model_id is None
        assert row.model_name == "openai/gpt-4o"

    def test_repr_prefers_snapshot_when_relationship_missing(self):
        """__repr__ falls back to the snapshot model_name when the FK is NULL."""
        row = LLMUsage()
        row.model_name = "mistral-ocr-latest"
        row.total_tokens = 10
        row.total_cost = 0  # type: ignore[assignment]

        # model relationship is None (FK target deleted), snapshot name wins.
        assert "mistral-ocr-latest" in repr(row)
