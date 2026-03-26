"""Unit tests for ScoreFusionService."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shu.services.retrieval.protocol import SurfaceHit, SurfaceResult
from shu.services.retrieval.score_fusion import ScoreFusionService


class TestScoreFusionService:
    """Tests for ScoreFusionService."""

    def _make_service(self, weights: dict[str, float] | None = None):
        """Create a ScoreFusionService with optional custom weights."""
        return ScoreFusionService(weights=weights)

    @pytest.mark.asyncio
    async def test_fuse_empty_results(self):
        """fuse() should return empty list for no surface results."""
        service = self._make_service()
        mock_db = AsyncMock()

        result = await service.fuse([], db=mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_fuse_single_surface_document_hits(self):
        """fuse() should handle single surface with document hits."""
        service = self._make_service({"synopsis_match": 1.0})
        mock_db = AsyncMock()

        doc_id = uuid4()
        surface_result = SurfaceResult(
            surface_name="synopsis_match",
            hits=[
                SurfaceHit(id=doc_id, id_type="document", score=0.85),
            ],
            execution_time_ms=10.0,
        )

        # Mock document metadata lookup (title, file_type, source_url, source_id, created_at)
        with patch.object(
            service, "_load_document_metadata", return_value={doc_id: ("Test Doc", "pdf", None, None, None)}
        ):
            with patch.object(service, "_load_chunk_details", return_value={}):
                result = await service.fuse([surface_result], db=mock_db)

        assert len(result) == 1
        assert result[0].document_id == doc_id
        assert result[0].document_title == "Test Doc"
        assert result[0].final_score == 0.85
        assert "synopsis_match" in result[0].surface_scores

    @pytest.mark.asyncio
    async def test_fuse_combines_scores_from_multiple_surfaces(self):
        """fuse() should combine scores from multiple surfaces with weights."""
        weights = {"chunk_vector": 0.6, "synopsis_match": 0.4}
        service = self._make_service(weights)
        mock_db = AsyncMock()

        doc_id = uuid4()
        chunk_id = uuid4()

        chunk_surface = SurfaceResult(
            surface_name="chunk_vector",
            hits=[SurfaceHit(id=chunk_id, id_type="chunk", score=0.9)],
            execution_time_ms=20.0,
        )
        synopsis_surface = SurfaceResult(
            surface_name="synopsis_match",
            hits=[SurfaceHit(id=doc_id, id_type="document", score=0.8)],
            execution_time_ms=15.0,
        )

        # Mock chunk -> document resolution
        with patch.object(
            service, "_resolve_chunk_documents", return_value={chunk_id: doc_id}
        ):
            with patch.object(
                service, "_load_document_metadata", return_value={doc_id: ("Combined Doc", "txt", None, None, None)}
            ):
                with patch.object(
                    service,
                    "_load_chunk_details",
                    return_value={chunk_id: (0, "Chunk content...", None, None, None)},
                ):
                    result = await service.fuse(
                        [chunk_surface, synopsis_surface], db=mock_db
                    )

        assert len(result) == 1
        assert result[0].document_id == doc_id
        # max * sqrt(mean/max): max=0.9, mean=0.85, agreement=0.944, score=0.874
        assert abs(result[0].final_score - 0.874) < 0.01

    @pytest.mark.asyncio
    async def test_fuse_respects_threshold(self):
        """fuse() should filter results below threshold."""
        service = self._make_service({"synopsis_match": 1.0})
        mock_db = AsyncMock()

        doc_id = uuid4()
        surface_result = SurfaceResult(
            surface_name="synopsis_match",
            hits=[SurfaceHit(id=doc_id, id_type="document", score=0.3)],
            execution_time_ms=10.0,
        )

        with patch.object(
            service, "_load_document_metadata", return_value={doc_id: ("Low Score Doc", "txt", None, None, None)}
        ):
            with patch.object(service, "_load_chunk_details", return_value={}):
                result = await service.fuse(
                    [surface_result], threshold=0.5, db=mock_db
                )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_fuse_respects_limit(self):
        """fuse() should limit number of results."""
        service = self._make_service({"synopsis_match": 1.0})
        mock_db = AsyncMock()

        # Create 5 document hits
        doc_ids = [uuid4() for _ in range(5)]
        hits = [
            SurfaceHit(id=did, id_type="document", score=0.9 - i * 0.1)
            for i, did in enumerate(doc_ids)
        ]
        surface_result = SurfaceResult(
            surface_name="synopsis_match",
            hits=hits,
            execution_time_ms=10.0,
        )

        metadata = {did: (f"Doc {i}", "txt", None, None, None) for i, did in enumerate(doc_ids)}
        with patch.object(service, "_load_document_metadata", return_value=metadata):
            with patch.object(service, "_load_chunk_details", return_value={}):
                result = await service.fuse(
                    [surface_result], limit=3, db=mock_db
                )

        assert len(result) == 3
        # Results should be sorted by score descending
        assert result[0].final_score > result[1].final_score > result[2].final_score

    @pytest.mark.asyncio
    async def test_fuse_tracks_contributing_chunks(self):
        """fuse() should track contributing chunks for each document."""
        service = self._make_service({"chunk_vector": 1.0})
        mock_db = AsyncMock()

        doc_id = uuid4()
        chunk1_id = uuid4()
        chunk2_id = uuid4()

        surface_result = SurfaceResult(
            surface_name="chunk_vector",
            hits=[
                SurfaceHit(id=chunk1_id, id_type="chunk", score=0.9),
                SurfaceHit(id=chunk2_id, id_type="chunk", score=0.85),
            ],
            execution_time_ms=20.0,
        )

        with patch.object(
            service,
            "_resolve_chunk_documents",
            return_value={chunk1_id: doc_id, chunk2_id: doc_id},
        ):
            with patch.object(
                service, "_load_document_metadata", return_value={doc_id: ("Test Doc", "txt", None, None, None)}
            ):
                with patch.object(
                    service,
                    "_load_chunk_details",
                    return_value={
                        chunk1_id: (0, "First chunk content...", "Summary 1", 0, 100),
                        chunk2_id: (1, "Second chunk content...", None, 100, 200),
                    },
                ):
                    result = await service.fuse([surface_result], db=mock_db)

        assert len(result) == 1
        assert len(result[0].contributing_chunks) == 2
        # Chunks should be sorted by score
        assert result[0].contributing_chunks[0].score == 0.9
        assert result[0].contributing_chunks[1].score == 0.85

    def test_make_snippet_truncates_long_content(self):
        """_make_snippet() should truncate content over 200 chars."""
        service = self._make_service()
        long_content = "x" * 300

        snippet = service._make_snippet(long_content)

        assert len(snippet) == 200
        assert snippet.endswith("...")

    def test_make_snippet_preserves_short_content(self):
        """_make_snippet() should preserve short content."""
        service = self._make_service()
        short_content = "This is short."

        snippet = service._make_snippet(short_content)

        assert snippet == short_content

    def test_default_weights_are_applied(self):
        """Service should use default weights when none provided."""
        service = self._make_service()

        assert "chunk_vector" in service._weights
        assert "synopsis_match" in service._weights

    def test_custom_weights_override_defaults(self):
        """Service should use custom weights when provided."""
        custom_weights = {"custom_surface": 0.5}
        service = self._make_service(custom_weights)

        assert service._weights == custom_weights
