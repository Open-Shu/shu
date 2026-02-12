"""Migration 006: Sixth Release Squash (r006_0001..r006_0004)

This migration condenses the sixth release development migrations into one.

Changes:
- Adds document profiling columns to documents table (synopsis, synopsis_embedding,
  document_type, capability_manifest, profiling_status, profiling_error, relational_context)
- Adds chunk enrichment columns to document_chunks table (summary, keywords, topics)
- Creates document_queries table for synthesized queries with vector embeddings
- Creates document_participants table for entity tracking
- Creates document_projects table for project associations
- Removes cost_per_input_token and cost_per_output_token from llm_providers
- Creates experiences table for configurable data/prompt/LLM compositions
- Creates experience_steps table for individual execution steps
- Creates experience_runs table for execution history
- Adds is_favorite column to conversations table
- Migrates google_id data from users to provider_identities table, drops google_id column

Replaces: r006_0001_document_profiling_and_rate_limiting,
          r006_0002_experience_platform,
          r006_0003_add_is_favorite_to_conversations,
          r006_0004_migrate_google_id_to_provider_identity
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from migrations.helpers import (
    add_column_if_not_exists,
    column_exists,
    drop_column_if_exists,
    drop_table_if_exists,
    index_exists,
    table_exists,
)

# Optional pgvector
try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except Exception:  # pragma: no cover
    Vector = lambda dim: sa.Text  # fallback for environments without pgvector

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None
replaces = ("r006_0001", "r006_0002", "r006_0003", "r006_0004")


def upgrade() -> None:
    """Apply all sixth release schema changes.

    All operations are idempotent — safe to run on databases that already have
    some or all of these changes applied (e.g., databases upgraded through the
    individual r006 dev migrations).
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: Document profiling columns on documents table (from r006_0001)
    # ========================================================================
    add_column_if_not_exists(inspector, "documents", sa.Column("synopsis", sa.Text(), nullable=True))
    add_column_if_not_exists(inspector, "documents", sa.Column("synopsis_embedding", Vector(384), nullable=True))
    add_column_if_not_exists(inspector, "documents", sa.Column("document_type", sa.String(50), nullable=True))
    add_column_if_not_exists(inspector, "documents", sa.Column("capability_manifest", JSONB(), nullable=True))
    add_column_if_not_exists(
        inspector,
        "documents",
        sa.Column("profiling_status", sa.String(20), nullable=True, server_default="'pending'"),
    )
    add_column_if_not_exists(inspector, "documents", sa.Column("profiling_error", sa.Text(), nullable=True))
    add_column_if_not_exists(inspector, "documents", sa.Column("relational_context", JSONB(), nullable=True))

    # ========================================================================
    # Part 2: Chunk enrichment columns on document_chunks table (from r006_0001)
    # ========================================================================
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("summary", sa.Text(), nullable=True))
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("keywords", JSONB(), nullable=True))
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("topics", JSONB(), nullable=True))

    # ========================================================================
    # Part 3: Create document_queries table (from r006_0001)
    # ========================================================================
    if not table_exists(inspector, "document_queries"):
        op.create_table(
            "document_queries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("query_text", sa.Text(), nullable=False),
            sa.Column("query_embedding", Vector(384), nullable=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        )

    # ========================================================================
    # Part 4: Create document_participants table (from r006_0001)
    # ========================================================================
    if not table_exists(inspector, "document_participants"):
        op.create_table(
            "document_participants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("entity_id", sa.String(36), nullable=True, index=True),
            sa.Column("entity_type", sa.String(50), nullable=False),
            sa.Column("entity_name", sa.String(255), nullable=False, index=True),
            sa.Column("role", sa.String(50), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "document_id",
                "entity_name",
                "role",
                name="uq_document_participants_doc_entity_role",
            ),
        )
        op.create_index(
            "ix_document_participants_entity_type",
            "document_participants",
            ["entity_type"],
        )

    # ========================================================================
    # Part 5: Create document_projects table (from r006_0001)
    # ========================================================================
    if not table_exists(inspector, "document_projects"):
        op.create_table(
            "document_projects",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("project_name", sa.String(255), nullable=False, index=True),
            sa.Column("association_strength", sa.Float(), nullable=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
            sa.UniqueConstraint("document_id", "project_name", name="uq_document_projects_doc_project"),
        )

    # ========================================================================
    # Part 6: Vector and GIN indexes (from r006_0001)
    # ========================================================================
    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if pgvector_available:
        if not index_exists(inspector, "documents", "ix_documents_synopsis_embedding"):
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_documents_synopsis_embedding
                ON documents USING ivfflat (synopsis_embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )

        if not index_exists(inspector, "document_queries", "ix_document_queries_query_embedding"):
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_document_queries_query_embedding
                ON document_queries USING ivfflat (query_embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )

    if not index_exists(inspector, "documents", "ix_documents_capability_manifest"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_documents_capability_manifest
            ON documents USING gin (capability_manifest)
            """
        )

    if not index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_document_chunks_keywords
            ON document_chunks USING gin (keywords)
            """
        )

    if not index_exists(inspector, "document_chunks", "ix_document_chunks_topics"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_document_chunks_topics
            ON document_chunks USING gin (topics)
            """
        )

    if not index_exists(inspector, "documents", "ix_documents_relational_context"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_documents_relational_context
            ON documents USING gin (relational_context)
            """
        )

    # ========================================================================
    # Part 7: Remove cost fields from llm_providers (from r006_0001)
    # ========================================================================
    drop_column_if_exists(inspector, "llm_providers", "cost_per_input_token")
    drop_column_if_exists(inspector, "llm_providers", "cost_per_output_token")

    # ========================================================================
    # Part 8: Create experiences table (from r006_0002)
    # ========================================================================
    if not table_exists(inspector, "experiences"):
        op.create_table(
            "experiences",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("name", sa.String(100), nullable=False, index=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "created_by",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("visibility", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("trigger_type", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("trigger_config", JSONB(), nullable=True),
            sa.Column("include_previous_run", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column(
                "model_configuration_id",
                sa.String(36),
                sa.ForeignKey("model_configurations.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "prompt_id",
                sa.String(36),
                sa.ForeignKey("prompts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("inline_prompt_template", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_active_version", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "parent_version_id",
                sa.String(36),
                sa.ForeignKey("experiences.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("max_run_seconds", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("token_budget", sa.Integer(), nullable=True),
            sa.Column("next_run_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("last_run_at", TIMESTAMP(timezone=True), nullable=True),
        )
        op.create_index("ix_experiences_visibility", "experiences", ["visibility"])
        op.create_index("ix_experiences_active_version", "experiences", ["is_active_version"])
        op.create_index("ix_experiences_next_run_at", "experiences", ["next_run_at"])
        op.create_index("ix_experiences_model_configuration_id", "experiences", ["model_configuration_id"])

    # ========================================================================
    # Part 9: Create experience_steps table (from r006_0002)
    # ========================================================================
    if not table_exists(inspector, "experience_steps"):
        op.create_table(
            "experience_steps",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column(
                "experience_id",
                sa.String(36),
                sa.ForeignKey("experiences.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("order", sa.Integer(), nullable=False),
            sa.Column("step_key", sa.String(50), nullable=False),
            sa.Column("step_type", sa.String(30), nullable=False, server_default="plugin"),
            sa.Column("plugin_name", sa.String(100), nullable=True),
            sa.Column("plugin_op", sa.String(100), nullable=True),
            sa.Column(
                "knowledge_base_id",
                sa.String(36),
                sa.ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("kb_query_template", sa.Text(), nullable=True),
            sa.Column("params_template", JSONB(), nullable=True),
            sa.Column("condition_template", sa.Text(), nullable=True),
            sa.Column("required_scopes", JSONB(), nullable=True),
            sa.UniqueConstraint("experience_id", "step_key", name="uq_experience_steps_experience_step_key"),
        )

    # ========================================================================
    # Part 10: Create experience_runs table (from r006_0002)
    # ========================================================================
    if not table_exists(inspector, "experience_runs"):
        op.create_table(
            "experience_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
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
            sa.Column(
                "previous_run_id",
                sa.String(36),
                sa.ForeignKey("experience_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("model_configuration_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
            sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("finished_at", TIMESTAMP(timezone=True), nullable=True),
            sa.Column("step_states", JSONB(), nullable=True),
            sa.Column("input_params", JSONB(), nullable=True),
            sa.Column("step_outputs", JSONB(), nullable=True),
            sa.Column("result_content", sa.Text(), nullable=True),
            sa.Column("result_metadata", JSONB(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("error_details", JSONB(), nullable=True),
        )
        op.create_index(
            "ix_experience_runs_experience_user",
            "experience_runs",
            ["experience_id", "user_id"],
        )
        op.create_index(
            "ix_experience_runs_experience_user_finished",
            "experience_runs",
            ["experience_id", "user_id", "finished_at"],
        )

    # ========================================================================
    # Part 11: Add is_favorite to conversations (from r006_0003)
    # ========================================================================
    if not column_exists(inspector, "conversations", "is_favorite"):
        op.add_column(
            "conversations",
            sa.Column(
                "is_favorite",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        )

    if not index_exists(inspector, "conversations", "ix_conversations_is_favorite"):
        op.create_index(
            "ix_conversations_is_favorite",
            "conversations",
            ["is_favorite"],
        )

    # ========================================================================
    # Part 12: Migrate google_id to ProviderIdentity (from r006_0004)
    # ========================================================================
    if column_exists(inspector, "users", "google_id"):
        # Migrate google_id values to ProviderIdentity
        users_with_google_id = conn.execute(
            text("""
                SELECT id, google_id, email, name, picture_url
                FROM users
                WHERE google_id IS NOT NULL
            """)
        ).fetchall()

        for user in users_with_google_id:
            existing = conn.execute(
                text("""
                    SELECT id FROM provider_identities
                    WHERE user_id = :user_id
                    AND provider_key = 'google'
                    AND account_id = :account_id
                """),
                {"user_id": user.id, "account_id": user.google_id},
            ).fetchone()

            if not existing:
                conn.execute(
                    text("""
                        INSERT INTO provider_identities
                        (id, user_id, provider_key, account_id, primary_email, display_name, avatar_url, created_at, updated_at)
                        VALUES (:id, :user_id, 'google', :account_id, :email, :name, :avatar_url, NOW(), NOW())
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user.id,
                        "account_id": user.google_id,
                        "email": user.email,
                        "name": user.name,
                        "avatar_url": user.picture_url,
                    },
                )

        # Drop index if it exists
        if index_exists(inspector, "users", "ix_users_google_id"):
            op.drop_index("ix_users_google_id", "users")

        # Drop google_id column
        op.drop_column("users", "google_id")


def downgrade() -> None:
    """Revert all sixth release schema changes.

    All operations are idempotent — safe to run on databases that have already
    been partially downgraded.

    Reversal order is the inverse of upgrade to respect foreign key dependencies.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 12 (reverse): Restore google_id column and migrate data back
    # ========================================================================
    if not column_exists(inspector, "users", "google_id"):
        op.add_column("users", sa.Column("google_id", sa.String(), nullable=True))
        # Re-inspect after adding column
        inspector = sa.inspect(conn)

    # Restore google_id values from ProviderIdentity (only where NULL)
    google_identities = conn.execute(
        text("""
            SELECT pi.id as identity_id, pi.user_id, pi.account_id
            FROM provider_identities pi
            JOIN users u ON u.id = pi.user_id
            WHERE pi.provider_key = 'google'
            AND u.google_id IS NULL
        """)
    ).fetchall()

    restored_identity_ids = []
    for identity in google_identities:
        conn.execute(
            text("""
                UPDATE users
                SET google_id = :google_id
                WHERE id = :user_id
                AND google_id IS NULL
            """),
            {"user_id": identity.user_id, "google_id": identity.account_id},
        )
        restored_identity_ids.append(identity.identity_id)

    # Recreate unique index
    if not index_exists(inspector, "users", "ix_users_google_id"):
        op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    # Remove only the ProviderIdentity rows that were restored
    for identity_id in restored_identity_ids:
        conn.execute(
            text("""
                DELETE FROM provider_identities
                WHERE id = :id
            """),
            {"id": identity_id},
        )

    # ========================================================================
    # Part 11 (reverse): Remove is_favorite from conversations
    # ========================================================================
    if index_exists(inspector, "conversations", "ix_conversations_is_favorite"):
        op.drop_index("ix_conversations_is_favorite", "conversations")

    if column_exists(inspector, "conversations", "is_favorite"):
        op.drop_column("conversations", "is_favorite")

    # ========================================================================
    # Part 8-10 (reverse): Drop experience platform tables
    # ========================================================================
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user_finished")
    op.execute("DROP INDEX IF EXISTS ix_experience_runs_experience_user")
    op.execute("DROP INDEX IF EXISTS ix_experiences_visibility")
    op.execute("DROP INDEX IF EXISTS ix_experiences_active_version")
    op.execute("DROP INDEX IF EXISTS ix_experiences_next_run_at")
    op.execute("DROP INDEX IF EXISTS ix_experiences_model_configuration_id")

    drop_table_if_exists(inspector, "experience_runs")
    drop_table_if_exists(inspector, "experience_steps")
    drop_table_if_exists(inspector, "experiences")

    # ========================================================================
    # Part 7 (reverse): Restore cost fields on llm_providers
    # ========================================================================
    add_column_if_not_exists(
        inspector,
        "llm_providers",
        sa.Column("cost_per_input_token", sa.Numeric(precision=12, scale=10), nullable=True),
    )
    add_column_if_not_exists(
        inspector,
        "llm_providers",
        sa.Column("cost_per_output_token", sa.Numeric(precision=12, scale=10), nullable=True),
    )

    # ========================================================================
    # Part 1-6 (reverse): Drop document profiling indexes, tables, and columns
    # ========================================================================
    op.execute("DROP INDEX IF EXISTS ix_documents_relational_context")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_topics")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_keywords")
    op.execute("DROP INDEX IF EXISTS ix_documents_capability_manifest")
    op.execute("DROP INDEX IF EXISTS ix_document_queries_query_embedding")
    op.execute("DROP INDEX IF EXISTS ix_documents_synopsis_embedding")
    op.execute("DROP INDEX IF EXISTS ix_document_participants_entity_type")
    op.execute("DROP INDEX IF EXISTS ix_document_participants_entity_name")

    drop_table_if_exists(inspector, "document_projects")
    drop_table_if_exists(inspector, "document_participants")
    drop_table_if_exists(inspector, "document_queries")

    drop_column_if_exists(inspector, "document_chunks", "topics")
    drop_column_if_exists(inspector, "document_chunks", "keywords")
    drop_column_if_exists(inspector, "document_chunks", "summary")

    drop_column_if_exists(inspector, "documents", "relational_context")
    drop_column_if_exists(inspector, "documents", "profiling_error")
    drop_column_if_exists(inspector, "documents", "profiling_status")
    drop_column_if_exists(inspector, "documents", "capability_manifest")
    drop_column_if_exists(inspector, "documents", "document_type")
    drop_column_if_exists(inspector, "documents", "synopsis_embedding")
    drop_column_if_exists(inspector, "documents", "synopsis")
