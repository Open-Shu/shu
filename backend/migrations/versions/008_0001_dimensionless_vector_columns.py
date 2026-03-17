"""Migration 008_0001: Dimensionless vector columns, new default model, embedding status.

Migrates all Vector(384) columns to dimensionless vector columns so the schema
supports any embedding model dimension without DDL changes. Drops old IVFFlat
indexes — new HNSW indexes are created dynamically at runtime by
VectorStore.ensure_index() based on the configured embedding model's dimension.
Updates the default embedding model on knowledge_bases from all-MiniLM-L6-v2 to
Snowflake/snowflake-arctic-embed-l-v2.0.

Adds embedding_status and re_embedding_progress columns to knowledge_bases for
stale KB detection and re-embedding progress tracking (SHU-605).

Adds document_chunks.summary_embedding for chunk summary vector retrieval (SHU-632).

Renames knowledge_bases.rag_max_results to rag_max_chunks for clarity (SHU-631).

Columns altered:
- document_chunks.embedding
- documents.synopsis_embedding
- document_queries.query_embedding
- knowledge_bases.rag_max_results → rag_max_chunks

Columns added:
- knowledge_bases.embedding_status
- knowledge_bases.re_embedding_progress
- document_chunks.summary_embedding
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import index_exists

revision = "008_0001"
down_revision = "007"
branch_labels = None
depends_on = None

# Old IVFFlat indexes to drop
_OLD_INDEXES = [
    "idx_document_chunks_embedding",
    "ix_documents_synopsis_embedding",
    "ix_document_queries_query_embedding",
]

# Columns to alter
_VECTOR_COLUMNS = [
    ("document_chunks", "embedding"),
    ("documents", "synopsis_embedding"),
    ("document_queries", "query_embedding"),
]


def upgrade() -> None:
    """ALTER vector columns to dimensionless, drop IVFFlat indexes."""
    conn = op.get_bind()

    # Check pgvector availability
    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if not pgvector_available:
        return

    # 1. ALTER columns from vector(384) to vector (dimensionless)
    for table, column in _VECTOR_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector")

    # 2. Drop old IVFFlat indexes — new HNSW indexes are created at runtime
    #    by VectorStore.ensure_index() based on the embedding model's dimension.
    for index_name in _OLD_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 3. Update default embedding model on knowledge_bases (SHU-606)
    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'Snowflake/snowflake-arctic-embed-l-v2.0'"),
    )

    # 4. Add embedding status tracking columns (SHU-605)
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "embedding_status",
            sa.String(20),
            server_default=sa.text("'current'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column("re_embedding_progress", sa.JSON(), nullable=True),
    )

    # 5. Add summary_embedding column for chunk summary vector retrieval (SHU-632)
    op.execute(
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS summary_embedding vector"
    )

    # 6. Rename rag_max_results → rag_max_chunks (SHU-631, idempotent)
    has_old_col = conn.execute(
        sa.text(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.columns"
            "  WHERE table_name = 'knowledge_bases' AND column_name = 'rag_max_results'"
            ")"
        )
    ).scalar()
    if has_old_col:
        op.alter_column("knowledge_bases", "rag_max_results", new_column_name="rag_max_chunks")


def downgrade() -> None:
    """Restore Vector(384) columns and IVFFlat indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if not pgvector_available:
        return

    # 1. Rename rag_max_chunks back to rag_max_results (SHU-631, idempotent)
    has_new_col = conn.execute(
        sa.text(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.columns"
            "  WHERE table_name = 'knowledge_bases' AND column_name = 'rag_max_chunks'"
            ")"
        )
    ).scalar()
    if has_new_col:
        op.alter_column("knowledge_bases", "rag_max_chunks", new_column_name="rag_max_results")

    # 2. Drop summary_embedding column (SHU-632)
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS summary_embedding")

    # 3. Drop embedding status columns (SHU-605)
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS re_embedding_progress")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS embedding_status")

    # 4. ALTER columns back to vector(384)
    for table, column in _VECTOR_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector(384)")

    # 5. Restore original embedding model default
    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'sentence-transformers/all-MiniLM-L6-v2'"),
    )

    # 6. Recreate original IVFFlat indexes
    if not index_exists(inspector, "document_chunks", "idx_document_chunks_embedding"):
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
            ON document_chunks USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )

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
