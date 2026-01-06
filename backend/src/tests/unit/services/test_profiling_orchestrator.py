"""
Unit tests for ProfilingOrchestrator (SHU-343).

Tests the DB-aware orchestration layer that coordinates profiling,
manages status transitions, and persists results.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from shu.services.profiling_orchestrator import ProfilingOrchestrator
from shu.schemas.profiling import (
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentProfile,
    DocumentType,
    CapabilityManifest,
    ProfilingMode,
)


@pytest.fixture
def mock_settings():
    """Mock settings with profiling configuration."""
    settings = MagicMock()
    settings.profiling_timeout_seconds = 60
    settings.chunk_profiling_batch_size = 5
    settings.profiling_full_doc_max_tokens = 4000
    settings.profiling_max_input_tokens = 8000
    settings.enable_document_profiling = True
    return settings


@pytest.fixture
def mock_db():
    """Mock async database session."""
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_side_call_service():
    """Mock SideCallService."""
    return AsyncMock()


@pytest.fixture
def orchestrator(mock_db, mock_settings, mock_side_call_service):
    """Create orchestrator with mocked dependencies."""
    return ProfilingOrchestrator(mock_db, mock_settings, mock_side_call_service)


def create_mock_document(doc_id: str = "doc-123", title: str = "Test Doc"):
    """Create a mock Document with profiling status helpers."""
    doc = MagicMock()
    doc.id = doc_id
    doc.title = title
    doc.profiling_status = "pending"
    doc.mark_profiling_started = MagicMock()
    doc.mark_profiling_complete = MagicMock()
    doc.mark_profiling_failed = MagicMock()
    return doc


def create_mock_chunk(chunk_id: str, index: int, content: str):
    """Create a mock DocumentChunk."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.chunk_index = index
    chunk.content = content
    chunk.set_profile = MagicMock()
    return chunk


class TestProfilingModeSelection:
    """Tests for profiling mode selection logic."""

    def test_small_document_uses_full_doc(self, orchestrator, mock_settings):
        """Documents under threshold use full-document profiling."""
        mock_settings.profiling_full_doc_max_tokens = 4000
        mode = orchestrator._choose_profiling_mode(3000)
        assert mode == ProfilingMode.FULL_DOCUMENT

    def test_large_document_uses_aggregation(self, orchestrator, mock_settings):
        """Documents over threshold use chunk aggregation."""
        mock_settings.profiling_full_doc_max_tokens = 4000
        mode = orchestrator._choose_profiling_mode(5000)
        assert mode == ProfilingMode.CHUNK_AGGREGATION

    def test_exactly_threshold_uses_full_doc(self, orchestrator, mock_settings):
        """Documents at exactly threshold use full-document."""
        mock_settings.profiling_full_doc_max_tokens = 4000
        mode = orchestrator._choose_profiling_mode(4000)
        assert mode == ProfilingMode.FULL_DOCUMENT


class TestDocumentTextAssembly:
    """Tests for assembling document text from chunks."""

    def test_assemble_document_text(self, orchestrator):
        """Test joining chunk content."""
        chunks = [
            create_mock_chunk("c1", 0, "First chunk"),
            create_mock_chunk("c2", 1, "Second chunk"),
            create_mock_chunk("c3", 2, "Third chunk"),
        ]
        text = orchestrator._assemble_document_text(chunks)
        assert text == "First chunk\n\nSecond chunk\n\nThird chunk"

    def test_assemble_empty_chunks(self, orchestrator):
        """Test with empty chunk list."""
        text = orchestrator._assemble_document_text([])
        assert text == ""


