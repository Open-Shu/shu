"""Migration 008_0002: Add access policy tables

Creates the three tables for the Policy-Based Access Control engine:
- access_policies: Named policies with allow/deny effect
- access_policy_bindings: Bind policies to users or groups
- access_policy_statements: Actions and resources governed by each policy

Part of SHU-613: Policy-Based Access Control Engine.
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import drop_table_if_exists, table_exists

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


def downgrade() -> None:
    """Drop access policy tables in reverse dependency order."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    drop_table_if_exists(inspector, "access_policy_statements")
    drop_table_if_exists(inspector, "access_policy_bindings")
    drop_table_if_exists(inspector, "access_policies")
