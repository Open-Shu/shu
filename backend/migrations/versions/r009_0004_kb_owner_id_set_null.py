"""Set ON DELETE SET NULL (owner_id) on knowledge_bases_owner_id_tfk.

Revision ID: r009_0004
Revises: r009_0003
Create Date: 2026-05-28

knowledge_bases.owner_id is nullable by design: a knowledge base outlives its
creator. The common pattern is spinning up a privileged user to ingest a
corpus, then deleting that user once ingestion is done — the corpus stays.

009 added the composite FK ``(tenant_id, owner_id) REFERENCES users(tenant_id,
id)`` without an ``ON DELETE`` clause, which defaults to NO ACTION. That
would block the user deletion in the pattern above. This migration changes
it to ``ON DELETE SET NULL (owner_id)`` — when a user is deleted, the KB's
``owner_id`` becomes NULL (an honest "no current owner" state) and the row
survives. The column-list ``(owner_id)`` scopes the NULL to that one column
only — without it, ``tenant_id`` would also be nulled, breaking RLS.

Requires PostgreSQL 15+ for the column-list syntax on ``ON DELETE SET NULL``.

Idempotent: DROP IF EXISTS + ADD CONSTRAINT recreates the constraint in the
desired shape regardless of prior state.

Policy: idempotent per docs/policies/DB_MIGRATION_POLICY.md §Policy.
"""

from __future__ import annotations

from alembic import op

revision = "r009_0004"
down_revision = "r009_0003"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "knowledge_bases_owner_id_tfk"


def upgrade() -> None:
    op.execute(f"ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE knowledge_bases ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"FOREIGN KEY (tenant_id, owner_id) REFERENCES users(tenant_id, id) "
        f"ON DELETE SET NULL (owner_id)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE knowledge_bases ADD CONSTRAINT {CONSTRAINT_NAME} "
        f"FOREIGN KEY (tenant_id, owner_id) REFERENCES users(tenant_id, id)"
    )
