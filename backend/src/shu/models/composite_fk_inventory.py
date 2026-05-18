"""Composite-FK inventory used by migration 009 and its snapshot test.

The function walks ``Base.metadata.tables`` so the inventory tracks the live
model graph without a hand-curated list. Two consumers share it:

  * migration 009 (Section D) — emits ``ALTER TABLE ... ADD CONSTRAINT ... FK``
    for every tuple at apply time.
  * ``test_composite_fk_inventory`` — diffs the live result against the
    committed JSON snapshot so a PR that adds a tenant-scoped FK has to
    update the snapshot, making the change visible in review.

Keeping the walker here (and not duplicating it in each consumer) means the
snapshot reflects exactly what the migration will emit — no drift window.
"""

from __future__ import annotations

from shu.core.database import Base

# (child_table, child_column, parent_table, parent_column)
CompositeFk = tuple[str, str, str, str]


def _tenant_scoped_table_names() -> set[str]:
    return {name for name, table in Base.metadata.tables.items() if "tenant_id" in table.columns}


def compute_composite_fk_inventory() -> list[CompositeFk]:
    """Return every FK whose child and parent are both tenant-scoped.

    Self-referential FKs and FKs to global tables (``llm_providers``,
    ``plugin_definitions``, etc.) are excluded — composite tenant matching
    is only meaningful between two tenant-stamped rows.
    """
    tenant_scoped = _tenant_scoped_table_names()
    result: list[CompositeFk] = []
    for child_name, child_table in Base.metadata.tables.items():
        if child_name not in tenant_scoped:
            continue
        for col in child_table.columns:
            for fk in col.foreign_keys:
                parent_name = fk.column.table.name
                if parent_name == child_name:
                    continue  # self-referential
                if parent_name not in tenant_scoped:
                    continue  # FK to global table
                result.append((child_name, col.name, parent_name, fk.column.name))
    return sorted(result)