class TestRunForDocument:
    """Tests for the main run_for_document method."""

    @pytest.mark.asyncio
    async def test_document_not_found(self, orchestrator, mock_db):
        """Test handling when document doesn't exist."""
        mock_db.get.return_value = None

        result = await orchestrator.run_for_document("nonexistent-id")

        assert result.success is False
        assert "not found" in result.error
        assert result.document_id == "nonexistent-id"

    @pytest.mark.asyncio
    async def test_full_doc_profiling_success(self, orchestrator, mock_db, mock_settings):
        """Test successful full-document profiling path."""
        # Setup mock document and chunks
        doc = create_mock_document()
        chunks = [
            create_mock_chunk("c1", 0, "Short content"),
            create_mock_chunk("c2", 1, "More short content"),
        ]
        mock_db.get.return_value = doc
        
        # Mock chunk query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        # Mock profiling service responses
        doc_profile = DocumentProfile(
            synopsis="Test synopsis",
            document_type=DocumentType.TECHNICAL,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1", chunk_index=0,
                profile=ChunkProfile(summary="Chunk 1", keywords=[], topics=[]),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2", chunk_index=1,
                profile=ChunkProfile(summary="Chunk 2", keywords=[], topics=[]),
                success=True,
            ),
        ]

        with patch.object(orchestrator.profiling_service, 'profile_chunks',
                         return_value=(chunk_results, 50)) as mock_chunks:
            with patch.object(orchestrator.profiling_service, 'profile_document',
                            return_value=(doc_profile, MagicMock(tokens_used=100))) as mock_doc:
                # Use small token count to force full-doc mode
                with patch('shu.services.profiling_orchestrator.estimate_tokens', return_value=100):
                    result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.FULL_DOCUMENT
        assert result.document_profile is not None
        assert len(result.chunk_profiles) == 2
        doc.mark_profiling_started.assert_called_once()
        doc.mark_profiling_complete.assert_called_once()
        mock_chunks.assert_called_once()
        mock_doc.assert_called_once()

    @pytest.mark.asyncio
    async def test_chunk_aggregation_profiling(self, orchestrator, mock_db, mock_settings):
        """Test chunk aggregation profiling path for large documents."""
        doc = create_mock_document()
        chunks = [create_mock_chunk(f"c{i}", i, f"Content {i}") for i in range(20)]
        mock_db.get.return_value = doc

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        doc_profile = DocumentProfile(
            synopsis="Aggregated synopsis",
            document_type=DocumentType.NARRATIVE,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id=f"c{i}", chunk_index=i,
                profile=ChunkProfile(summary=f"Summary {i}", keywords=[], topics=[]),
                success=True,
            )
            for i in range(20)
        ]

        with patch.object(orchestrator.profiling_service, 'profile_chunks',
                         return_value=(chunk_results, 100)):
            with patch.object(orchestrator.profiling_service, 'aggregate_chunk_profiles',
                            return_value=(doc_profile, MagicMock(tokens_used=200))):
                # Large token count to force aggregation mode
                with patch('shu.services.profiling_orchestrator.estimate_tokens', return_value=10000):
                    result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.CHUNK_AGGREGATION

    @pytest.mark.asyncio
    async def test_exception_marks_failed(self, orchestrator, mock_db):
        """Test that exceptions properly mark profiling as failed."""
        doc = create_mock_document()
        mock_db.get.return_value = doc
        mock_db.execute.side_effect = Exception("Database error")

        result = await orchestrator.run_for_document("doc-123")

        assert result.success is False
        assert "Database error" in result.error
        doc.mark_profiling_failed.assert_called_once()
        mock_db.commit.assert_called()  # Should commit the failed status


class TestPersistResults:
    """Tests for result persistence."""

    @pytest.mark.asyncio
    async def test_persist_success(self, orchestrator, mock_db):
        """Test persisting successful profiling results."""
        doc = create_mock_document()
        chunks = [
            create_mock_chunk("c1", 0, "Content 1"),
            create_mock_chunk("c2", 1, "Content 2"),
        ]
        doc_profile = DocumentProfile(
            synopsis="Test synopsis",
            document_type=DocumentType.TECHNICAL,
            capability_manifest=CapabilityManifest(
                answers_questions_about=["APIs"],
                provides_information_type=["instructions"],
            ),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1", chunk_index=0,
                profile=ChunkProfile(summary="Sum1", keywords=["k1"], topics=["t1"]),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2", chunk_index=1,
                profile=ChunkProfile(summary="Sum2", keywords=["k2"], topics=["t2"]),
                success=True,
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

        doc.mark_profiling_complete.assert_called_once()
        call_kwargs = doc.mark_profiling_complete.call_args[1]
        assert call_kwargs["synopsis"] == "Test synopsis"
        assert call_kwargs["document_type"] == "technical"
        chunks[0].set_profile.assert_called_once()
        chunks[1].set_profile.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_with_failed_chunks(self, orchestrator, mock_db):
        """Test that failed chunks don't get profiles set."""
        doc = create_mock_document()
        chunks = [create_mock_chunk("c1", 0, "Content")]
        doc_profile = DocumentProfile(
            synopsis="Test",
            document_type=DocumentType.NARRATIVE,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1", chunk_index=0,
                profile=ChunkProfile(summary="", keywords=[], topics=[]),
                success=False,
                error="Failed",
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

        # Chunk profile should NOT be set for failed chunk
        chunks[0].set_profile.assert_not_called()
        doc.mark_profiling_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_no_doc_profile(self, orchestrator, mock_db):
        """Test handling when document profile is None."""
        doc = create_mock_document()
        chunks = []

        await orchestrator._persist_results(doc, chunks, None, [])

        doc.mark_profiling_failed.assert_called_once()
        doc.mark_profiling_complete.assert_not_called()


class TestHelperMethods:
    """Tests for helper methods."""

    @pytest.mark.asyncio
    async def test_is_profiling_enabled(self, orchestrator, mock_settings):
        """Test profiling enabled check."""
        mock_settings.enable_document_profiling = True
        assert await orchestrator.is_profiling_enabled() is True

        mock_settings.enable_document_profiling = False
        assert await orchestrator.is_profiling_enabled() is False

    @pytest.mark.asyncio
    async def test_get_profiling_status(self, orchestrator, mock_db):
        """Test getting document profiling status."""
        doc = create_mock_document()
        doc.profiling_status = "complete"
        mock_db.get.return_value = doc

        status = await orchestrator.get_profiling_status("doc-123")
        assert status == "complete"

    @pytest.mark.asyncio
    async def test_get_profiling_status_not_found(self, orchestrator, mock_db):
        """Test status for non-existent document."""
        mock_db.get.return_value = None

        status = await orchestrator.get_profiling_status("nonexistent")
        assert status is None

