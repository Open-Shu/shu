"""Migration r006_0001: Document and Relational Schema

This migration adds the schema for Shu RAG document profiling (SHU-342) and
relational context (SHU-355).

SHU-342 Changes:
- Adds synopsis, synopsis_embedding, document_type, capability_manifest, profiling_status
  columns to the documents table
- Adds summary, keywords, topics columns to the document_chunks table
- Creates document_queries table for synthesized queries
- Adds vector indexes for synopsis_embedding and query_embedding
- Adds GIN indexes for capability_manifest, keywords, and topics JSONB columns

SHU-355 Changes:
- Adds relational_context JSONB column to the documents table
- Creates document_participants table for entity tracking
- Creates document_projects table for project associations
- Adds indexes for entity_id, entity_type, and project_name lookups
- Adds unique constraints to prevent duplicate entries

SHU-416 Changes:
- Removes cost_per_input_token and cost_per_output_token from llm_providers table
  (token costs belong on models, not providers)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from migrations.helpers import (
    add_column_if_not_exists,
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
revision = "r006_0001"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Apply the migration that updates document-related schema, adds new tables and indexes, and removes provider-level token cost columns.

    This migration performs the following changes in an idempotent manner:
    - Adds columns to the `documents` table: `synopsis`, `synopsis_embedding`, `document_type`, `capability_manifest`, `profiling_status` (default 'pending'), `profiling_error`, and `relational_context`.
    - Adds columns to the `document_chunks` table: `summary`, `keywords`, and `topics`.
    - Creates the `document_queries` table with query text/embedding and timestamps.
    - Creates the `document_participants` table with participant metadata, a uniqueness constraint on (document_id, entity_name, role), and an index on `entity_type`.
    - Creates the `document_projects` table with project association metadata and a uniqueness constraint on (document_id, project_name).
    - Creates vector indexes for embedding columns when the pgvector extension is available (ivfflat with cosine operator class).
    - Creates GIN indexes for JSONB columns (`capability_manifest`, `keywords`, `topics`, `relational_context`) to improve containment queries.
    - Removes `cost_per_input_token` and `cost_per_output_token` from `llm_providers`.

    All creations and drops are guarded by existence checks to ensure safe, repeatable application.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ========================================================================
    # Part 1: Add columns to documents table
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

    # SHU-355: Add relational_context column
    add_column_if_not_exists(inspector, "documents", sa.Column("relational_context", JSONB(), nullable=True))

    # ========================================================================
    # Part 2: Add columns to document_chunks table
    # ========================================================================
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("summary", sa.Text(), nullable=True))
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("keywords", JSONB(), nullable=True))
    add_column_if_not_exists(inspector, "document_chunks", sa.Column("topics", JSONB(), nullable=True))

    # ========================================================================
    # Part 3: Create document_queries table
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
    # Part 4: Create document_participants table (SHU-355)
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
        # Index on entity_type for filtering
        op.create_index(
            "ix_document_participants_entity_type",
            "document_participants",
            ["entity_type"],
        )

    # ========================================================================
    # Part 5: Create document_projects table (SHU-355)
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
    # Part 6: Create indexes
    # ========================================================================
    # Check if pgvector extension is available before creating vector indexes
    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if pgvector_available:
        # Vector index for synopsis_embedding (ivfflat for approximate nearest neighbor)
        if not index_exists(inspector, "documents", "ix_documents_synopsis_embedding"):
            # Create ivfflat index - requires sufficient rows for clustering
            # Using cosine distance operator class
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_documents_synopsis_embedding
                ON documents USING ivfflat (synopsis_embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )

        # Vector index for query_embedding
        if not index_exists(inspector, "document_queries", "ix_document_queries_query_embedding"):
            op.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_document_queries_query_embedding
                ON document_queries USING ivfflat (query_embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )

    # GIN indexes for JSONB columns (enable efficient containment queries)
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

    # GIN index for relational_context (SHU-355)
    if not index_exists(inspector, "documents", "ix_documents_relational_context"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_documents_relational_context
            ON documents USING gin (relational_context)
            """
        )

    # ========================================================================
    # Part 7: Remove cost fields from llm_providers (SHU-416)
    # Token costs belong on models, not providers
    # ========================================================================
    drop_column_if_exists(inspector, "llm_providers", "cost_per_input_token")
    drop_column_if_exists(inspector, "llm_providers", "cost_per_output_token")


def downgrade() -> None:
    """
    Revert the schema changes applied by the corresponding upgrade migration.

    Drops indexes created for document-related features, removes the document_projects, document_participants, and document_queries tables, and drops columns added to document_chunks and documents. Finally, restores the llm_providers columns `cost_per_input_token` and `cost_per_output_token` to revert the SHU-416 change.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop indexes first (IF EXISTS handles idempotency)
    op.execute("DROP INDEX IF EXISTS ix_documents_relational_context")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_topics")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_keywords")
    op.execute("DROP INDEX IF EXISTS ix_documents_capability_manifest")
    op.execute("DROP INDEX IF EXISTS ix_document_queries_query_embedding")
    op.execute("DROP INDEX IF EXISTS ix_documents_synopsis_embedding")
    op.execute("DROP INDEX IF EXISTS ix_document_participants_entity_type")
    op.execute("DROP INDEX IF EXISTS ix_document_participants_entity_name")

    # Drop SHU-355 tables (reverse order of creation)
    drop_table_if_exists(inspector, "document_projects")
    drop_table_if_exists(inspector, "document_participants")

    # Drop document_queries table
    drop_table_if_exists(inspector, "document_queries")

    # Drop columns from document_chunks
    drop_column_if_exists(inspector, "document_chunks", "topics")
    drop_column_if_exists(inspector, "document_chunks", "keywords")
    drop_column_if_exists(inspector, "document_chunks", "summary")

    # Drop columns from documents (SHU-355 + SHU-342)
    drop_column_if_exists(inspector, "documents", "relational_context")
    drop_column_if_exists(inspector, "documents", "profiling_error")
    drop_column_if_exists(inspector, "documents", "profiling_status")
    drop_column_if_exists(inspector, "documents", "capability_manifest")
    drop_column_if_exists(inspector, "documents", "document_type")
    drop_column_if_exists(inspector, "documents", "synopsis_embedding")
    drop_column_if_exists(inspector, "documents", "synopsis")

    # Re-add cost columns to llm_providers (SHU-416 rollback)
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
