"""Migration r006_0001: Document Profile Schema

This migration adds the schema for Shu RAG document profiling (SHU-342).

Changes:
- Adds synopsis, synopsis_embedding, document_type, capability_manifest, profiling_status
  columns to the documents table
- Adds summary, keywords, topics columns to the document_chunks table
- Creates document_queries table for synthesized queries
- Adds vector indexes for synopsis_embedding and query_embedding
- Adds GIN indexes for capability_manifest, keywords, and topics JSONB columns
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
    # Part 4: Create indexes
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


def downgrade() -> None:
    # Drop indexes first
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_topics")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_keywords")
    op.execute("DROP INDEX IF EXISTS ix_documents_capability_manifest")
    op.execute("DROP INDEX IF EXISTS ix_document_queries_query_embedding")
    op.execute("DROP INDEX IF EXISTS ix_documents_synopsis_embedding")

    # Drop document_queries table
    op.drop_table("document_queries")

    # Drop columns from document_chunks
    op.drop_column("document_chunks", "topics")
    op.drop_column("document_chunks", "keywords")
    op.drop_column("document_chunks", "summary")

    # Drop columns from documents
    op.drop_column("documents", "profiling_status")
    op.drop_column("documents", "capability_manifest")
    op.drop_column("documents", "document_type")
    op.drop_column("documents", "synopsis_embedding")
    op.drop_column("documents", "synopsis")

