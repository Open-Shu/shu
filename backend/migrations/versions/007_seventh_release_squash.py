"""Migration 007: Seventh Release Squash (007_0001..007_0005)

This migration condenses the seventh release development migrations into one.

Changes:
- Adds must_change_password boolean column to users table (admin password reset flow)
- Backfills knowledge_bases.document_count and total_chunks from actual data
- Adds index on attachments.expires_at for cleanup query performance
- Adds profiling_coverage_percent column to documents table
- Adds scope column to experiences table for shared experiences
- Makes experience_runs.user_id nullable for shared runs

Replaces: 007_0001_add_must_change_password,
          007_0002_backfill_kb_stats,
          007_0003_add_attachment_expires_at_index,
          007_0004_add_profiling_coverage_to_documents,
          007_0005_add_experience_scope_global_runs
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import (
    add_column_if_not_exists,
    column_exists,
    drop_column_if_exists,
    index_exists,
)

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None
replaces = ("007_0001", "007_0002", "007_0003", "007_0004", "007_0005")


def upgrade() -> None:
    """Apply all seventh release schema changes.

    All operations are idempotent — safe to run on databases that already have
    some or all of these changes applied (e.g., databases upgraded through the
    individual 007_* dev migrations).
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: Add must_change_password to users (from 007_0001)
    # ========================================================================
    add_column_if_not_exists(
        inspector,
        "users",
        sa.Column("must_change_password", sa.Boolean, server_default="false", nullable=False),
    )

    # ========================================================================
    # Part 2: Backfill knowledge_bases stats (from 007_0002)
    # ========================================================================
    op.execute("""
        UPDATE knowledge_bases kb
        SET document_count = COALESCE(
            (SELECT COUNT(*) FROM documents d WHERE d.knowledge_base_id = kb.id),
            0
        )
    """)

    op.execute("""
        UPDATE knowledge_bases kb
        SET total_chunks = COALESCE(
            (SELECT COUNT(*) FROM document_chunks dc WHERE dc.knowledge_base_id = kb.id),
            0
        )
    """)

    # ========================================================================
    # Part 3: Add index on attachments.expires_at (from 007_0003)
    # ========================================================================
    if not index_exists(inspector, "attachments", "ix_attachments_expires_at"):
        op.create_index("ix_attachments_expires_at", "attachments", ["expires_at"], if_not_exists=True)

    # ========================================================================
    # Part 4: Add profiling_coverage_percent to documents (from 007_0004)
    # ========================================================================
    add_column_if_not_exists(
        inspector,
        "documents",
        sa.Column("profiling_coverage_percent", sa.Float(), nullable=True),
    )

    # ========================================================================
    # Part 5: Add scope to experiences + nullable user_id on runs (from 007_0005)
    # ========================================================================
    add_column_if_not_exists(
        inspector,
        "experiences",
        sa.Column("scope", sa.String(20), nullable=False, server_default="user"),
    )

    if not index_exists(inspector, "experiences", "idx_experiences_scope"):
        op.create_index("idx_experiences_scope", "experiences", ["scope"])

    # Make experience_runs.user_id nullable (idempotent: only alter if currently NOT NULL)
    if column_exists(inspector, "experience_runs", "user_id"):
        cols = inspector.get_columns("experience_runs")
        user_id_col = next((c for c in cols if c["name"] == "user_id"), None)
        if user_id_col and not user_id_col["nullable"]:
            op.alter_column("experience_runs", "user_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Revert all seventh release schema changes.

    All operations are idempotent — safe to run on databases that have already
    been partially downgraded.

    Reversal order is the inverse of upgrade to respect dependencies.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 5 (reverse): Restore NOT NULL on user_id; remove scope
    # ========================================================================
    if column_exists(inspector, "experience_runs", "user_id"):
        # Delete shared runs before restoring NOT NULL
        op.execute("DELETE FROM experience_runs WHERE user_id IS NULL")
        cols = inspector.get_columns("experience_runs")
        user_id_col = next((c for c in cols if c["name"] == "user_id"), None)
        if user_id_col and user_id_col["nullable"]:
            op.alter_column("experience_runs", "user_id", existing_type=sa.String(), nullable=False)

    if index_exists(inspector, "experiences", "idx_experiences_scope"):
        op.drop_index("idx_experiences_scope", table_name="experiences")

    drop_column_if_exists(inspector, "experiences", "scope")

    # ========================================================================
    # Part 4 (reverse): Remove profiling_coverage_percent
    # ========================================================================
    drop_column_if_exists(inspector, "documents", "profiling_coverage_percent")

    # ========================================================================
    # Part 3 (reverse): Remove expires_at index
    # ========================================================================
    if index_exists(inspector, "attachments", "ix_attachments_expires_at"):
        op.drop_index("ix_attachments_expires_at", table_name="attachments")

    # ========================================================================
    # Part 2 (reverse): No reversal needed for backfill — data stays as-is
    # ========================================================================

    # ========================================================================
    # Part 1 (reverse): Remove must_change_password
    # ========================================================================
    drop_column_if_exists(inspector, "users", "must_change_password")
