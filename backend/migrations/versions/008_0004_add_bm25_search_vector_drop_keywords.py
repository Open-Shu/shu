"""Migration 008_0004: Add BM25 search_vector to documents, drop keywords from document_chunks

Adds a ``search_vector`` tsvector column to the ``documents`` table with a GIN
index for Postgres full-text search (BM25-family ranking via ``ts_rank``).
Backfills the column from ``title`` and ``content``.

Drops the ``keywords`` JSONB column and its GIN index from ``document_chunks``,
since keyword extraction is replaced by native full-text search on documents.

Part of SHU-644: Replace KeywordMatch with BM25 Retrieval Surface.
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

    # 1. Add search_vector tsvector column to documents
    add_column_if_not_exists(
        inspector,
        "documents",
        sa.Column("search_vector", sa.dialects.postgresql.TSVECTOR, nullable=True),
    )

    # 2. Create GIN index on search_vector
    if not index_exists(inspector, "documents", "ix_documents_search_vector"):
        op.create_index(
            "ix_documents_search_vector",
            "documents",
            ["search_vector"],
            postgresql_using="gin",
        )

    # 3. Backfill search_vector from title + content for existing documents
    op.execute(
        sa.text(
            "UPDATE documents SET search_vector = to_tsvector('english', "
            "coalesce(title, '') || ' ' || coalesce(content, '')) "
            "WHERE search_vector IS NULL"
        )
    )

    # 4. Create trigger to auto-populate search_vector on INSERT/UPDATE
    op.execute(
        sa.text("""
            CREATE OR REPLACE FUNCTION documents_search_vector_update() RETURNS trigger AS $$
            BEGIN
                NEW.search_vector := to_tsvector('english', coalesce(NEW.title, '') || ' ' || coalesce(NEW.content, ''));
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
    )
    op.execute(
        sa.text("""
            DROP TRIGGER IF EXISTS trig_documents_search_vector ON documents;
            CREATE TRIGGER trig_documents_search_vector
                BEFORE INSERT OR UPDATE OF title, content ON documents
                FOR EACH ROW
                EXECUTE FUNCTION documents_search_vector_update();
        """)
    )

    # 5. Drop keywords GIN index from document_chunks (if it exists)
    if index_exists(inspector, "document_chunks", "ix_document_chunks_keywords"):
        op.drop_index("ix_document_chunks_keywords", table_name="document_chunks")

    # 6. Drop keywords column from document_chunks
    drop_column_if_exists(inspector, "document_chunks", "keywords")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

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

    # Drop trigger and function
    op.execute(sa.text("DROP TRIGGER IF EXISTS trig_documents_search_vector ON documents;"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS documents_search_vector_update();"))

    # Drop search_vector index and column
    if index_exists(inspector, "documents", "ix_documents_search_vector"):
        op.drop_index("ix_documents_search_vector", table_name="documents")

    drop_column_if_exists(inspector, "documents", "search_vector")
