"""
Unit tests for ProfilingOrchestrator (SHU-343, SHU-581).

Tests the DB-aware orchestration layer that coordinates profiling,
manages status transitions, and persists results.

SHU-581 consolidated query synthesis into the profiling flow:
- Small docs: Unified profiling generates synopsis, chunks, queries in one call
- Large docs: Batch chunk profiling + aggregation generates queries
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
    UnifiedChunkProfile,
    UnifiedProfilingResponse,
)
from shu.services.profiling_orchestrator import ProfilingOrchestrator


@pytest.fixture
def mock_settings():
    """Mock settings with profiling configuration."""
    settings = MagicMock()
    settings.profiling_timeout_seconds = 60
    settings.chunk_profiling_batch_size = 5
    settings.profiling_full_doc_max_tokens = 4000
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
    async def test_unified_profiling_success(self, orchestrator, mock_db, mock_settings):
        """Test successful unified profiling path for small documents."""
        doc = create_mock_document()
        chunks = [
            create_mock_chunk("c1", 0, "Short content"),
            create_mock_chunk("c2", 1, "More short content"),
        ]
        mock_db.get.return_value = doc

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        # Mock unified profiling response
        unified_response = UnifiedProfilingResponse(
            synopsis="Test synopsis for the document",
            chunks=[
                UnifiedChunkProfile(
                    index=0,
                    one_liner="Explains short content",
                    summary="Detailed summary of chunk 1",
                    keywords=["short", "content"],
                    topics=["testing"],
                ),
                UnifiedChunkProfile(
                    index=1,
                    one_liner="Covers more content",
                    summary="Detailed summary of chunk 2",
                    keywords=["more", "content"],
                    topics=["testing"],
                ),
            ],
            document_type="technical",
            capability_manifest=CapabilityManifest(
                answers_questions_about=["testing"],
            ),
            synthesized_queries=[
                "What is the short content about?",
                "How does the content work?",
            ],
        )

        with patch.object(
            orchestrator.profiling_service,
            "profile_document_unified",
            return_value=(unified_response, MagicMock(tokens_used=200)),
        ) as mock_unified:
            with patch("shu.services.profiling_orchestrator.estimate_tokens", return_value=100):
                result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.FULL_DOCUMENT
        assert result.document_profile is not None
        assert result.document_profile.synopsis == "Test synopsis for the document"
        assert len(result.chunk_profiles) == 2
        doc.mark_profiling_started.assert_called_once()
        doc.mark_profiling_complete.assert_called_once()
        mock_unified.assert_called_once()
        # Verify queries were persisted (2 queries)
        assert mock_db.add.call_count == 2

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
                chunk_id=f"c{i}",
                chunk_index=i,
                profile=ChunkProfile(
                    one_liner=f"One-liner {i}",
                    summary=f"Summary {i}",
                    keywords=[],
                    topics=[],
                ),
                success=True,
            )
            for i in range(20)
        ]
        synthesized_queries = ["What is this about?", "How does it work?"]

        with patch.object(
            orchestrator.profiling_service, "profile_chunks", return_value=(chunk_results, 100)
        ):
            with patch.object(
                orchestrator.profiling_service,
                "aggregate_chunk_profiles",
                return_value=((doc_profile, synthesized_queries), MagicMock(tokens_used=200)),
            ):
                with patch("shu.services.profiling_orchestrator.estimate_tokens", return_value=10000):
                    result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.CHUNK_AGGREGATION
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
    async def test_persist_success_with_one_liner(self, orchestrator, mock_db):
        """Test persisting successful profiling results including one_liner."""
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
                    one_liner="Explains API basics",
                    summary="Sum1",
                    keywords=["k1"],
                    topics=["t1"],
                ),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2",
                chunk_index=1,
                profile=ChunkProfile(
                    one_liner="Covers advanced usage",
                    summary="Sum2",
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

        # Verify one_liner is passed to set_profile
        chunks[0].set_profile.assert_called_once()
        c1_call_kwargs = chunks[0].set_profile.call_args[1]
        assert c1_call_kwargs["one_liner"] == "Explains API basics"
        assert c1_call_kwargs["summary"] == "Sum1"

        chunks[1].set_profile.assert_called_once()
        c2_call_kwargs = chunks[1].set_profile.call_args[1]
        assert c2_call_kwargs["one_liner"] == "Covers advanced usage"

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
                profile=ChunkProfile(one_liner="", summary="", keywords=[], topics=[]),
                success=False,
                error="Failed",
            ),
        ]

        await orchestrator._persist_results(doc, chunks, doc_profile, chunk_results)

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


class TestPersistQueries:
    """Tests for query persistence."""

    @pytest.mark.asyncio
    async def test_persist_queries_success(self, orchestrator, mock_db):
        """Test persisting synthesized queries."""
        doc = create_mock_document()

        queries_created = await orchestrator._persist_queries(
            doc,
            ["What is X?", "How does Y work?", "Show me Z"],
        )

        assert queries_created == 3
        assert mock_db.add.call_count == 3
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_queries_skips_empty(self, orchestrator, mock_db):
        """Test that empty queries are skipped."""
        doc = create_mock_document()

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

        queries_created = await orchestrator._persist_queries(doc, [])

        assert queries_created == 0
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


class TestUnifiedToDocumentProfile:
    """Tests for converting unified response to DocumentProfile."""

    def test_unified_to_document_profile(self, orchestrator):
        """Test conversion of unified response to DocumentProfile."""
        unified = UnifiedProfilingResponse(
            synopsis="Test synopsis",
            chunks=[],
            document_type="technical",
            capability_manifest=CapabilityManifest(
                answers_questions_about=["APIs", "authentication"],
            ),
            synthesized_queries=[],
        )

        profile = orchestrator._unified_to_document_profile(unified)

        assert profile.synopsis == "Test synopsis"
        assert profile.document_type == DocumentType.TECHNICAL
        assert profile.capability_manifest.answers_questions_about == ["APIs", "authentication"]

    def test_unified_to_document_profile_invalid_type(self, orchestrator):
        """Test fallback for invalid document type."""
        unified = UnifiedProfilingResponse(
            synopsis="Test",
            chunks=[],
            document_type="invalid_type",
            capability_manifest=CapabilityManifest(),
            synthesized_queries=[],
        )

        profile = orchestrator._unified_to_document_profile(unified)

        assert profile.document_type == DocumentType.NARRATIVE  # Fallback


class TestUnifiedToChunkResults:
    """Tests for converting unified response chunks to ChunkProfileResults."""

    def test_unified_to_chunk_results(self, orchestrator):
        """Test conversion of unified chunks to ChunkProfileResults."""
        from shu.schemas.profiling import ChunkData

        unified = UnifiedProfilingResponse(
            synopsis="Test",
            chunks=[
                UnifiedChunkProfile(
                    index=0,
                    one_liner="First chunk summary",
                    summary="Detailed first",
                    keywords=["k1"],
                    topics=["t1"],
                ),
                UnifiedChunkProfile(
                    index=1,
                    one_liner="Second chunk summary",
                    summary="Detailed second",
                    keywords=["k2"],
                    topics=["t2"],
                ),
            ],
            document_type="narrative",
            capability_manifest=CapabilityManifest(),
            synthesized_queries=[],
        )

        chunk_data = [
            ChunkData(chunk_id="c1", chunk_index=0, content="Content 1"),
            ChunkData(chunk_id="c2", chunk_index=1, content="Content 2"),
        ]

        results = orchestrator._unified_to_chunk_results(unified, chunk_data)

        assert len(results) == 2
        assert results[0].chunk_id == "c1"
        assert results[0].profile.one_liner == "First chunk summary"
        assert results[0].success is True
        assert results[1].chunk_id == "c2"
        assert results[1].profile.one_liner == "Second chunk summary"

    def test_unified_to_chunk_results_missing_chunk(self, orchestrator):
        """Test handling when unified response is missing a chunk."""
        from shu.schemas.profiling import ChunkData

        unified = UnifiedProfilingResponse(
            synopsis="Test",
            chunks=[
                UnifiedChunkProfile(
                    index=0,
                    one_liner="Only first",
                    summary="First",
                    keywords=[],
                    topics=[],
                ),
            ],
            document_type="narrative",
            capability_manifest=CapabilityManifest(),
            synthesized_queries=[],
        )

        chunk_data = [
            ChunkData(chunk_id="c1", chunk_index=0, content="Content 1"),
            ChunkData(chunk_id="c2", chunk_index=1, content="Content 2"),
        ]

        results = orchestrator._unified_to_chunk_results(unified, chunk_data)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert "No profile in unified response" in results[1].error


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


class TestQuerySynthesisDisabled:
    """Tests for query synthesis disabled scenarios."""

    @pytest.mark.asyncio
    async def test_queries_not_persisted_when_disabled(self, orchestrator, mock_db, mock_settings):
        """Test that queries are not persisted when query synthesis is disabled."""
        mock_settings.enable_query_synthesis = False

        doc = create_mock_document()
        chunks = [create_mock_chunk("c1", 0, "Content")]
        mock_db.get.return_value = doc

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        unified_response = UnifiedProfilingResponse(
            synopsis="Test synopsis",
            chunks=[
                UnifiedChunkProfile(
                    index=0,
                    one_liner="One liner",
                    summary="Summary",
                    keywords=[],
                    topics=[],
                ),
            ],
            document_type="technical",
            capability_manifest=CapabilityManifest(),
            synthesized_queries=["Query 1", "Query 2"],  # Queries in response
        )

        with patch.object(
            orchestrator.profiling_service,
            "profile_document_unified",
            return_value=(unified_response, MagicMock(tokens_used=100)),
        ):
            with patch("shu.services.profiling_orchestrator.estimate_tokens", return_value=100):
                result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        # No queries should be added to DB
        mock_db.add.assert_not_called()
