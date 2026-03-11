"""Unit tests for MultiSurfaceSearchService."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from shu.services.retrieval.multi_surface_search import MultiSurfaceSearchService
from shu.services.retrieval.protocol import FusedResult, SurfaceHit, SurfaceResult


class TestMultiSurfaceSearchService:
    """Tests for MultiSurfaceSearchService."""

    def _make_mock_surface(self, name: str, hits: list[SurfaceHit] | None = None):
        """Create a mock retrieval surface."""
        surface = MagicMock()
        surface.name = name
        surface.search = AsyncMock(
            return_value=SurfaceResult(
                surface_name=name,
                hits=hits or [],
                execution_time_ms=10.0,
            )
        )
        return surface

    def _make_service(
        self,
        surfaces=None,
        embedding_service=None,
        fusion_service=None,
    ):
        """Create a MultiSurfaceSearchService with mocked dependencies."""
        mock_embedding = embedding_service or MagicMock()
        if not embedding_service:
            mock_embedding.embed_query = AsyncMock(return_value=[0.1] * 1024)

        mock_fusion = fusion_service or MagicMock()
        if not fusion_service:
            mock_fusion.fuse = AsyncMock(return_value=[])

        return MultiSurfaceSearchService(
            surfaces=surfaces or [],
            embedding_service=mock_embedding,
            fusion_service=mock_fusion,
        )

    @pytest.mark.asyncio
    async def test_search_executes_all_surfaces(self):
        """search() should execute all configured surfaces."""
        surface1 = self._make_mock_surface("surface1")
        surface2 = self._make_mock_surface("surface2")
        service = self._make_service(surfaces=[surface1, surface2])
        mock_db = AsyncMock()

        await service.search("test query", uuid4(), keyword_terms=["test"], db=mock_db)

        surface1.search.assert_called_once()
        surface2.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_embeds_query(self):
        """search() should embed the query once."""
        mock_embedding = MagicMock()
        mock_embedding.embed_query = AsyncMock(return_value=[0.1] * 1024)
        service = self._make_service(embedding_service=mock_embedding)
        mock_db = AsyncMock()

        await service.search("test query", uuid4(), keyword_terms=["test"], db=mock_db)

        mock_embedding.embed_query.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_search_passes_results_to_fusion(self):
        """search() should pass surface results to fusion service."""
        doc_id = uuid4()
        surface = self._make_mock_surface(
            "test_surface",
            hits=[SurfaceHit(id=doc_id, id_type="document", score=0.9)],
        )
        mock_fusion = MagicMock()
        mock_fusion.fuse = AsyncMock(
            return_value=[
                FusedResult(
                    document_id=doc_id,
                    document_title="Test",
                    final_score=0.9,
                    surface_scores={"test_surface": 0.9},
                    contributing_chunks=[],
                )
            ]
        )
        service = self._make_service(surfaces=[surface], fusion_service=mock_fusion)
        mock_db = AsyncMock()
        kb_id = uuid4()

        result = await service.search(
            "test", kb_id, keyword_terms=["test"], limit=5, threshold=0.5, db=mock_db
        )

        mock_fusion.fuse.assert_called_once()
        call_kwargs = mock_fusion.fuse.call_args.kwargs
        assert call_kwargs["limit"] == 5
        assert call_kwargs["threshold"] == 0.5
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_handles_surface_exceptions(self):
        """search() should handle surface exceptions gracefully."""
        good_surface = self._make_mock_surface("good_surface")
        bad_surface = MagicMock()
        bad_surface.name = "bad_surface"
        bad_surface.search = AsyncMock(side_effect=Exception("Surface error"))

        mock_fusion = MagicMock()
        mock_fusion.fuse = AsyncMock(return_value=[])

        service = self._make_service(
            surfaces=[good_surface, bad_surface],
            fusion_service=mock_fusion,
        )
        mock_db = AsyncMock()

        # Should not raise, should log warning and continue
        await service.search("test", uuid4(), keyword_terms=["test"], db=mock_db)

        # Fusion should still be called with results from good surface
        mock_fusion.fuse.assert_called_once()
        call_args = mock_fusion.fuse.call_args[0][0]
        assert len(call_args) == 1  # Only good surface result

    @pytest.mark.asyncio
    async def test_search_returns_empty_when_all_surfaces_fail(self):
        """search() should return empty list when all surfaces fail."""
        bad_surface = MagicMock()
        bad_surface.name = "bad_surface"
        bad_surface.search = AsyncMock(side_effect=Exception("Fail"))

        service = self._make_service(surfaces=[bad_surface])
        mock_db = AsyncMock()

        result = await service.search("test", uuid4(), keyword_terms=["test"], db=mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_search_passes_keyword_terms_to_surfaces(self):
        """search() should pass keyword terms to surfaces."""
        surface = self._make_mock_surface("test_surface")
        service = self._make_service(surfaces=[surface])
        mock_db = AsyncMock()

        await service.search(
            "test query",
            uuid4(),
            keyword_terms=["budget", "marketing"],
            db=mock_db,
        )

        call_args = surface.search.call_args
        keyword_terms = call_args[0][2]  # Third positional arg
        assert "budget" in keyword_terms
        assert "marketing" in keyword_terms

    @pytest.mark.asyncio
    async def test_search_respects_timeout(self):
        """search() should timeout slow surfaces."""

        async def slow_search(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout
            return SurfaceResult(
                surface_name="slow", hits=[], execution_time_ms=10000
            )

        slow_surface = MagicMock()
        slow_surface.name = "slow_surface"
        slow_surface.search = slow_search

        service = self._make_service(surfaces=[slow_surface])
        service._timeout_ms = 100  # 100ms timeout
        mock_db = AsyncMock()

        # Should not hang, should handle timeout
        result = await service.search("test", uuid4(), keyword_terms=["test"], db=mock_db)

        # Result should be empty (surface timed out)
        assert result == []

    def test_default_configuration(self):
        """Service should have sensible default configuration."""
        mock_embedding = MagicMock()
        service = MultiSurfaceSearchService(
            surfaces=[],
            embedding_service=mock_embedding,
        )

        assert service._surface_limit == 50
        assert service._timeout_ms == 2000
