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


    # ========================================================================
    # Part 4: Create Morning Briefing experience
    # ========================================================================
    experience_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    # Get first user to own this experience (required field)
    result = conn.execute(sa.text("SELECT id FROM users LIMIT 1"))
    row = result.fetchone()
    if row:
        owner_user_id = row[0]
        
        # Create the Morning Briefing experience
        conn.execute(
            sa.text("""
                INSERT INTO experiences (
                    id, name, description, visibility, trigger_type, trigger_config,
                    max_run_seconds, include_previous_run, prompt_id, inline_prompt_template,
                    llm_provider_id, model_name, version, is_active_version,
                    created_by, created_at, updated_at
                ) VALUES (
                    :id, :name, :description, :visibility, :trigger_type, :trigger_config,
                    :max_run_seconds, :include_previous_run, :prompt_id, :inline_prompt_template,
                    :llm_provider_id, :model_name, :version, :is_active_version,
                    :created_by, :created_at, :updated_at
                )
            """),
            {
                "id": experience_id,
                "name": "Morning Briefing",
                "description": (
                    "Daily morning briefing synthesizing emails, calendar events, "
                    "and chat messages from the past 24-72 hours."
                ),
                "visibility": "draft",
                "trigger_type": "cron",
                "trigger_config": '{"timezone": "America/New_York", "cron": "0 7 * * *"}',
                "max_run_seconds": 180,
                "include_previous_run": True,
                "prompt_id": None,
                "inline_prompt_template": """Synthesize a morning briefing for {{ user.display_name }} based on the `gmail_digest`, `calendar_events`, and `gchat_digest` data.

## Instructions
- Review all emails, calendar events, and chat messages
- Highlight important action items and urgent matters first
- Group by category (email priorities, meetings, chat highlights)
- Flag likely spam/bulk emails under a separate "Likely Spam" section with brief reasons
- Keep it concise but comprehensive

Please synthesize this information into a clear, actionable morning briefing.

### Special instructions

You have the previous run result at your disposal. Compare it to the current step data and see if there is something the user may have forgotten to address. Give the user a gentle nudge on those things and make sure to stress the importance.

```
{{ previous_run.result_content }}
```""",
                "llm_provider_id": None,  # Will use user's default
                "model_name": None,
                "version": 1,
                "is_active_version": True,
                "created_by": owner_user_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        
        # Create experience steps
        steps = [
            {
                "id": str(uuid.uuid4()),
                "experience_id": experience_id,
                "order": 1,
                "step_key": "gmail_digest",
                "step_type": "plugin",
                "plugin_name": "gmail_digest",
                "plugin_op": "list",
                "params_template": '{"since_hours": 72, "max_results": 50}',
                "condition_template": None,
                "knowledge_base_id": None,
                "kb_query_template": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "experience_id": experience_id,
                "order": 2,
                "step_key": "calendar_events",
                "step_type": "plugin",
                "plugin_name": "calendar_events",
                "plugin_op": "list",
                "params_template": '{"since_hours": 48, "max_results": 50}',
                "condition_template": None,
                "knowledge_base_id": None,
                "kb_query_template": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "experience_id": experience_id,
                "order": 3,
                "step_key": "gchat_digest",
                "step_type": "plugin",
                "plugin_name": "gchat_digest",
                "plugin_op": "list",
                "params_template": '{"since_hours": 168, "max_spaces": 20, "max_messages_per_space": 100}',
                "condition_template": None,
                "knowledge_base_id": None,
                "kb_query_template": None,
                "created_at": now,
                "updated_at": now,
            },
        ]
        
        for step in steps:
            conn.execute(
                sa.text("""
                    INSERT INTO experience_steps (
                        id, experience_id, "order", step_key, step_type,
                        plugin_name, plugin_op, params_template, condition_template,
                        knowledge_base_id, kb_query_template, created_at, updated_at
                    ) VALUES (
                        :id, :experience_id, :order, :step_key, :step_type,
                        :plugin_name, :plugin_op, :params_template, :condition_template,
                        :knowledge_base_id, :kb_query_template, :created_at, :updated_at
                    )
                """),
                step
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 4: Delete Morning Briefing (if exists)
    # ========================================================================
    if table_exists(inspector, "experience_steps") and table_exists(inspector, "experiences"):
        conn.execute(
            sa.text("""
                DELETE FROM experience_steps 
                WHERE experience_id IN (
                    SELECT id FROM experiences WHERE name = 'Morning Briefing'
                )
            """)
        )
        conn.execute(
            sa.text("""
                DELETE FROM experiences 
                WHERE name = 'Morning Briefing'
            """)
        )

    # Drop indexes first
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user_finished")
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user")
    op.execute("DROP INDEX IF EXISTS ix_experiences_next_run_at")

    # Drop tables in reverse order (respecting foreign key dependencies)
    drop_table_if_exists(inspector, "experience_runs")
    drop_table_if_exists(inspector, "experience_steps")
    drop_table_if_exists(inspector, "experiences")
