"""
Unit tests for ProfilingOrchestrator (SHU-343, SHU-581, SHU-589).

Tests the DB-aware orchestration layer that coordinates profiling,
manages status transitions, and persists results.

SHU-589 removed unified profiling, consolidating on incremental profiling only.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.schemas.profiling import (
    CapabilityManifest,
    ChunkProfile,
    ChunkProfileResult,
    DocumentProfile,
    DocumentType,
    ProfilingMode,
)
from shu.services.profiling_orchestrator import ProfilingOrchestrator


@pytest.fixture
def mock_settings():
    """Mock settings with profiling configuration."""
    settings = MagicMock()
    settings.profiling_timeout_seconds = 180
    settings.chunk_profiling_batch_size = 5
    settings.profiling_max_input_tokens = 8000
    settings.enable_document_profiling = True
    settings.enable_query_synthesis = True
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
    doc.knowledge_base_id = "kb-123"
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
    async def test_chunk_aggregation_profiling(self, orchestrator, mock_db, mock_settings):
        """Test incremental profiling path for large documents (SHU-582).

        Large documents use profile_chunks_incremental() which eliminates
        the separate aggregation LLM call by having the final batch generate
        document-level metadata from accumulated summaries.
        """
        doc = create_mock_document()
        chunks = [create_mock_chunk(f"c{i}", i, f"Content {i}") for i in range(20)]
        mock_db.get.return_value = doc

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        doc_profile = DocumentProfile(
            synopsis="Incremental synopsis from accumulated summaries",
            document_type=DocumentType.NARRATIVE,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id=f"c{i}",
                chunk_index=i,
                profile=ChunkProfile(
                    summary=f"Summary {i} with specific details",
                    keywords=[],
                    topics=[],
                ),
                success=True,
            )
            for i in range(20)
        ]
        synthesized_queries = ["What is this about?", "How does it work?"]

        with patch.object(
            orchestrator.profiling_service,
            "profile_chunks_incremental",
            new=AsyncMock(return_value=(chunk_results, doc_profile, synthesized_queries, 300, 100.0)),
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.CHUNK_AGGREGATION
        assert result.document_profile.synopsis == "Incremental synopsis from accumulated summaries"
        assert result.chunk_coverage_percent == 100.0
        # Verify queries were persisted
        assert mock_db.add.call_count == 2

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
        mock_db.commit.assert_called()


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
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(
                    summary="Explains API basics with OAuth examples",
                    keywords=["k1"],
                    topics=["t1"],
                ),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2",
                chunk_index=1,
                profile=ChunkProfile(
                    summary="Covers advanced usage patterns",
                    keywords=["k2"],
                    topics=["t2"],
                ),
                success=True,
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

        doc.mark_profiling_complete.assert_called_once()
        call_kwargs = doc.mark_profiling_complete.call_args[1]
        assert call_kwargs["synopsis"] == "Test synopsis"
        assert call_kwargs["document_type"] == "technical"

        # Verify summary is passed to set_profile
        chunks[0].set_profile.assert_called_once()
        c1_call_kwargs = chunks[0].set_profile.call_args[1]
        assert c1_call_kwargs["summary"] == "Explains API basics with OAuth examples"

        chunks[1].set_profile.assert_called_once()
        c2_call_kwargs = chunks[1].set_profile.call_args[1]
        assert c2_call_kwargs["summary"] == "Covers advanced usage patterns"

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
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(summary="", keywords=[], topics=[]),
                success=False,
                error="Failed",
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

        chunks[0].set_profile.assert_not_called()
        doc.mark_profiling_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_skips_empty_summary_chunks(self, orchestrator, mock_db):
        """Test that chunks with success=True but empty summary are NOT persisted.

        This matches the failure detection in ProfilingService._is_chunk_profile_failed()
        which considers empty summaries as failures for coverage calculation.
        """
        doc = create_mock_document()
        chunks = [
            create_mock_chunk("c1", 0, "Content 1"),
            create_mock_chunk("c2", 1, "Content 2"),
        ]
        doc_profile = DocumentProfile(
            synopsis="Test",
            document_type=DocumentType.NARRATIVE,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(summary="", keywords=["k1"], topics=["t1"]),  # Empty summary
                success=True,  # success=True but empty summary should NOT be persisted
            ),
            ChunkProfileResult(
                chunk_id="c2",
                chunk_index=1,
                profile=ChunkProfile(summary="Valid summary", keywords=["k2"], topics=["t2"]),
                success=True,
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

        # Chunk with empty summary should NOT have set_profile called
        chunks[0].set_profile.assert_not_called()
        # Chunk with valid summary SHOULD have set_profile called
        chunks[1].set_profile.assert_called_once()
        c2_call_kwargs = chunks[1].set_profile.call_args[1]
        assert c2_call_kwargs["summary"] == "Valid summary"

    @pytest.mark.asyncio
    async def test_persist_no_doc_profile(self, orchestrator, mock_db):
        """Test handling when document profile is None."""
        doc = create_mock_document()
        chunks = []

        await orchestrator._persist_results(doc, chunks, None, [])

        doc.mark_profiling_failed.assert_called_once()
        doc.mark_profiling_complete.assert_not_called()


class TestPersistQueries:
    """Tests for query persistence."""

    @pytest.mark.asyncio
    async def test_persist_queries_success(self, orchestrator, mock_db):
        """Test persisting synthesized queries."""
        doc = create_mock_document()
        mock_db.execute = AsyncMock()  # Mock the delete execute

        queries_created = await orchestrator._persist_queries(
            doc,
            ["What is X?", "How does Y work?", "Show me Z"],
        )

        assert queries_created == 3
        # 1 execute for delete + 3 adds
        mock_db.execute.assert_called_once()  # Delete existing queries
        assert mock_db.add.call_count == 3
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_queries_skips_empty(self, orchestrator, mock_db):
        """Test that empty queries are skipped."""
        doc = create_mock_document()
        mock_db.execute = AsyncMock()

        queries_created = await orchestrator._persist_queries(
            doc,
            ["Valid query", "", "  ", "Another valid"],
        )

        assert queries_created == 2
        assert mock_db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_queries_empty_list(self, orchestrator, mock_db):
        """Test handling empty query list."""
        doc = create_mock_document()
        mock_db.execute = AsyncMock()

        queries_created = await orchestrator._persist_queries(doc, [])

        assert queries_created == 0
        # Delete is still called even for empty list (clears old queries)
        mock_db.execute.assert_called_once()
        mock_db.add.assert_not_called()
        # Commit is called to flush the DELETE even with no new queries
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_queries_deletes_existing(self, orchestrator, mock_db):
        """Test that existing queries are deleted before creating new ones (re-profiling)."""
        doc = create_mock_document()
        mock_db.execute = AsyncMock()

        await orchestrator._persist_queries(doc, ["New query"])

        # Verify delete was called with correct document_id filter
        mock_db.execute.assert_called_once()
        delete_call = mock_db.execute.call_args[0][0]
        # The delete statement should target document_queries table
        assert "document_queries" in str(delete_call) or "DocumentQuery" in str(delete_call)


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
