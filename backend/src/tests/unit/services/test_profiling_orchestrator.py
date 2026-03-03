"""
Unit tests for ProfilingOrchestrator (SHU-343, SHU-351, SHU-359, SHU-581, SHU-589).

Tests the DB-aware orchestration layer that coordinates profiling,
manages status transitions, persists results, and embeds profile artifacts.

SHU-589 removed unified profiling, consolidating on incremental profiling only.
SHU-351/359 added synopsis and query embedding after profiling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.models.document import Document
from shu.models.knowledge_base import KnowledgeBase
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
    return settings


@pytest.fixture
def mock_db():
    """Mock async database session.

    Uses MagicMock as base to ensure sync methods like `add()` are not
    accidentally async. Only explicitly async methods (execute, commit, get, etc.)
    are set as AsyncMock.
    """
    db = MagicMock()
    # Async methods on AsyncSession
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    # Sync methods like add(), delete() remain as MagicMock (default)
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
    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.title = title
    doc.knowledge_base_id = "kb-123"
    doc.profiling_status = "pending"
    doc.synopsis = None
    doc.synopsis_embedding = None
    doc.mark_profiling_started = MagicMock()
    doc.mark_profiling_complete = MagicMock()
    doc.mark_profiling_failed = MagicMock()
    return doc


def create_mock_kb(kb_id: str = "kb-123", embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"):
    """Create a mock KnowledgeBase."""
    kb = MagicMock(spec=KnowledgeBase)
    kb.id = kb_id
    kb.embedding_model = embedding_model
    return kb


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
        mock_kb = create_mock_kb()
        chunks = [create_mock_chunk(f"c{i}", i, f"Content {i}") for i in range(20)]
        # get() called twice: once for Document, once for KnowledgeBase
        mock_db.get = AsyncMock(side_effect=[doc, mock_kb])

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

        with (
            patch.object(
                orchestrator.profiling_service,
                "profile_chunks_incremental",
                new=AsyncMock(return_value=(chunk_results, doc_profile, synthesized_queries, 300, 100.0)),
            ),
            patch.object(orchestrator, "_embed_profile_artifacts", new=AsyncMock(return_value=(True, 2))),
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.CHUNK_AGGREGATION
        assert result.document_profile.synopsis == "Incremental synopsis from accumulated summaries"
        assert result.chunk_coverage_percent == 100.0
        assert result.synopsis_embedded is True
        assert result.queries_embedded == 2
        # Verify queries were persisted
        assert mock_db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_marks_failed(self, orchestrator, mock_db):
        """Test that exceptions properly mark profiling as failed."""
        doc = create_mock_document()
        mock_kb = create_mock_kb()
        # get() called twice: Document, then KnowledgeBase. Execute raises.
        mock_db.get = AsyncMock(side_effect=[doc, mock_kb])
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


class TestEmbedProfileArtifacts:
    """Tests for synopsis and query embedding (SHU-351, SHU-359)."""

    @pytest.mark.asyncio
    async def test_embeds_synopsis(self, orchestrator, mock_db):
        """Test that synopsis is embedded when present."""
        doc = create_mock_document()
        doc.synopsis = "A document about OAuth2 authentication patterns"

        # No queries to embed
        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_query_result

        fake_embedding = [0.1] * 384
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=1)
        with (
            patch.object(orchestrator, "_embed_texts", new=AsyncMock(return_value=[fake_embedding])),
            patch(
                "shu.core.vector_store.get_vector_store",
                new=AsyncMock(return_value=mock_vector_store),
            ),
        ):
            synopsis_embedded, queries_embedded = await orchestrator._embed_profile_artifacts(doc)

        assert synopsis_embedded is True
        assert queries_embedded == 0
        mock_vector_store.store_embeddings.assert_called_once()
        call_args = mock_vector_store.store_embeddings.call_args
        assert call_args[0][0] == "synopses"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_embeds_queries(self, orchestrator, mock_db):
        """Test that synthesized queries are batch-embedded."""
        doc = create_mock_document()
        doc.synopsis = None  # No synopsis

        # Create mock queries without embeddings
        mock_queries = []
        for text in ["What is OAuth2?", "How does PKCE work?", "What are JWT claims?"]:
            q = MagicMock()
            q.id = f"q-{text[:5]}"
            q.query_text = text
            q.query_embedding = None
            mock_queries.append(q)

        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = mock_queries
        mock_db.execute.return_value = mock_query_result

        fake_embeddings = [[0.1 * i] * 384 for i in range(3)]
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=3)
        with (
            patch.object(orchestrator, "_embed_texts", new=AsyncMock(return_value=fake_embeddings)),
            patch(
                "shu.core.vector_store.get_vector_store",
                new=AsyncMock(return_value=mock_vector_store),
            ),
        ):
            synopsis_embedded, queries_embedded = await orchestrator._embed_profile_artifacts(doc)

        assert synopsis_embedded is False
        assert queries_embedded == 3
        # Verify VectorStore was called for queries collection
        mock_vector_store.store_embeddings.assert_called_once()
        call_args = mock_vector_store.store_embeddings.call_args
        assert call_args[0][0] == "queries"
        entries = call_args[0][1]
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_embeds_both_synopsis_and_queries(self, orchestrator, mock_db):
        """Test that both synopsis and queries are embedded in one pass."""
        doc = create_mock_document()
        doc.synopsis = "Test synopsis"

        mock_query = MagicMock()
        mock_query.id = "q-1"
        mock_query.query_text = "What is this about?"
        mock_query.query_embedding = None

        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = [mock_query]
        mock_db.execute.return_value = mock_query_result

        synopsis_embedding = [0.5] * 384
        query_embedding = [0.3] * 384

        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=1)
        # _embed_texts called twice: once for synopsis, once for queries
        with (
            patch.object(
                orchestrator,
                "_embed_texts",
                new=AsyncMock(side_effect=[[synopsis_embedding], [query_embedding]]),
            ),
            patch(
                "shu.core.vector_store.get_vector_store",
                new=AsyncMock(return_value=mock_vector_store),
            ),
        ):
            synopsis_embedded, queries_embedded = await orchestrator._embed_profile_artifacts(doc)

        assert synopsis_embedded is True
        assert queries_embedded == 1
        # VectorStore should be called twice: once for synopses, once for queries
        assert mock_vector_store.store_embeddings.call_count == 2
        calls = mock_vector_store.store_embeddings.call_args_list
        assert calls[0][0][0] == "synopses"
        assert calls[1][0][0] == "queries"

    @pytest.mark.asyncio
    async def test_skips_empty_synopsis(self, orchestrator, mock_db):
        """Test that empty/whitespace synopsis is not embedded."""
        doc = create_mock_document()
        doc.synopsis = "   "  # Whitespace only

        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_query_result

        mock_vector_store = AsyncMock()
        with (
            patch.object(orchestrator, "_embed_texts", new=AsyncMock()) as mock_embed,
            patch(
                "shu.core.vector_store.get_vector_store",
                new=AsyncMock(return_value=mock_vector_store),
            ),
        ):
            synopsis_embedded, queries_embedded = await orchestrator._embed_profile_artifacts(doc)

        assert synopsis_embedded is False
        mock_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_embedding_failure_isolated_in_run_for_document(self, orchestrator, mock_db):
        """Test that embedding failure doesn't fail the profiling job."""
        doc = create_mock_document()
        chunks = [create_mock_chunk("c1", 0, "Content")]
        mock_db.get = AsyncMock(return_value=doc)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        doc_profile = DocumentProfile(
            synopsis="Test synopsis",
            document_type=DocumentType.TECHNICAL,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(summary="Summary", keywords=[], topics=[]),
                success=True,
            ),
        ]

        with (
            patch.object(
                orchestrator.profiling_service,
                "profile_chunks_incremental",
                new=AsyncMock(return_value=(chunk_results, doc_profile, ["query"], 100, 100.0)),
            ),
            patch.object(
                orchestrator,
                "_embed_profile_artifacts",
                new=AsyncMock(side_effect=Exception("Embedding model not available")),
            ),
        ):
            result = await orchestrator.run_for_document("doc-123")

        # Profiling still succeeds even though embedding failed
        assert result.success is True
        assert result.synopsis_embedded is False
        assert result.queries_embedded == 0

    @pytest.mark.asyncio
    async def test_embedding_runs_after_successful_profiling(self, orchestrator, mock_db):
        """Test that embedding runs when profiling succeeds (no KB lookup needed for embedding)."""
        doc = create_mock_document()
        chunks = [create_mock_chunk("c1", 0, "Content")]
        mock_db.get = AsyncMock(return_value=doc)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        mock_db.execute.return_value = mock_result

        doc_profile = DocumentProfile(
            synopsis="Test synopsis",
            document_type=DocumentType.TECHNICAL,
            capability_manifest=CapabilityManifest(),
        )
        chunk_results = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(summary="Summary", keywords=[], topics=[]),
                success=True,
            ),
        ]

        with (
            patch.object(
                orchestrator.profiling_service,
                "profile_chunks_incremental",
                new=AsyncMock(return_value=(chunk_results, doc_profile, ["query"], 100, 100.0)),
            ),
            patch.object(
                orchestrator,
                "_embed_profile_artifacts",
                new=AsyncMock(return_value=(True, 1)),
            ),
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.synopsis_embedded is True
        assert result.queries_embedded == 1
