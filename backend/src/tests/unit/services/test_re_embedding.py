"""Unit tests for the re-embedding worker handler.

Tests _handle_re_embedding_job for batch processing, resumability,
error handling, and progress tracking.
"""

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from shu.models.knowledge_base import KnowledgeBase


def _make_job(knowledge_base_id="kb-1"):
    """Create a mock job with re-embedding payload."""
    job = MagicMock()
    job.id = "job-123"
    job.payload = {"knowledge_base_id": knowledge_base_id, "action": "re_embed_kb"}
    job.attempts = 1
    job.max_attempts = 3
    return job


def _make_chunk(chunk_id, content="test content", model="old-model"):
    """Create a mock DocumentChunk."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.content = content
    chunk.embedding_model = model
    chunk.embedding_created_at = None
    chunk.knowledge_base_id = "kb-1"
    return chunk


def _make_document(doc_id, synopsis="Test synopsis"):
    """Create a mock Document with a synopsis."""
    doc = MagicMock()
    doc.id = doc_id
    doc.synopsis = synopsis
    doc.knowledge_base_id = "kb-1"
    return doc


def _make_query(query_id, query_text="What is X?"):
    """Create a mock DocumentQuery."""
    q = MagicMock()
    q.id = query_id
    q.query_text = query_text
    q.knowledge_base_id = "kb-1"
    return q


class TestHandleReEmbeddingJob:
    """Tests for _handle_re_embedding_job in worker.py."""

    @pytest.mark.asyncio
    async def test_missing_knowledge_base_id_raises(self):
        """Job without knowledge_base_id should raise ValueError."""
        from shu.worker import _handle_re_embedding_job

        job = MagicMock()
        job.payload = {}

        with pytest.raises(ValueError, match="missing knowledge_base_id"):
            await _handle_re_embedding_job(job)

    @pytest.mark.asyncio
    async def test_deleted_kb_discards_job(self):
        """If KB was deleted, the job should be silently discarded."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock),
        ):
            # Should not raise
            await _handle_re_embedding_job(job)

    @pytest.mark.asyncio
    async def test_wrong_status_skips_processing(self):
        """If KB embedding_status != 're_embedding', skip without error."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "current"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock),
        ):
            await _handle_re_embedding_job(job)

    @pytest.mark.asyncio
    async def test_re_embeds_chunks_in_batches(self):
        """Verify chunks are re-embedded and progress is tracked."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()

        # Create mock chunks
        chunks = [_make_chunk(f"chunk-{i}") for i in range(3)]

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "re_embedding"
        mock_kb.re_embedding_progress = {"chunks_done": 0, "chunks_total": 3, "phase": "chunks"}
        mock_kb.update_re_embedding_progress = MagicMock()
        mock_kb.update_re_embedding_phase = MagicMock()
        mock_kb.mark_re_embedding_complete = MagicMock()

        # Mock embedding service
        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024, [0.3] * 1024])

        # Mock vector store
        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=3)
        mock_vs.ensure_index = AsyncMock(return_value=True)

        # Mock session
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        # Streaming pagination: each phase queries until empty.
        # Chunks: batch(3) → empty; Synopses: empty; Queries: empty
        chunks_result = MagicMock()
        chunks_result.scalars.return_value.all.return_value = chunks
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(side_effect=[
            chunks_result, empty_result,  # chunks: data, then empty
            empty_result,                  # synopses: empty
            empty_result,                  # queries: empty
        ])
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embedding_job(job)

        # Verify embeddings were generated
        mock_embedding.embed_texts.assert_called_once()

        # Verify vectors were stored
        mock_vs.store_embeddings.assert_called()

        # Verify progress was updated
        mock_kb.update_re_embedding_progress.assert_called_with(3)

        # Verify completion
        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")

    @pytest.mark.asyncio
    async def test_failure_marks_kb_error(self):
        """If re-embedding fails on final attempt, KB should be marked with error status."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()
        job.attempts = 3  # Equal to max_attempts — final attempt

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "re_embedding"
        mock_kb.re_embedding_progress = {"chunks_done": 0, "chunks_total": 1, "phase": "chunks"}
        mock_kb.mark_re_embedding_failed = MagicMock()

        # Make embed_texts raise
        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(side_effect=RuntimeError("GPU OOM"))

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        chunks_result = MagicMock()
        chunks_result.scalars.return_value.all.return_value = [_make_chunk("c-1")]
        mock_session.execute = AsyncMock(return_value=chunks_result)
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            with pytest.raises(RuntimeError, match="GPU OOM"):
                await _handle_re_embedding_job(job)

        mock_kb.mark_re_embedding_failed.assert_called_once()
        assert "GPU OOM" in mock_kb.mark_re_embedding_failed.call_args[0][0]


    @pytest.mark.asyncio
    async def test_resume_from_synopses_skips_chunks(self):
        """Resuming from 'synopses' phase should skip chunk processing entirely."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "re_embedding"
        mock_kb.re_embedding_progress = {"chunks_done": 10, "chunks_total": 10, "phase": "synopses"}
        mock_kb.update_re_embedding_progress = MagicMock()
        mock_kb.update_re_embedding_phase = MagicMock()
        mock_kb.mark_re_embedding_complete = MagicMock()

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(return_value=[[0.1] * 1024])

        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=1)
        mock_vs.ensure_index = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        # Streaming: synopses data → synopses empty → queries empty
        synopses_result = MagicMock()
        synopses_result.scalars.return_value.all.return_value = [_make_document("doc-1")]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(side_effect=[
            synopses_result, empty_result,  # synopses: data, then empty
            empty_result,                    # queries: empty
        ])
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embedding_job(job)

        # Synopses were embedded
        mock_embedding.embed_texts.assert_called_once()
        mock_vs.store_embeddings.assert_called_once()

        # Phase transitions happened (synopses→queries, queries→indexes)
        phase_calls = [c[0][0] for c in mock_kb.update_re_embedding_phase.call_args_list]
        assert "queries" in phase_calls
        assert "indexes" in phase_calls
        # "synopses" should NOT be in calls since we started there
        assert "synopses" not in phase_calls

        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")

    @pytest.mark.asyncio
    async def test_resume_from_queries_skips_chunks_and_synopses(self):
        """Resuming from 'queries' phase should skip chunks and synopses."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "re_embedding"
        mock_kb.re_embedding_progress = {"chunks_done": 10, "chunks_total": 10, "phase": "queries"}
        mock_kb.update_re_embedding_progress = MagicMock()
        mock_kb.update_re_embedding_phase = MagicMock()
        mock_kb.mark_re_embedding_complete = MagicMock()

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_queries = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024])

        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=2)
        mock_vs.ensure_index = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        # Streaming: queries data → queries empty
        queries_result = MagicMock()
        queries_result.scalars.return_value.all.return_value = [
            _make_query("q-1"), _make_query("q-2"),
        ]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(side_effect=[
            queries_result, empty_result,  # queries: data, then empty
        ])
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embedding_job(job)

        # Queries were embedded using embed_queries (not embed_texts)
        mock_embedding.embed_queries.assert_called_once()

        # Phase transitions: queries→indexes only
        phase_calls = [c[0][0] for c in mock_kb.update_re_embedding_phase.call_args_list]
        assert phase_calls == ["indexes"]

        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")

    @pytest.mark.asyncio
    async def test_resume_from_indexes_only_runs_indexing(self):
        """Resuming from 'indexes' phase should only run ensure_index calls."""
        from shu.worker import _handle_re_embedding_job

        job = _make_job()

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "re_embedding"
        mock_kb.re_embedding_progress = {"chunks_done": 10, "chunks_total": 10, "phase": "indexes"}
        mock_kb.update_re_embedding_progress = MagicMock()
        mock_kb.update_re_embedding_phase = MagicMock()
        mock_kb.mark_re_embedding_complete = MagicMock()

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()
        mock_vs.ensure_index = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embedding_job(job)

        # No embeddings generated (no chunks, synopses, or queries to process)
        mock_embedding.embed_texts.assert_not_called()
        assert mock_vs.ensure_index.call_count == 3  # chunks, synopses, queries

        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")


class TestReEmbeddingWorkloadType:
    """Tests for the RE_EMBEDDING WorkloadType."""

    def test_re_embedding_queue_name(self):
        from shu.core.workload_routing import WorkloadType

        assert WorkloadType.RE_EMBEDDING.queue_name == "shu:re_embedding"

    def test_from_queue_name(self):
        from shu.core.workload_routing import WorkloadType

        wt = WorkloadType.from_queue_name("shu:re_embedding")
        assert wt == WorkloadType.RE_EMBEDDING


class TestEnsureIndexDimensionScoped:
    """Tests for the updated ensure_index with dimension-scoped HNSW indexes."""

    def test_index_name_generation(self):
        from shu.core.vector_store import _index_name

        assert _index_name("chunks", 1024) == "ix_document_chunks_embedding_hnsw_1024"
        assert _index_name("synopses", 384) == "ix_documents_synopsis_embedding_hnsw_384"
        assert _index_name("queries", 1024) == "ix_document_queries_query_embedding_hnsw_1024"
        # Index type is reflected in the name
        assert _index_name("chunks", 384, "ivfflat") == "ix_document_chunks_embedding_ivfflat_384"

    @pytest.mark.asyncio
    async def test_ensure_index_creates_hnsw_partial_index(self):
        from shu.core.vector_store import PgVectorStore

        vs = PgVectorStore(index_type="hnsw")
        mock_db = AsyncMock()

        # Index doesn't exist yet
        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None
        # Row count
        count_result = MagicMock()
        count_result.scalar_one.return_value = 50
        mock_db.execute = AsyncMock(side_effect=[check_result, count_result, MagicMock()])

        created = await vs.ensure_index("chunks", 1024, db=mock_db)
        assert created is True

        # Verify the SQL contains dimension-scoped elements
        create_call = mock_db.execute.call_args_list[2]
        sql_str = str(create_call[0][0])
        assert "hnsw" in sql_str.lower()
        assert "vector(1024)" in sql_str
        assert "vector_dims" in sql_str

    @pytest.mark.asyncio
    async def test_ensure_index_skips_existing(self):
        from shu.core.vector_store import PgVectorStore

        vs = PgVectorStore()
        mock_db = AsyncMock()

        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = 1  # Already exists
        mock_db.execute = AsyncMock(return_value=check_result)

        created = await vs.ensure_index("chunks", 1024, db=mock_db)
        assert created is False
        assert mock_db.execute.call_count == 1  # Only the check query
