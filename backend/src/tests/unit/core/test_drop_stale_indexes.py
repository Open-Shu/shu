"""Unit tests for drop_orphaned_indexes in vector_store.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from shu.core.vector_store import drop_orphaned_indexes


class TestDropOrphanedIndexes:
    """Tests for the drop_orphaned_indexes function."""

    @pytest.mark.asyncio
    async def test_drops_indexes_with_wrong_dimension(self):
        """Indexes with a different dimension should be dropped."""
        mock_db = AsyncMock()

        # Simulate pg_indexes returning old 384-dim indexes
        select_result = MagicMock()
        select_result.fetchall.return_value = [
            ("ix_document_chunks_embedding_hnsw_384",),
            ("ix_documents_synopsis_embedding_hnsw_384",),
            ("ix_document_queries_query_embedding_hnsw_1024",),
        ]
        # First call = SELECT, subsequent calls = DROP
        mock_db.execute = AsyncMock(side_effect=[select_result, MagicMock(), MagicMock()])

        dropped = await drop_orphaned_indexes(mock_db, current_dimension=1024)

        assert dropped == [
            "ix_document_chunks_embedding_hnsw_384",
            "ix_documents_synopsis_embedding_hnsw_384",
        ]
        # 1 SELECT + 2 DROPs
        assert mock_db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_keeps_indexes_with_current_dimension(self):
        """Indexes matching the current dimension should not be dropped."""
        mock_db = AsyncMock()

        select_result = MagicMock()
        select_result.fetchall.return_value = [
            ("ix_document_chunks_embedding_hnsw_1024",),
            ("ix_documents_synopsis_embedding_hnsw_1024",),
        ]
        mock_db.execute = AsyncMock(return_value=select_result)

        dropped = await drop_orphaned_indexes(mock_db, current_dimension=1024)

        assert dropped == []
        # Only the SELECT query
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_no_indexes_found(self):
        """When no matching indexes exist, return empty list."""
        mock_db = AsyncMock()

        select_result = MagicMock()
        select_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=select_result)

        dropped = await drop_orphaned_indexes(mock_db, current_dimension=1024)

        assert dropped == []
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_dimensions(self):
        """Only indexes with non-matching dimensions are dropped."""
        mock_db = AsyncMock()

        select_result = MagicMock()
        select_result.fetchall.return_value = [
            ("ix_document_chunks_embedding_hnsw_384",),
            ("ix_document_chunks_embedding_hnsw_1024",),
            ("ix_documents_synopsis_embedding_hnsw_768",),
        ]
        mock_db.execute = AsyncMock(side_effect=[select_result, MagicMock(), MagicMock()])

        dropped = await drop_orphaned_indexes(mock_db, current_dimension=1024)

        assert dropped == [
            "ix_document_chunks_embedding_hnsw_384",
            "ix_documents_synopsis_embedding_hnsw_768",
        ]
