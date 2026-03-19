"""Migration 008_0002: Add access policy tables and experience slug column

Creates the three tables for the Policy-Based Access Control engine:
- access_policies: Named policies with allow/deny effect
- access_policy_bindings: Bind policies to users or groups
- access_policy_statements: Actions and resources governed by each policy

Also adds a ``slug`` column to the ``experiences`` table so PBAC resource
identifiers use human-readable, wildcard-friendly names instead of UUIDs.

Part of SHU-613: Policy-Based Access Control Engine.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, column_exists, drop_column_if_exists, drop_table_if_exists, index_exists, slugify, table_exists

# revision identifiers, used by Alembic.
revision = "008_0002"
down_revision = "008_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create access_policies, access_policy_bindings, and access_policy_statements tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Table 1: access_policies
    # ========================================================================
    if not table_exists(inspector, "access_policies"):
        op.create_table(
            "access_policies",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("effect", sa.String(10), nullable=False),
            sa.CheckConstraint("effect IN ('allow', 'deny')", name="chk_policy_effect"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )
        op.create_index("ix_access_policies_name", "access_policies", ["name"])
        op.create_index("ix_access_policies_is_active", "access_policies", ["is_active"])
        op.create_index("ix_access_policies_created_by", "access_policies", ["created_by"])

    # ========================================================================
    # Table 2: access_policy_bindings
    # ========================================================================
    if not table_exists(inspector, "access_policy_bindings"):
        op.create_table(
            "access_policy_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("access_policies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("actor_type", sa.String(10), nullable=False),
            sa.Column("actor_id", sa.String(36), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
            sa.UniqueConstraint("policy_id", "actor_type", "actor_id", name="uq_binding_policy_actor"),
            sa.CheckConstraint("actor_type IN ('user', 'group')", name="chk_binding_actor_type"),
        )
        op.create_index("ix_access_policy_bindings_policy_id", "access_policy_bindings", ["policy_id"])
        op.create_index("ix_access_policy_bindings_actor_type", "access_policy_bindings", ["actor_type"])
        op.create_index("ix_access_policy_bindings_actor_id", "access_policy_bindings", ["actor_id"])

    # ========================================================================
    # Table 3: access_policy_statements
    # ========================================================================
    if not table_exists(inspector, "access_policy_statements"):
        op.create_table(
            "access_policy_statements",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "policy_id",
                sa.String(36),
                sa.ForeignKey("access_policies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("actions", sa.JSON(), nullable=False),
            sa.Column("resources", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_access_policy_statements_policy_id", "access_policy_statements", ["policy_id"]
        )


    # ========================================================================
    # Part 4: Add slug column to experiences table
    # ========================================================================
    if not column_exists(inspector, "experiences", "slug"):
        # Add column as nullable first so we can backfill existing rows.
        op.add_column("experiences", sa.Column("slug", sa.String(100), nullable=True))

        # Backfill slugs from existing experience names.
        # Duplicates (same slug or empty slug) are deleted — acceptable for the
        # small number of experiences in production.
        experiences_table = sa.table(
            "experiences",
            sa.column("id", sa.String),
            sa.column("name", sa.String),
            sa.column("slug", sa.String),
        )
        rows = conn.execute(sa.select(experiences_table.c.id, experiences_table.c.name)).fetchall()
        seen_slugs: set[str] = set()
        for row in rows:
            slug = slugify(row.name)
            if not slug or slug in seen_slugs:
                conn.execute(experiences_table.delete().where(experiences_table.c.id == row.id))
                continue
            seen_slugs.add(slug)
            conn.execute(
                experiences_table.update()
                .where(experiences_table.c.id == row.id)
                .values(slug=slug)
            )

        # Now make it non-nullable and add unique index.
        op.alter_column("experiences", "slug", nullable=False)
        op.create_index("ix_experiences_slug", "experiences", ["slug"], unique=True)


def downgrade() -> None:
    """Drop access policy tables and experience slug column."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if index_exists(inspector, "experiences", "ix_experiences_slug"):
        op.drop_index("ix_experiences_slug", table_name="experiences")
    drop_column_if_exists(inspector, "experiences", "slug")

    drop_table_if_exists(inspector, "access_policy_statements")
    drop_table_if_exists(inspector, "access_policy_bindings")
    drop_table_if_exists(inspector, "access_policies")
