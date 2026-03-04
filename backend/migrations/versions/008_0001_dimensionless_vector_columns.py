"""Migration 008_0001: Dimensionless vector columns, HNSW indexes, new default model.

Migrates all Vector(384) columns to dimensionless vector columns so the schema
supports any embedding model dimension without DDL changes. Replaces IVFFlat
indexes with HNSW dimension-scoped partial indexes. Updates the default
embedding model on knowledge_bases from all-MiniLM-L6-v2 to
Snowflake/snowflake-arctic-embed-l-v2.0.

Columns altered:
- document_chunks.embedding
- documents.synopsis_embedding
- document_queries.query_embedding

Note: HNSW indexes are capped at 2,000 dimensions for the `vector` type.
If a future model exceeds this (e.g., OpenAI text-embedding-3-large at 3,072),
the column/index type would need to use `halfvec` instead.
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
    ("idx_document_chunks_embedding", "document_chunks"),
    ("ix_documents_synopsis_embedding", "documents"),
    ("ix_document_queries_query_embedding", "document_queries"),
]

# New HNSW indexes to create (dimension-scoped partial indexes)
_NEW_INDEXES = [
    (
        "ix_document_chunks_embedding_hnsw_384",
        "document_chunks",
        "embedding",
    ),
    (
        "ix_documents_synopsis_embedding_hnsw_384",
        "documents",
        "synopsis_embedding",
    ),
    (
        "ix_document_queries_query_embedding_hnsw_384",
        "document_queries",
        "query_embedding",
    ),
]

# Columns to alter
_VECTOR_COLUMNS = [
    ("document_chunks", "embedding"),
    ("documents", "synopsis_embedding"),
    ("document_queries", "query_embedding"),
]


def upgrade() -> None:
    """ALTER vector columns to dimensionless, replace IVFFlat with HNSW."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check pgvector availability
    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if not pgvector_available:
        return

    # 1. ALTER columns from vector(384) to vector (dimensionless)
    for table, column in _VECTOR_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector")

    # 2. Drop old IVFFlat indexes
    for index_name, table_name in _OLD_INDEXES:
        if index_exists(inspector, table_name, index_name):
            op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 3. Create new HNSW dimension-scoped partial indexes
    for index_name, table_name, column_name in _NEW_INDEXES:
        if not index_exists(inspector, table_name, index_name):
            op.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {index_name}
                ON {table_name} USING hnsw (({column_name}::vector(384)) vector_cosine_ops)
                WHERE vector_dims({column_name}) = 384
                """
            )

    # 4. Update default embedding model on knowledge_bases (SHU-606)
    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'Snowflake/snowflake-arctic-embed-l-v2.0'"),
    )


def downgrade() -> None:
    """Restore Vector(384) columns and IVFFlat indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    pgvector_available = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    ).scalar()

    if not pgvector_available:
        return

    # 1. Drop HNSW indexes
    for index_name, table_name, _column_name in _NEW_INDEXES:
        if index_exists(inspector, table_name, index_name):
            op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 2. ALTER columns back to vector(384)
    for table, column in _VECTOR_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE vector(384)")

    # 3. Restore original embedding model default
    op.alter_column(
        "knowledge_bases",
        "embedding_model",
        server_default=sa.text("'sentence-transformers/all-MiniLM-L6-v2'"),
    )

    # 4. Recreate original IVFFlat indexes
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
