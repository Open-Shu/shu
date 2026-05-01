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
    SynthesizedQuery,
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


def create_mock_kb(kb_id: str = "kb-123", embedding_model: str = "Snowflake/snowflake-arctic-embed-l-v2.0"):
    """Create a mock KnowledgeBase."""
    kb = MagicMock(spec=KnowledgeBase)
    kb.id = kb_id
    kb.embedding_model = embedding_model
    return kb


def create_mock_chunk(chunk_id: str, index: int, content: str, summary: str | None = None):
    """Create a mock DocumentChunk."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.chunk_index = index
    chunk.content = content
    chunk.summary = summary
    chunk.topics = []
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
        """Test profiling path with fresh chunks (no existing summaries).

        Chunks without summaries are sent to profile_chunk_batch in batches.
        Results are committed per batch. Then document metadata is generated
        from DB-sourced summaries.
        """
        doc = create_mock_document()
        mock_kb = create_mock_kb()
        # Chunks without summaries — need profiling
        chunks = [create_mock_chunk(f"c{i}", i, f"Content {i}", summary=None) for i in range(20)]
        mock_db.get = AsyncMock(side_effect=[doc, mock_kb])

        # execute() is called for: loading chunks, loading summaries, then DELETE in _persist_queries
        mock_chunks_result = MagicMock()
        mock_chunks_result.scalars.return_value.all.return_value = chunks

        # After profiling, _load_chunk_summaries re-queries — column-only tuples
        # (chunk_index, summary, topics) per SHU-731. We make .scalars() raise
        # so any regression that reverts to loading full ORM rows fails the
        # test loudly instead of silently passing.
        mock_summaries_result = MagicMock()
        mock_summaries_result.scalars.side_effect = AssertionError(
            "regression: _load_chunk_summaries must use column-only .all(), not .scalars() (SHU-731)"
        )
        mock_summaries_result.all.return_value = [
            (i, f"Summary {i}", []) for i in range(20)
        ]

        # DELETE for existing queries returns a generic result
        mock_delete_result = MagicMock()

        mock_db.execute = AsyncMock(side_effect=[mock_chunks_result, mock_summaries_result, mock_delete_result])

        doc_profile = DocumentProfile(
            synopsis="Synopsis from accumulated summaries",
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
            for i in range(10)  # One batch of 10
        ]
        synthesized_queries = [
            SynthesizedQuery(query_text="What is this about?", chunk_index=0),
            SynthesizedQuery(query_text="How does it work?", chunk_index=1),
        ]

        # SHU-731: assert from inside the Phase 2 mock so we prove expunge
        # ran *before* the long-running LLM call, not just at end of run.
        # Asserting only after run_for_document completes can't distinguish
        # "expunged before Phase 2" from "expunged after Phase 2 returned".
        async def _phase2_side_effect(*_args, **_kwargs):
            assert mock_db.expunge.call_count == len(chunks), (
                "SHU-731: chunks must be expunged from the identity map BEFORE "
                "generate_document_metadata runs, not after"
            )
            return doc_profile, synthesized_queries, 100

        with (
            patch.object(
                orchestrator.profiling_service,
                "profile_chunk_batch",
                new=AsyncMock(return_value=(chunk_results, 150)),
            ),
            patch.object(
                orchestrator.profiling_service,
                "generate_document_metadata",
                new=AsyncMock(side_effect=_phase2_side_effect),
            ),
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.profiling_mode == ProfilingMode.CHUNK_AGGREGATION
        assert result.document_profile.synopsis == "Synopsis from accumulated summaries"
        assert result.chunk_coverage_percent == 100.0
        assert result.synopsis_embedded is False
        assert result.chunk_summaries_embedded == 0
        assert result.queries_embedded == 0
        assert mock_db.add.call_count == 2  # 2 queries persisted
        # SHU-731: ProfilingResult no longer carries per-chunk results — the
        # worker never reads them and accumulating was pure retention.
        assert result.chunk_profiles == []

    @pytest.mark.asyncio
    async def test_skip_already_profiled_chunks(self, orchestrator, mock_db, mock_settings):
        """Test that chunks with existing summaries are skipped on retry.

        When all chunks already have summaries (e.g., from a previous run where
        only metadata generation failed), chunk profiling is skipped entirely
        and the orchestrator goes straight to metadata generation.
        """
        doc = create_mock_document()
        mock_kb = create_mock_kb()
        # Chunks WITH summaries — should be skipped
        chunks = [create_mock_chunk(f"c{i}", i, f"Content {i}", summary=f"Existing summary {i}") for i in range(20)]
        mock_db.get = AsyncMock(side_effect=[doc, mock_kb])

        mock_chunks_result = MagicMock()
        mock_chunks_result.scalars.return_value.all.return_value = chunks

        mock_summaries_result = MagicMock()
        # SHU-731: same column-only contract as test_chunk_aggregation_profiling.
        mock_summaries_result.scalars.side_effect = AssertionError(
            "regression: _load_chunk_summaries must use column-only .all(), not .scalars() (SHU-731)"
        )
        mock_summaries_result.all.return_value = [
            (i, f"Existing summary {i}", []) for i in range(20)
        ]

        # DELETE for existing queries returns a generic result
        mock_delete_result = MagicMock()
        mock_db.execute = AsyncMock(side_effect=[mock_chunks_result, mock_summaries_result, mock_delete_result])

        doc_profile = DocumentProfile(
            synopsis="Synopsis on retry",
            document_type=DocumentType.NARRATIVE,
            capability_manifest=CapabilityManifest(),
        )

        with (
            patch.object(
                orchestrator.profiling_service,
                "profile_chunk_batch",
                new=AsyncMock(),
            ) as mock_batch,
            patch.object(
                orchestrator.profiling_service,
                "generate_document_metadata",
                new=AsyncMock(return_value=(doc_profile, [], 100)),
            ),
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        assert result.document_profile.synopsis == "Synopsis on retry"
        # profile_chunk_batch should NOT have been called — all chunks skipped
        mock_batch.assert_not_called()

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


class TestPersistDocumentProfile:
    """Tests for document profile persistence."""

    @pytest.mark.asyncio
    async def test_persist_success(self, orchestrator, mock_db):
        """Test persisting successful document profile."""
        doc = create_mock_document()
        doc_profile = DocumentProfile(
            synopsis="Test synopsis",
            document_type=DocumentType.TECHNICAL,
            capability_manifest=CapabilityManifest(
                answers_questions_about=["APIs"],
                provides_information_type=["instructions"],
            ),
        )

        await orchestrator._persist_document_profile(doc, doc_profile, 100.0)

        doc.mark_profiling_complete.assert_called_once()
        call_kwargs = doc.mark_profiling_complete.call_args[1]
        assert call_kwargs["synopsis"] == "Test synopsis"
        assert call_kwargs["document_type"] == "technical"
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_no_doc_profile(self, orchestrator, mock_db):
        """Test handling when document profile is None."""
        doc = create_mock_document()

        await orchestrator._persist_document_profile(doc, None, 100.0)

        doc.mark_profiling_failed.assert_called_once()
        doc.mark_profiling_complete.assert_not_called()
        mock_db.commit.assert_called_once()


class TestPersistQueries:
    """Tests for query persistence."""

    @pytest.mark.asyncio
    async def test_persist_queries_success(self, orchestrator, mock_db):
        """Test persisting synthesized queries with chunk provenance."""
        doc = create_mock_document()
        chunks = [create_mock_chunk("c0", 0, "Content 0"), create_mock_chunk("c1", 1, "Content 1")]
        mock_db.execute = AsyncMock()  # Mock the delete execute

        queries_created = await orchestrator._persist_queries(
            doc,
            [
                SynthesizedQuery(query_text="What is X?", chunk_index=0),
                SynthesizedQuery(query_text="How does Y work?", chunk_index=1),
                SynthesizedQuery(query_text="Show me Z", chunk_index=None),
            ],
            chunks,
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
        chunks = [create_mock_chunk("c0", 0, "Content 0")]
        mock_db.execute = AsyncMock()

        queries_created = await orchestrator._persist_queries(
            doc,
            [
                SynthesizedQuery(query_text="Valid query", chunk_index=0),
                SynthesizedQuery(query_text="", chunk_index=0),
                SynthesizedQuery(query_text="  ", chunk_index=None),
                SynthesizedQuery(query_text="Another valid", chunk_index=None),
            ],
            chunks,
        )

        assert queries_created == 2
        assert mock_db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_persist_queries_empty_list(self, orchestrator, mock_db):
        """Test handling empty query list."""
        doc = create_mock_document()
        mock_db.execute = AsyncMock()

        queries_created = await orchestrator._persist_queries(doc, [], [])

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
        chunks = [create_mock_chunk("c0", 0, "Content 0")]
        mock_db.execute = AsyncMock()

        await orchestrator._persist_queries(
            doc,
            [SynthesizedQuery(query_text="New query", chunk_index=0)],
            chunks,
        )

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
    """Tests for the standalone embed_profile_artifacts function (SHU-637).

    This function runs in a separate INGESTION_EMBED worker job, not inline
    during profiling. It uses document encoder for synopsis + chunk summaries,
    and query encoder for synthesized queries.
    """

    @pytest.mark.asyncio
    async def test_embeds_synopsis(self, mock_db):
        """Test that synopsis is embedded using document encoder."""
        from shu.services.profiling_orchestrator import embed_profile_artifacts

        doc = create_mock_document()
        doc.synopsis = "A document about OAuth2 authentication patterns"

        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_query_result

        fake_embedding = [0.1] * 384
        mock_embedding_service = AsyncMock()
        mock_embedding_service.embed_texts = AsyncMock(return_value=[fake_embedding])
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=1)
        with (
            patch("shu.services.profiling_orchestrator.get_embedding_service", AsyncMock(return_value=mock_embedding_service)),
            patch("shu.services.profiling_orchestrator.get_vector_store", AsyncMock(return_value=mock_vector_store)),
        ):
            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(mock_db, doc)

        assert synopsis_embedded is True
        assert chunk_summaries_embedded == 0
        assert queries_embedded == 0
        mock_embedding_service.embed_texts.assert_called_once()
        mock_vector_store.store_embeddings.assert_called_once()
        assert mock_vector_store.store_embeddings.call_args[0][0] == "synopses"
        assert mock_db.commit.call_count == 2

    @pytest.mark.asyncio
    async def test_embeds_queries_with_query_encoder(self, mock_db):
        """Test that synthesized queries use the query encoder (not document encoder)."""
        from shu.services.profiling_orchestrator import embed_profile_artifacts

        doc = create_mock_document()
        doc.synopsis = None

        mock_queries = []
        for text in ["What is OAuth2?", "How does PKCE work?", "What are JWT claims?"]:
            q = MagicMock()
            q.id = f"q-{text[:5]}"
            q.query_text = text
            q.query_embedding = None
            mock_queries.append(q)

        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = mock_queries
        mock_db.execute.side_effect = [empty_result, mock_query_result]

        fake_embeddings = [[0.1 * i] * 384 for i in range(3)]
        mock_embedding_service = AsyncMock()
        mock_embedding_service.embed_queries = AsyncMock(return_value=fake_embeddings)
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=3)
        with (
            patch("shu.services.profiling_orchestrator.get_embedding_service", AsyncMock(return_value=mock_embedding_service)),
            patch("shu.services.profiling_orchestrator.get_vector_store", AsyncMock(return_value=mock_vector_store)),
        ):
            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(mock_db, doc)

        assert synopsis_embedded is False
        assert chunk_summaries_embedded == 0
        assert queries_embedded == 3
        # Must use embed_queries (query encoder), NOT embed_texts (document encoder)
        mock_embedding_service.embed_queries.assert_called_once()
        mock_vector_store.store_embeddings.assert_called_once()
        assert mock_vector_store.store_embeddings.call_args[0][0] == "queries"

    @pytest.mark.asyncio
    async def test_embeds_both_synopsis_and_queries(self, mock_db):
        """Test that both synopsis and queries are embedded in one pass."""
        from shu.services.profiling_orchestrator import embed_profile_artifacts

        doc = create_mock_document()
        doc.synopsis = "Test synopsis"

        mock_query = MagicMock()
        mock_query.id = "q-1"
        mock_query.query_text = "What is this about?"
        mock_query.query_embedding = None

        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = [mock_query]
        mock_db.execute.side_effect = [empty_result, mock_query_result]

        mock_embedding_service = AsyncMock()
        mock_embedding_service.embed_texts = AsyncMock(return_value=[[0.5] * 384])
        mock_embedding_service.embed_queries = AsyncMock(return_value=[[0.3] * 384])
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=1)
        with (
            patch("shu.services.profiling_orchestrator.get_embedding_service", AsyncMock(return_value=mock_embedding_service)),
            patch("shu.services.profiling_orchestrator.get_vector_store", AsyncMock(return_value=mock_vector_store)),
        ):
            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(mock_db, doc)

        assert synopsis_embedded is True
        assert chunk_summaries_embedded == 0
        assert queries_embedded == 1
        # Synopsis uses document encoder, queries use query encoder
        mock_embedding_service.embed_texts.assert_called_once()
        mock_embedding_service.embed_queries.assert_called_once()
        assert mock_vector_store.store_embeddings.call_count == 2
        calls = mock_vector_store.store_embeddings.call_args_list
        assert calls[0][0][0] == "synopses"
        assert calls[1][0][0] == "queries"

    @pytest.mark.asyncio
    async def test_skips_empty_synopsis(self, mock_db):
        """Test that empty/whitespace synopsis is not embedded."""
        from shu.services.profiling_orchestrator import embed_profile_artifacts

        doc = create_mock_document()
        doc.synopsis = "   "

        mock_query_result = MagicMock()
        mock_query_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_query_result

        mock_embedding_service = AsyncMock()
        mock_vector_store = AsyncMock()
        with (
            patch("shu.services.profiling_orchestrator.get_embedding_service", AsyncMock(return_value=mock_embedding_service)),
            patch("shu.services.profiling_orchestrator.get_vector_store", AsyncMock(return_value=mock_vector_store)),
        ):
            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(mock_db, doc)

        assert synopsis_embedded is False
        assert chunk_summaries_embedded == 0
        mock_embedding_service.embed_texts.assert_not_called()

    @pytest.mark.asyncio
    async def test_embeds_chunk_summaries(self, mock_db):
        """Test that chunk summaries use document encoder."""
        from shu.services.profiling_orchestrator import embed_profile_artifacts

        doc = create_mock_document()
        doc.synopsis = None

        mock_chunk1 = MagicMock()
        mock_chunk1.id = "chunk-1"
        mock_chunk1.summary = "Summary for chunk 1"
        mock_chunk1.summary_embedding = None

        mock_chunk2 = MagicMock()
        mock_chunk2.id = "chunk-2"
        mock_chunk2.summary = "Summary for chunk 2"
        mock_chunk2.summary_embedding = None

        chunks_result = MagicMock()
        chunks_result.scalars.return_value.all.return_value = [mock_chunk1, mock_chunk2]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [chunks_result, empty_result]

        fake_embeddings = [[0.1] * 384, [0.2] * 384]
        mock_embedding_service = AsyncMock()
        mock_embedding_service.embed_texts = AsyncMock(return_value=fake_embeddings)
        mock_vector_store = AsyncMock()
        mock_vector_store.store_embeddings = AsyncMock(return_value=2)
        with (
            patch("shu.services.profiling_orchestrator.get_embedding_service", AsyncMock(return_value=mock_embedding_service)),
            patch("shu.services.profiling_orchestrator.get_vector_store", AsyncMock(return_value=mock_vector_store)),
        ):
            synopsis_embedded, chunk_summaries_embedded, queries_embedded = await embed_profile_artifacts(mock_db, doc)

        assert synopsis_embedded is False
        assert chunk_summaries_embedded == 2
        assert queries_embedded == 0
        # Must use embed_texts (document encoder) for chunk summaries
        mock_embedding_service.embed_texts.assert_called_once()
        mock_vector_store.store_embeddings.assert_called_once()
        assert mock_vector_store.store_embeddings.call_args[0][0] == "chunk_summaries"

    @pytest.mark.asyncio
    async def test_embedding_not_called_inline_after_profiling(self, orchestrator, mock_db):
        """Test that artifact embedding is NOT called inline during profiling (SHU-637)."""
        doc = create_mock_document()
        chunks = [create_mock_chunk("c1", 0, "Content", summary=None)]
        mock_db.get = AsyncMock(return_value=doc)

        mock_chunks_result = MagicMock()
        mock_chunks_result.scalars.return_value.all.return_value = chunks

        profiled_chunks = [create_mock_chunk("c1", 0, "Content", summary="Summary")]
        mock_summaries_result = MagicMock()
        mock_summaries_result.scalars.return_value.all.return_value = profiled_chunks

        mock_delete_result = MagicMock()
        mock_db.execute = AsyncMock(side_effect=[mock_chunks_result, mock_summaries_result, mock_delete_result])

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
                "profile_chunk_batch",
                new=AsyncMock(return_value=(chunk_results, 100)),
            ),
            patch.object(
                orchestrator.profiling_service,
                "generate_document_metadata",
                new=AsyncMock(return_value=(doc_profile, [], 50)),
            ),
            patch(
                "shu.services.profiling_orchestrator.embed_profile_artifacts",
                new=AsyncMock(),
            ) as embed_spy,
        ):
            result = await orchestrator.run_for_document("doc-123")

        assert result.success is True
        embed_spy.assert_not_called()
        assert result.synopsis_embedded is False
        assert result.chunk_summaries_embedded == 0
        assert result.queries_embedded == 0
