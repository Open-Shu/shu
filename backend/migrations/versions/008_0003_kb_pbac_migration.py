"""Migration 008_0003: Add slug column to knowledge_bases

Adds a ``slug`` column to the ``knowledge_bases`` table so PBAC resource
identifiers use human-readable, wildcard-friendly names instead of UUIDs.

Existing knowledge bases are backfilled from their ``name`` column. Duplicates
are resolved by appending a numeric suffix (e.g., ``my-kb-2``), with the
earliest-created KB receiving the canonical slug.

Part of SHU-613: Policy-Based Access Control Engine.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import column_exists, drop_column_if_exists, index_exists, slugify


# revision identifiers, used by Alembic.
revision = "008_0003"
down_revision = "008_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add slug column to knowledge_bases and backfill from names."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not column_exists(inspector, "knowledge_bases", "slug"):
        op.add_column("knowledge_bases", sa.Column("slug", sa.String(100), nullable=True))

        kb_table = sa.table(
            "knowledge_bases",
            sa.column("id", sa.String),
            sa.column("name", sa.String),
            sa.column("slug", sa.String),
            sa.column("created_at", sa.DateTime),
        )

        rows = conn.execute(
            sa.select(kb_table.c.id, kb_table.c.name).order_by(kb_table.c.created_at.asc())
        ).fetchall()

        seen_slugs: set[str] = set()
        for row in rows:
            slug = slugify(row.name) or "kb"
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            conn.execute(
                kb_table.update().where(kb_table.c.id == row.id).values(slug=slug)
            )

        op.alter_column("knowledge_bases", "slug", nullable=False)
        op.create_index(
            "ix_knowledge_bases_slug", "knowledge_bases", ["slug"], unique=True
        )

    # Phase 3: Permission-to-PBAC migration will be added here


def downgrade() -> None:
    """Drop slug column from knowledge_bases."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Phase 3: Permission-to-PBAC rollback will be added here

    if index_exists(inspector, "knowledge_bases", "ix_knowledge_bases_slug"):
        op.drop_index("ix_knowledge_bases_slug", table_name="knowledge_bases")
    drop_column_if_exists(inspector, "knowledge_bases", "slug")
