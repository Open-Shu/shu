"""Live-model-graph helpers used only by the migration inventory tests.

Two tests in this directory compare the live SQLAlchemy model graph against
frozen snapshots:

  * ``test_composite_fk_inventory`` — diffs against ``composite_fk_inventory.json``.
  * ``test_stage_a_table_inventory`` — diffs against the inline frozen lists
    in migration 009.

Both need the same walker. Originally this lived under ``shu.models`` because
migration 009 also called it at apply time, but 009's inventory was later
frozen inline so the walker is now test-only. Keeping it under ``tests/``
makes the dependency direction explicit: tests reach into models, not the
other way.
"""

from __future__ import annotations

from shu.core.database import Base

# (child_table, child_column, parent_table, parent_column)
CompositeFk = tuple[str, str, str, str]


def tenant_scoped_table_names() -> set[str]:
    return {name for name, table in Base.metadata.tables.items() if "tenant_id" in table.columns}


def compute_composite_fk_inventory() -> list[CompositeFk]:
    """Return every FK whose child and parent are both tenant-scoped.

    Self-referential FKs and FKs to global tables (``llm_providers``,
    ``plugin_definitions``, etc.) are excluded — composite tenant matching
    is only meaningful between two tenant-stamped rows.
    """
    tenant_scoped = tenant_scoped_table_names()
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
