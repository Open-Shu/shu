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
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

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
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Helper to check if column exists
    def _column_exists(table_name: str, column_name: str) -> bool:
        try:
            return any(col["name"] == column_name for col in inspector.get_columns(table_name))
        except Exception:
            return False

    # Helper to check if table exists
    def _table_exists(table_name: str) -> bool:
        return table_name in inspector.get_table_names()

    # Helper to check if index exists
    def _index_exists(table_name: str, index_name: str) -> bool:
        try:
            indexes = inspector.get_indexes(table_name)
            return any(idx["name"] == index_name for idx in indexes)
        except Exception:
            return False

    # ========================================================================
    # Part 1: Add columns to documents table
    # ========================================================================
    if not _column_exists("documents", "synopsis"):
        op.add_column("documents", sa.Column("synopsis", sa.Text(), nullable=True))

    if not _column_exists("documents", "synopsis_embedding"):
        op.add_column("documents", sa.Column("synopsis_embedding", Vector(384), nullable=True))

    if not _column_exists("documents", "document_type"):
        op.add_column("documents", sa.Column("document_type", sa.String(50), nullable=True))

    if not _column_exists("documents", "capability_manifest"):
        op.add_column("documents", sa.Column("capability_manifest", JSONB(), nullable=True))

    if not _column_exists("documents", "profiling_status"):
        op.add_column(
            "documents",
            sa.Column(
                "profiling_status",
                sa.String(20),
                nullable=True,
                server_default="pending",
            ),
        )

    # SHU-355: Add relational_context column
    if not _column_exists("documents", "relational_context"):
        op.add_column("documents", sa.Column("relational_context", JSONB(), nullable=True))

    # ========================================================================
    # Part 2: Add columns to document_chunks table
    # ========================================================================
    if not _column_exists("document_chunks", "summary"):
        op.add_column("document_chunks", sa.Column("summary", sa.Text(), nullable=True))

    if not _column_exists("document_chunks", "keywords"):
        op.add_column("document_chunks", sa.Column("keywords", JSONB(), nullable=True))

    if not _column_exists("document_chunks", "topics"):
        op.add_column("document_chunks", sa.Column("topics", JSONB(), nullable=True))

    # ========================================================================
    # Part 3: Create document_queries table
    # ========================================================================
    if not _table_exists("document_queries"):
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
    if not _table_exists("document_participants"):
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
            sa.Column("entity_name", sa.String(255), nullable=False),
            sa.Column("role", sa.String(50), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "document_id", "entity_name", "role",
                name="uq_document_participants_doc_entity_role"
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
    if not _table_exists("document_projects"):
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
            sa.UniqueConstraint(
                "document_id", "project_name",
                name="uq_document_projects_doc_project"
            ),
        )

    # ========================================================================
    # Part 6: Create indexes
    # ========================================================================
    # Vector index for synopsis_embedding (ivfflat for approximate nearest neighbor)
    if not _index_exists("documents", "ix_documents_synopsis_embedding"):
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
    if not _index_exists("document_queries", "ix_document_queries_query_embedding"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_document_queries_query_embedding
            ON document_queries USING ivfflat (query_embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )

    # GIN indexes for JSONB columns (enable efficient containment queries)
    if not _index_exists("documents", "ix_documents_capability_manifest"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_documents_capability_manifest
            ON documents USING gin (capability_manifest)
            """
        )

    if not _index_exists("document_chunks", "ix_document_chunks_keywords"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_document_chunks_keywords
            ON document_chunks USING gin (keywords)
            """
        )

    if not _index_exists("document_chunks", "ix_document_chunks_topics"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_document_chunks_topics
            ON document_chunks USING gin (topics)
            """
        )

    # GIN index for relational_context (SHU-355)
    if not _index_exists("documents", "ix_documents_relational_context"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_documents_relational_context
            ON documents USING gin (relational_context)
            """
        )


def downgrade() -> None:
    # Drop SHU-355 tables first (reverse order of creation)
    op.drop_table("document_projects")
    op.drop_table("document_participants")

    # Drop SHU-342 indexes
    op.execute("DROP INDEX IF EXISTS ix_documents_relational_context")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_topics")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_keywords")
    op.execute("DROP INDEX IF EXISTS ix_documents_capability_manifest")
    op.execute("DROP INDEX IF EXISTS ix_document_queries_query_embedding")
    op.execute("DROP INDEX IF EXISTS ix_documents_synopsis_embedding")
    op.execute("DROP INDEX IF EXISTS ix_document_participants_entity_type")

    # Drop document_queries table
    op.drop_table("document_queries")

    # Drop columns from document_chunks
    op.drop_column("document_chunks", "topics")
    op.drop_column("document_chunks", "keywords")
    op.drop_column("document_chunks", "summary")

    # Drop columns from documents (SHU-355 + SHU-342)
    op.drop_column("documents", "relational_context")
    op.drop_column("documents", "profiling_status")
    op.drop_column("documents", "capability_manifest")
    op.drop_column("documents", "document_type")
    op.drop_column("documents", "synopsis_embedding")
    op.drop_column("documents", "synopsis")
