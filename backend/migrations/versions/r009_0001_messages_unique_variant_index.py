"""Dedupe colliding (parent_message_id, variant_index) rows and add UNIQUE constraint.

Revision ID: r009_0001
Revises: 008
Create Date: 2026-05-11

SHU-759 — closes a pre-existing race in regen variant_index computation
([chat_service.py:_locate_regeneration_indices]) where two concurrent
regenerates of the same target could compute identical `next_idx` values.
This migration:

1. Dedupes any existing rows that already violate `UNIQUE (parent_message_id,
   variant_index)`. Affected variant groups are renumbered sequentially in
   `created_at` order — preserving the legacy backfill semantics in
   `ChatService.get_conversation_messages`. Rows with NULL parent_message_id
   or NULL variant_index are left alone (Postgres treats NULLs as distinct
   under UNIQUE).
2. Creates the UNIQUE index. ``CREATE UNIQUE INDEX IF NOT EXISTS`` makes the
   step idempotent on re-runs.

Downgrade drops the index; the dedupe is not reversed (the renumbered data
was previously invalid, so reverting it is meaningless).

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "r009_0001"
down_revision = "008"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_messages_parent_variant_index"


def upgrade() -> None:
    """Dedupe colliding rows, then add the UNIQUE index."""

    # Step 1 — dedupe.
    #
    # Find every parent_message_id group that has at least one duplicate
    # (parent, variant_index) pair, then renumber the entire group's
    # non-NULL variant_index children sequentially by created_at,id. Using
    # the full group (rather than only renumbering one half of each
    # conflicting pair) keeps the sequence dense and matches the lineage-
    # backfill behaviour in get_conversation_messages.
    #
    # IS DISTINCT FROM guards against a no-op write when the existing value
    # already matches the new value.
    op.execute(
        """
        WITH conflicting_pairs AS (
            SELECT parent_message_id
            FROM messages
            WHERE parent_message_id IS NOT NULL
              AND variant_index IS NOT NULL
            GROUP BY parent_message_id, variant_index
            HAVING COUNT(*) > 1
        ),
        affected_parents AS (
            SELECT DISTINCT parent_message_id FROM conflicting_pairs
        ),
        to_renumber AS (
            SELECT
                m.id,
                (
                    ROW_NUMBER() OVER (
                        PARTITION BY m.parent_message_id
                        ORDER BY m.created_at, m.id
                    ) - 1
                ) AS new_idx
            FROM messages m
            JOIN affected_parents ap ON ap.parent_message_id = m.parent_message_id
            WHERE m.variant_index IS NOT NULL
        )
        UPDATE messages
        SET variant_index = to_renumber.new_idx
        FROM to_renumber
        WHERE messages.id = to_renumber.id
          AND messages.variant_index IS DISTINCT FROM to_renumber.new_idx;
        """
    )

    # Step 2 — add the UNIQUE constraint via a UNIQUE INDEX with IF NOT
    # EXISTS so re-running the migration is a no-op once the index exists.
    # asyncpg/psycopg2 raise IntegrityError on insert conflicts either way;
    # the index is functionally equivalent to a table-level constraint for
    # the SHU-759 retry-on-conflict logic.
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
        ON messages (parent_message_id, variant_index);
        """
    )


def downgrade() -> None:
    """Drop the UNIQUE index. The dedupe is not reversed."""
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME};")
