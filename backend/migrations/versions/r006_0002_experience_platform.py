"""Migration r006_0002: Experience Platform

This migration adds the schema for the Experience Platform (SHU-19).

Creates:
- experiences table - configurable compositions of data sources, prompts, LLM synthesis
- experience_steps table - individual steps (plugin calls, KB queries, etc.)
- experience_runs table - execution history with inputs, outputs, and results
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
import uuid
from datetime import datetime, timezone

from migrations.helpers import (
    table_exists,
    index_exists,
    drop_table_if_exists,
)

# revision identifiers, used by Alembic.
revision = "r006_0002"
down_revision = "r006_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: Create experiences table
    # ========================================================================
    if not table_exists(inspector, "experiences"):
        op.create_table(
            "experiences",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),

            # Basic information
            sa.Column("name", sa.String(100), nullable=False, index=True),
            sa.Column("description", sa.Text(), nullable=True),

            # Ownership & visibility
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="draft"),

            # Trigger configuration
            sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("trigger_config", JSONB(), nullable=True),

            # Backlink flag
            sa.Column("include_previous_run", sa.Boolean(), nullable=False, server_default="false"),

            # LLM Configuration
            sa.Column(
                "llm_provider_id",
                sa.String(36),
                sa.ForeignKey("llm_providers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("model_name", sa.String(100), nullable=True),

            # Prompt configuration
            sa.Column(
                "prompt_id",
                sa.String(36),
                sa.ForeignKey("prompts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("inline_prompt_template", sa.Text(), nullable=True),

            # Version control (forward-looking)
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_active_version", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "parent_version_id",
                sa.String(36),
                sa.ForeignKey("experiences.id", ondelete="SET NULL"),
                nullable=True,
            ),

            # Constraints & budgets
            sa.Column("max_run_seconds", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("token_budget", sa.Integer(), nullable=True),

            # Scheduler fields
            sa.Column("next_run_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("last_run_at", TIMESTAMP(timezone=True), nullable=True),
        )

        # Additional indexes for experiences
        op.create_index("ix_experiences_visibility", "experiences", ["visibility"])
        op.create_index("ix_experiences_active_version", "experiences", ["is_active_version"])
        op.create_index("ix_experiences_next_run_at", "experiences", ["next_run_at"])

    # ========================================================================
    # Part 2: Create experience_steps table
    # ========================================================================
    if not table_exists(inspector, "experience_steps"):
        op.create_table(
            "experience_steps",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),

            # Parent experience
            sa.Column(
                "experience_id",
                sa.String(36),
                sa.ForeignKey("experiences.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("order", sa.Integer(), nullable=False),
            sa.Column("step_key", sa.String(50), nullable=False),

            # Step type
            sa.Column("step_type", sa.String(30), nullable=False, server_default="plugin"),

            # Plugin configuration
            sa.Column("plugin_name", sa.String(100), nullable=True),
            sa.Column("plugin_op", sa.String(100), nullable=True),

            # Knowledge Base configuration
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("kb_query_template", sa.Text(), nullable=True),

            # Parameters template (JSON with Jinja2 expressions)
            sa.Column("params_template", JSONB(), nullable=True),

            # Conditional execution (forward-looking)
            sa.Column("condition_template", sa.Text(), nullable=True),

            # Required scopes (cached)
            sa.Column("required_scopes", JSONB(), nullable=True),

            # Unique constraint: step_key must be unique within an experience
            sa.UniqueConstraint(
                "experience_id", "step_key",
                name="uq_experience_steps_experience_step_key"
            ),
        )

    # ========================================================================
    # Part 3: Create experience_runs table
    # ========================================================================
    if not table_exists(inspector, "experience_runs"):
        op.create_table(
            "experience_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),

            # Parent experience and user
            sa.Column(
                "experience_id",
                sa.String(36),
                sa.ForeignKey("experiences.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),

            # Backlink to previous run
            sa.Column(
                "previous_run_id",
                sa.String(36),
                sa.ForeignKey("experience_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),

            # Model snapshot at execution time
            sa.Column("model_provider_id", sa.String(36), nullable=True),
            sa.Column("model_name", sa.String(100), nullable=True),

            # Status tracking
            sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
            sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("finished_at", TIMESTAMP(timezone=True), nullable=True),

            # Step-by-step execution state
            sa.Column("step_states", JSONB(), nullable=True),

            # Inputs & Outputs
            sa.Column("input_params", JSONB(), nullable=True),
            sa.Column("step_outputs", JSONB(), nullable=True),

            # Final LLM result
            sa.Column("result_content", sa.Text(), nullable=True),
            sa.Column("result_metadata", JSONB(), nullable=True),

            # Error tracking
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("error_details", JSONB(), nullable=True),
        )

        # Composite index for user's runs of a specific experience (common query)
        op.create_index(
            "ix_experience_runs_experience_user",
            "experience_runs",
            ["experience_id", "user_id"],
        )

        # Index for finding latest run (for previous_run backlink)
        op.create_index(
            "ix_experience_runs_experience_user_finished",
            "experience_runs",
            ["experience_id", "user_id", "finished_at"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop indexes first
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user_finished")
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user")
    op.execute("DROP INDEX IF EXISTS ix_experiences_next_run_at")

    # Drop tables in reverse order (respecting foreign key dependencies)
    drop_table_if_exists(inspector, "experience_runs")
    drop_table_if_exists(inspector, "experience_steps")
    drop_table_if_exists(inspector, "experiences")
