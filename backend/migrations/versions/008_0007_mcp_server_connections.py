"""Create mcp_server_connections table

Revision ID: 008_0007
Revises: 008_0006
Create Date: 2026-03-26
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "008_0007"
down_revision = "008_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("name", sa.String(96), nullable=False),  # plugin name limit + "mcp:" prefix
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("tool_configs", sa.JSON(), nullable=True),
        sa.Column("discovered_tools", sa.JSON(), nullable=True),
        sa.Column("timeouts", sa.JSON(), nullable=True),
        sa.Column("response_size_limit_bytes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_connected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("server_info", sa.JSON(), nullable=True),
        sa.UniqueConstraint("name", name="uq_mcp_server_connections_name"),
    )
    op.create_index("ix_mcp_server_connections_enabled", "mcp_server_connections", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_connections_enabled", table_name="mcp_server_connections")
    op.drop_table("mcp_server_connections")
