"""Unit tests for retrieval protocol and types."""

from uuid import uuid4

import pytest

from shu.services.retrieval.protocol import (
    ContributingChunk,
    FusedResult,
    SurfaceHit,
    SurfaceResult,
)


class TestSurfaceHit:
    """Tests for SurfaceHit dataclass."""

    def test_create_chunk_hit(self):
        """SurfaceHit can represent a chunk hit."""
        chunk_id = uuid4()
        hit = SurfaceHit(
            id=chunk_id,
            id_type="chunk",
            score=0.85,
            metadata={"matched_terms": ["test"]},
        )

        assert hit.id == chunk_id
        assert hit.id_type == "chunk"
        assert hit.score == 0.85
        assert hit.metadata == {"matched_terms": ["test"]}

    def test_create_document_hit(self):
        """SurfaceHit can represent a document hit."""
        doc_id = uuid4()
        hit = SurfaceHit(
            id=doc_id,
            id_type="document",
            score=0.72,
        )

        assert hit.id == doc_id
        assert hit.id_type == "document"
        assert hit.score == 0.72
        assert hit.metadata == {}

    def test_hit_is_frozen(self):
        """SurfaceHit is immutable."""
        hit = SurfaceHit(id=uuid4(), id_type="chunk", score=0.5)
        with pytest.raises(AttributeError):
            hit.score = 0.9


class TestSurfaceResult:
    """Tests for SurfaceResult dataclass."""

    def test_create_result_with_hits(self):
        """SurfaceResult holds hits and timing info."""
        hits = [
            SurfaceHit(id=uuid4(), id_type="chunk", score=0.9),
            SurfaceHit(id=uuid4(), id_type="chunk", score=0.8),
        ]
        result = SurfaceResult(
            surface_name="chunk_vector",
            hits=hits,
            execution_time_ms=45.2,
        )

        assert result.surface_name == "chunk_vector"
        assert len(result.hits) == 2
        assert result.execution_time_ms == 45.2

    def test_create_empty_result(self):
        """SurfaceResult can have empty hits."""
        result = SurfaceResult(
            surface_name="synopsis_match",
            hits=[],
            execution_time_ms=12.0,
        )

        assert result.surface_name == "synopsis_match"
        assert len(result.hits) == 0


class TestContributingChunk:
    """Tests for ContributingChunk dataclass."""

    def test_create_with_summary(self):
        """ContributingChunk can include summary."""
        chunk = ContributingChunk(
            chunk_id=uuid4(),
            chunk_index=3,
            surface="chunk_vector",
            score=0.88,
            snippet="This is a test snippet...",
            summary="Summary of the chunk content.",
        )

        assert chunk.chunk_index == 3
        assert chunk.surface == "chunk_vector"
        assert chunk.score == 0.88
        assert chunk.summary == "Summary of the chunk content."

    def test_create_without_summary(self):
        """ContributingChunk works without summary."""
        chunk = ContributingChunk(
            chunk_id=uuid4(),
            chunk_index=0,
            surface="synopsis_match",
            score=0.75,
            snippet="Snippet text...",
        )

        assert chunk.summary is None


class TestFusedResult:
    """Tests for FusedResult dataclass."""

    def test_create_fused_result(self):
        """FusedResult aggregates document-level results."""
        doc_id = uuid4()
        chunks = [
            ContributingChunk(
                chunk_id=uuid4(),
                chunk_index=1,
                surface="chunk_vector",
                score=0.9,
                snippet="First chunk...",
            ),
            ContributingChunk(
                chunk_id=uuid4(),
                chunk_index=2,
                surface="chunk_vector",
                score=0.85,
                snippet="Second chunk...",
            ),
        ]

        result = FusedResult(
            document_id=doc_id,
            document_title="Test Document",
            final_score=0.87,
            surface_scores={"chunk_vector": 0.9, "synopsis_match": 0.75},
            contributing_chunks=chunks,
        )

        assert result.document_id == doc_id
        assert result.document_title == "Test Document"
        assert result.final_score == 0.87
        assert len(result.surface_scores) == 2
        assert len(result.contributing_chunks) == 2
