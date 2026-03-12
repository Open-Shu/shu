"""Unit tests for retrieval surfaces."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from shu.core.vector_store import VectorSearchResult
from shu.services.retrieval.surfaces import ChunkVectorSurface, SynopsisMatchSurface


class TestChunkVectorSurface:
    """Tests for ChunkVectorSurface."""

    def _make_surface(self, mock_results: list[VectorSearchResult] | None = None):
        """Create a ChunkVectorSurface with mocked VectorStore."""
        mock_vector_store = MagicMock()
        mock_vector_store.search = AsyncMock(return_value=mock_results or [])
        return ChunkVectorSurface(mock_vector_store), mock_vector_store

    @pytest.mark.asyncio
    async def test_search_returns_chunk_hits(self):
        """search() should return chunk hits with normalized scores."""
        chunk_id = str(uuid4())
        mock_results = [
            VectorSearchResult(id=chunk_id, score=0.92),
        ]
        surface, mock_vs = self._make_surface(mock_results)
        mock_db = AsyncMock()

        result = await surface.search(
            query_text="test query",
            query_vector=[0.1] * 1024,
            keyword_terms=["test"],
            kb_id=uuid4(),
            limit=10,
            threshold=0.5,
            db=mock_db,
        )

        assert result.surface_name == "chunk_vector"
        assert len(result.hits) == 1
        assert result.hits[0].id_type == "chunk"
        assert result.hits[0].score == 0.92
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_search_calls_vector_store_correctly(self):
        """search() should call VectorStore with correct parameters."""
        surface, mock_vs = self._make_surface([])
        mock_db = AsyncMock()
        kb_id = uuid4()

        await surface.search(
            query_text="test query",
            query_vector=[0.5] * 1024,
            keyword_terms=["test"],
            kb_id=kb_id,
            limit=20,
            threshold=0.7,
            db=mock_db,
        )

        mock_vs.search.assert_called_once()
        call_kwargs = mock_vs.search.call_args.kwargs
        assert call_kwargs["collection"] == "chunks"
        assert call_kwargs["limit"] == 20
        assert call_kwargs["threshold"] == 0.7
        assert call_kwargs["filters"]["knowledge_base_id"] == str(kb_id)

    @pytest.mark.asyncio
    async def test_search_handles_empty_results(self):
        """search() should handle empty results gracefully."""
        surface, _ = self._make_surface([])
        mock_db = AsyncMock()

        result = await surface.search(
            query_text="no matches",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=uuid4(),
            db=mock_db,
        )

        assert result.surface_name == "chunk_vector"
        assert len(result.hits) == 0

    def test_surface_has_correct_name(self):
        """ChunkVectorSurface has the expected name."""
        surface, _ = self._make_surface()
        assert surface.name == "chunk_vector"


class TestSynopsisMatchSurface:
    """Tests for SynopsisMatchSurface."""

    def _make_surface(self, mock_results: list[VectorSearchResult] | None = None):
        """Create a SynopsisMatchSurface with mocked VectorStore."""
        mock_vector_store = MagicMock()
        mock_vector_store.search = AsyncMock(return_value=mock_results or [])
        return SynopsisMatchSurface(mock_vector_store), mock_vector_store

    @pytest.mark.asyncio
    async def test_search_returns_document_hits(self):
        """search() should return document hits from synopsis collection."""
        doc_id = str(uuid4())
        mock_results = [
            VectorSearchResult(id=doc_id, score=0.78),
        ]
        surface, _ = self._make_surface(mock_results)
        mock_db = AsyncMock()

        result = await surface.search(
            query_text="test query",
            query_vector=[0.1] * 1024,
            keyword_terms=["test"],
            kb_id=uuid4(),
            limit=10,
            threshold=0.5,
            db=mock_db,
        )

        assert result.surface_name == "synopsis_match"
        assert len(result.hits) == 1
        assert result.hits[0].id_type == "document"
        assert result.hits[0].score == 0.78

    @pytest.mark.asyncio
    async def test_search_uses_synopses_collection(self):
        """search() should query the synopses collection."""
        surface, mock_vs = self._make_surface([])
        mock_db = AsyncMock()

        await surface.search(
            query_text="test",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=uuid4(),
            db=mock_db,
        )

        call_kwargs = mock_vs.search.call_args.kwargs
        assert call_kwargs["collection"] == "synopses"

    def test_surface_has_correct_name(self):
        """SynopsisMatchSurface has the expected name."""
        surface, _ = self._make_surface()
        assert surface.name == "synopsis_match"
