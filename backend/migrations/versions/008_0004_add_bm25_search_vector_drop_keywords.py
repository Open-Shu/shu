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

4. Makes ``rag_minimum_query_words`` on ``knowledge_bases`` nullable
   with no default, so the ConfigurationManager cascade correctly falls
   through to the global ``SHU_RAG_MINIMUM_QUERY_WORDS_DEFAULT``.
   Previously the column had ``default=3, nullable=False``, which baked
   the value at KB creation time and ignored the env setting.  (SHU-647)
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

    # 3. Create ParadeDB BM25 index on documents (if pg_search extension is available)
    #    Uses English stemming and stopword removal on title and content.
    #    The ||| operator provides disjunctive (OR) matching; pdb.score(id)
    #    returns true Okapi BM25 scores.
    #    When pg_search is not installed the index is skipped and BM25Surface
    #    degrades gracefully at query time (returns empty results).
    has_pg_search = conn.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")
    ).scalar()

    if not has_pg_search:
        print("NOTE: pg_search extension not available — skipping BM25 index creation. "
              "BM25 retrieval surface will be inactive.")
    elif not index_exists(inspector, "documents", "ix_documents_bm25"):
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

    # 6. Make rag_minimum_query_words nullable with no default (SHU-647)
    #    Drop the column default and NOT NULL constraint so the
    #    ConfigurationManager cascade falls through to the global setting.
    #    Set existing rows to NULL so they also use the global default.
    op.alter_column(
        "knowledge_bases",
        "rag_minimum_query_words",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )
    op.execute(sa.text("UPDATE knowledge_bases SET rag_minimum_query_words = NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Drop source_chunk_id index and column from document_queries (SHU-645)
    if index_exists(inspector, "document_queries", "ix_document_queries_source_chunk_id"):
        op.drop_index("ix_document_queries_source_chunk_id", table_name="document_queries")
    drop_column_if_exists(inspector, "document_queries", "source_chunk_id")

    # Restore rag_minimum_query_words NOT NULL with default 3
    op.execute(
        sa.text("UPDATE knowledge_bases SET rag_minimum_query_words = 3 WHERE rag_minimum_query_words IS NULL")
    )
    op.alter_column(
        "knowledge_bases",
        "rag_minimum_query_words",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("3"),
    )

    # Drop ParadeDB BM25 index (may not exist if pg_search was unavailable)
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
