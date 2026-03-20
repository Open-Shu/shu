"""Migration 008_0004: ParadeDB BM25 index + query chunk provenance

1. Drops the ``keywords`` JSONB column and its GIN index from
   ``document_chunks``, since keyword extraction is replaced by BM25
   full-text search on documents.  (SHU-644)

2. Creates a ParadeDB BM25 index on the ``documents`` table for true
   Okapi BM25 scoring via ``pdb.score()`` and the ``|||`` operator.
   Requires the ``pg_search`` extension.  (SHU-644)

3. Adds a nullable ``source_chunk_id`` FK column to ``document_queries``,
   linking each synthesized query to the chunk that inspired it.  Uses
   ``ON DELETE SET NULL`` so deleting a chunk doesn't cascade-delete
   queries.  (SHU-645)
"""

import sqlalchemy as sa
from alembic import op

from migrations.helpers import add_column_if_not_exists, drop_column_if_exists, index_exists

# revision identifiers, used by Alembic.
revision = "008_0004"
down_revision = "008_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Drop keywords GIN index from document_chunks (if it exists)
    if index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.drop_index("ix_document_chunks_keywords", table_name="document_chunks")

    # 2. Drop keywords column from document_chunks
    drop_column_if_exists(inspector, "document_chunks", "keywords")

    # 3. Create ParadeDB BM25 index on documents (requires pg_search extension)
    #    Uses English stemming and stopword removal on title and content.
    #    The ||| operator provides disjunctive (OR) matching; pdb.score(id)
    #    returns true Okapi BM25 scores.
    if not index_exists(inspector, "documents", "ix_documents_bm25"):
        op.execute(
            sa.text("""
                CREATE INDEX ix_documents_bm25 ON documents
                USING bm25 (
                    id,
                    (title::pdb.simple('stemmer=english', 'stopwords_language=english')),
                    (content::pdb.simple('stemmer=english', 'stopwords_language=english'))
                )
                WITH (key_field='id')
            """)
        )

    # 4. Add source_chunk_id FK to document_queries (SHU-645)
    add_column_if_not_exists(
        inspector,
        "document_queries",
        sa.Column(
            "source_chunk_id",
            sa.String,
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 5. Index on source_chunk_id for reverse lookups
    if not index_exists(inspector, "document_queries", "ix_document_queries_source_chunk_id"):
        op.create_index(
            "ix_document_queries_source_chunk_id",
            "document_queries",
            ["source_chunk_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop source_chunk_id index and column from document_queries (SHU-645)
    if index_exists(inspector, "document_queries", "ix_document_queries_source_chunk_id"):
        op.drop_index("ix_document_queries_source_chunk_id", table_name="document_queries")
    drop_column_if_exists(inspector, "document_queries", "source_chunk_id")

    # Drop ParadeDB BM25 index
    if index_exists(inspector, "documents", "ix_documents_bm25"):
        op.drop_index("ix_documents_bm25", table_name="documents")

    # Re-add keywords column to document_chunks
    add_column_if_not_exists(
        inspector,
        "document_chunks",
        sa.Column("keywords", sa.dialects.postgresql.JSONB, nullable=True),
    )

    # Re-create GIN index on keywords
    if not index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.create_index(
            "ix_document_chunks_keywords",
            "document_chunks",
            ["keywords"],
            postgresql_using="gin",
        )
