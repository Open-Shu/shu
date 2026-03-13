"""Unit tests for retrieval surfaces."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shu.core.vector_store import VectorSearchResult
from shu.services.retrieval.surfaces import ChunkVectorSurface, QueryMatchSurface, SynopsisMatchSurface


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


class TestQueryMatchSurface:
    """Tests for QueryMatchSurface."""

    def _make_surface(
        self,
        mock_vector_results: list[VectorSearchResult] | None = None,
        mock_db_results: list[tuple[str, str, str]] | None = None,
    ):
        """Create a QueryMatchSurface with mocked VectorStore and db.

        Args:
            mock_vector_results: Results from VectorStore.search
            mock_db_results: Tuples of (query_id, document_id, query_text) for db query

        """
        mock_vector_store = MagicMock()
        mock_vector_store.search = AsyncMock(return_value=mock_vector_results or [])

        # Create mock db session that returns mock_db_results
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_db_results or []
        mock_db.execute = AsyncMock(return_value=mock_result)

        return QueryMatchSurface(mock_vector_store), mock_vector_store, mock_db

    @pytest.mark.asyncio
    async def test_search_returns_document_hits_with_matched_query(self):
        """search() should return document hits with matched_query in metadata."""
        query_id = str(uuid4())
        doc_id = str(uuid4())
        query_text = "What is the quarterly budget?"

        mock_vector_results = [VectorSearchResult(id=query_id, score=0.85)]
        mock_db_results = [(query_id, doc_id, query_text)]

        surface, _, mock_db = self._make_surface(mock_vector_results, mock_db_results)

        result = await surface.search(
            query_text="budget info",
            query_vector=[0.1] * 1024,
            keyword_terms=["budget"],
            kb_id=uuid4(),
            limit=10,
            threshold=0.5,
            db=mock_db,
        )

        assert result.surface_name == "query_match"
        assert len(result.hits) == 1
        assert result.hits[0].id_type == "document"
        assert result.hits[0].id == UUID(doc_id)
        assert result.hits[0].score == 0.85
        assert result.hits[0].metadata["matched_query"] == query_text

    @pytest.mark.asyncio
    async def test_search_aggregates_by_document_using_max_score(self):
        """search() should aggregate multiple queries per document using max score."""
        query_id_1 = str(uuid4())
        query_id_2 = str(uuid4())
        doc_id = str(uuid4())

        # Two queries from same document, different scores
        mock_vector_results = [
            VectorSearchResult(id=query_id_1, score=0.90),
            VectorSearchResult(id=query_id_2, score=0.70),
        ]
        mock_db_results = [
            (query_id_1, doc_id, "High scoring query"),
            (query_id_2, doc_id, "Lower scoring query"),
        ]

        surface, _, mock_db = self._make_surface(mock_vector_results, mock_db_results)

        result = await surface.search(
            query_text="test",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=uuid4(),
            limit=10,
            db=mock_db,
        )

        # Should return one document with max score
        assert len(result.hits) == 1
        assert result.hits[0].score == 0.90
        assert result.hits[0].metadata["matched_query"] == "High scoring query"

    @pytest.mark.asyncio
    async def test_search_handles_empty_results(self):
        """search() should handle empty vector search results gracefully."""
        surface, _, mock_db = self._make_surface([], [])

        result = await surface.search(
            query_text="no matches",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=uuid4(),
            db=mock_db,
        )

        assert result.surface_name == "query_match"
        assert len(result.hits) == 0
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_search_calls_vector_store_with_queries_collection(self):
        """search() should query the queries collection."""
        surface, mock_vs, mock_db = self._make_surface([], [])

        await surface.search(
            query_text="test",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=uuid4(),
            db=mock_db,
        )

        call_kwargs = mock_vs.search.call_args.kwargs
        assert call_kwargs["collection"] == "queries"

    @pytest.mark.asyncio
    async def test_search_fetches_more_results_for_aggregation(self):
        """search() should fetch limit*3 from vector store to allow for aggregation."""
        surface, mock_vs, mock_db = self._make_surface([], [])
        kb_id = uuid4()

        await surface.search(
            query_text="test",
            query_vector=[0.1] * 1024,
            keyword_terms=[],
            kb_id=kb_id,
            limit=10,
            db=mock_db,
        )

        call_kwargs = mock_vs.search.call_args.kwargs
        # Should request 3x the limit to allow for document aggregation
        assert call_kwargs["limit"] == 30

    def test_surface_has_correct_name(self):
        """QueryMatchSurface has the expected name."""
        surface, _, _ = self._make_surface()
        assert surface.name == "query_match"
