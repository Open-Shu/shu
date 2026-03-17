"""Unit tests for the re-embedding worker handlers.

Tests handle_re_embedding_job, _handle_re_embed_chunks_job, and
_handle_re_embed_finalize_job for batch processing, parallel completion,
error handling, and progress tracking.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shu.models.knowledge_base import KnowledgeBase


def _make_job(knowledge_base_id="kb-1", action="re_embed_chunks", worker_index=0):
    """Create a mock job with re-embedding payload."""
    job = MagicMock()
    job.id = "job-123"
    job.payload = {
        "knowledge_base_id": knowledge_base_id,
        "action": action,
        "worker_index": worker_index,
    }
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


def _make_kb(phase="chunks", chunks_done=0, chunks_total=3, workers_total=1, workers_completed=0):
    """Create a mock KnowledgeBase with re-embedding progress."""
    mock_kb = MagicMock(spec=KnowledgeBase)
    mock_kb.id = "kb-1"
    mock_kb.embedding_status = "re_embedding"
    mock_kb.re_embedding_progress = {
        "chunks_done": chunks_done,
        "chunks_total": chunks_total,
        "workers_total": workers_total,
        "workers_completed": workers_completed,
        "phase": phase,
    }
    mock_kb.update_re_embedding_phase = MagicMock()
    mock_kb.mark_re_embedding_complete = MagicMock()
    mock_kb.mark_re_embedding_failed = MagicMock()
    mock_kb.increment_re_embedding_progress = MagicMock()
    mock_kb.increment_workers_completed = MagicMock(return_value=True)  # last worker by default
    return mock_kb


def _make_session_factory(session):
    """Create a mock async session factory wrapping the given session."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _make_scalar_result(items):
    """Create a mock query result returning the given items."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_lock_result(kb):
    """Create a mock FOR UPDATE result returning the given KB."""
    result = MagicMock()
    result.scalar_one.return_value = kb
    result.scalar_one_or_none.return_value = kb
    return result


def _make_count_result(count):
    """Create a mock scalar count result."""
    result = MagicMock()
    result.scalar.return_value = count
    return result


HANDLER_MODULE = "shu.re_embedding_handler"


class TestHandleReEmbeddingJob:
    """Tests for the RE_EMBEDDING job router."""

    @pytest.mark.asyncio
    async def test_missing_knowledge_base_id_raises(self):
        """Job without knowledge_base_id should raise ValueError."""
        from shu.re_embedding_handler import handle_re_embedding_job

        job = MagicMock()
        job.payload = {"action": "re_embed_chunks"}

        with pytest.raises(ValueError, match="missing knowledge_base_id"):
            await handle_re_embedding_job(job)

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self):
        """Job with unknown action should raise ValueError."""
        from shu.re_embedding_handler import handle_re_embedding_job

        job = MagicMock()
        job.payload = {"knowledge_base_id": "kb-1", "action": "unknown_action"}

        with pytest.raises(ValueError, match="Unknown re-embedding action"):
            await handle_re_embedding_job(job)


class TestHandleReEmbedChunksJob:
    """Tests for _handle_re_embed_chunks_job."""

    @pytest.mark.asyncio
    async def test_deleted_kb_discards_job(self):
        """If KB was deleted, the job should be silently discarded."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock),
        ):
            await _handle_re_embed_chunks_job(job)

    @pytest.mark.asyncio
    async def test_wrong_status_skips_processing(self):
        """If KB embedding_status != 're_embedding', skip without error."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()

        mock_kb = MagicMock(spec=KnowledgeBase)
        mock_kb.embedding_status = "current"

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock),
        ):
            await _handle_re_embed_chunks_job(job)

    @pytest.mark.asyncio
    async def test_re_embeds_chunks_in_batches(self):
        """Verify chunks are re-embedded and progress is updated atomically."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        chunks = [_make_chunk(f"chunk-{i}") for i in range(3)]
        mock_kb = _make_kb(workers_total=1)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024, [0.3] * 1024])

        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=3)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()

        chunks_result = _make_scalar_result(chunks)
        empty_result = _make_scalar_result([])
        lock_result = _make_lock_result(mock_kb)
        count_result = _make_count_result(0)  # no remaining chunks

        mock_session.execute = AsyncMock(side_effect=[
            chunks_result,  # chunk query: 3 results
            lock_result,    # FOR UPDATE progress increment
            empty_result,   # chunk query: empty (done)
            lock_result,    # FOR UPDATE workers_completed
            count_result,   # remaining chunks count
        ])
        mock_session.commit = AsyncMock()

        mock_queue_backend = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("shu.core.queue_backend.get_queue_backend", new_callable=AsyncMock, return_value=mock_queue_backend),
        ):
            await _handle_re_embed_chunks_job(job)

        mock_embedding.embed_texts.assert_called_once()
        mock_vs.store_embeddings.assert_called()
        mock_kb.increment_re_embedding_progress.assert_called_with(3)
        mock_kb.increment_workers_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_last_worker_enqueues_finalization(self):
        """When the last worker completes, a finalization job is enqueued."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        mock_kb = _make_kb(workers_total=3, workers_completed=2)
        mock_kb.increment_workers_completed = MagicMock(return_value=True)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()

        empty_result = _make_scalar_result([])
        lock_result = _make_lock_result(mock_kb)
        count_result = _make_count_result(0)  # no remaining chunks

        mock_session.execute = AsyncMock(side_effect=[
            empty_result,   # chunk query: empty (nothing to process)
            lock_result,    # FOR UPDATE workers_completed
            count_result,   # remaining chunks count
        ])
        mock_session.commit = AsyncMock()

        mock_queue_backend = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("shu.core.queue_backend.get_queue_backend", new_callable=AsyncMock, return_value=mock_queue_backend),
            patch("shu.core.workload_routing.enqueue_job", new_callable=AsyncMock) as mock_enqueue,
        ):
            await _handle_re_embed_chunks_job(job)

        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs[1]["payload"]["action"] == "re_embed_finalize"

    @pytest.mark.asyncio
    async def test_last_worker_marks_error_when_chunks_remain(self):
        """If all workers complete but chunks remain unprocessed, mark KB as error."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        mock_kb = _make_kb(workers_total=2, workers_completed=1)
        mock_kb.increment_workers_completed = MagicMock(return_value=True)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()

        empty_result = _make_scalar_result([])
        lock_result = _make_lock_result(mock_kb)
        count_result = _make_count_result(5)  # 5 chunks still unprocessed

        mock_session.execute = AsyncMock(side_effect=[
            empty_result,   # chunk query: empty
            lock_result,    # FOR UPDATE workers_completed
            count_result,   # remaining chunks count: 5
        ])
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embed_chunks_job(job)

        mock_kb.mark_re_embedding_failed.assert_called_once()
        assert "5 chunks remain" in mock_kb.mark_re_embedding_failed.call_args[0][0]

    @pytest.mark.asyncio
    async def test_non_last_worker_does_not_enqueue_finalization(self):
        """Non-last workers should not enqueue finalization."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        mock_kb = _make_kb(workers_total=3, workers_completed=0)
        mock_kb.increment_workers_completed = MagicMock(return_value=False)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()

        empty_result = _make_scalar_result([])
        lock_result = _make_lock_result(mock_kb)

        mock_session.execute = AsyncMock(side_effect=[
            empty_result,   # chunk query: empty
            lock_result,    # FOR UPDATE workers_completed
        ])
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("shu.core.workload_routing.enqueue_job", new_callable=AsyncMock) as mock_enqueue,
        ):
            await _handle_re_embed_chunks_job(job)

        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_does_not_mark_kb_error(self):
        """Individual worker failure should NOT mark KB as error.

        Other workers absorb remaining work via competing consumers.
        Only the last worker to complete checks for unprocessed chunks.
        """
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        job.attempts = 3

        mock_kb = _make_kb()

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(side_effect=RuntimeError("GPU OOM"))

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        chunks_result = _make_scalar_result([_make_chunk("c-1")])

        mock_session.execute = AsyncMock(side_effect=[
            chunks_result,  # chunk query with results
        ])
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            with pytest.raises(RuntimeError, match="GPU OOM"):
                await _handle_re_embed_chunks_job(job)

        # KB should NOT be marked as error — other workers will continue
        mock_kb.mark_re_embedding_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_if_kb_status_changes(self):
        """Worker should stop processing if KB status changes mid-loop."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        chunks = [_make_chunk(f"chunk-{i}") for i in range(3)]
        mock_kb = _make_kb(workers_total=2)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024, [0.3] * 1024])

        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=3)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        # Track refresh calls to change status on the 3rd one (second loop iteration's check)
        refresh_count = 0

        async def fake_refresh(obj, attribute_names=None):
            nonlocal refresh_count
            refresh_count += 1
            # 1st: status check (still re_embedding)
            # 2nd: after commit (still re_embedding)
            # 3rd: status check on second iteration — changed to error
            if refresh_count >= 3:
                obj.embedding_status = "error"

        mock_session.refresh = AsyncMock(side_effect=fake_refresh)

        chunks_result = _make_scalar_result(chunks)
        lock_result = _make_lock_result(mock_kb)
        # Not the last worker, so no remaining count query needed
        mock_kb.increment_workers_completed = MagicMock(return_value=False)

        mock_session.execute = AsyncMock(side_effect=[
            chunks_result,  # chunk query: 3 results
            lock_result,    # FOR UPDATE progress increment
            # Loop restarts, refresh detects status change, breaks
            lock_result,    # FOR UPDATE workers_completed
        ])
        mock_session.commit = AsyncMock()

        mock_queue_backend = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("shu.core.queue_backend.get_queue_backend", new_callable=AsyncMock, return_value=mock_queue_backend),
        ):
            await _handle_re_embed_chunks_job(job)

        # Only one batch should have been processed
        mock_embedding.embed_texts.assert_called_once()


class TestHandleReEmbedFinalizeJob:
    """Tests for _handle_re_embed_finalize_job."""

    @pytest.mark.asyncio
    async def test_processes_synopses_queries_and_indexes(self):
        """Finalization should process synopses, queries, indexes, and mark complete."""
        from shu.re_embedding_handler import _handle_re_embed_finalize_job

        job = _make_job(action="re_embed_finalize")
        mock_kb = _make_kb(phase="chunks")

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(return_value=[[0.1] * 1024])
        mock_embedding.embed_queries = AsyncMock(return_value=[[0.2] * 1024])

        mock_vs = AsyncMock()
        mock_vs.store_embeddings = AsyncMock(return_value=1)
        mock_vs.ensure_index = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        synopses_result = _make_scalar_result([_make_document("doc-1")])
        queries_result = _make_scalar_result([_make_query("q-1")])
        empty_result = _make_scalar_result([])

        mock_session.execute = AsyncMock(side_effect=[
            synopses_result, empty_result,  # synopses: data, then empty
            queries_result, empty_result,   # queries: data, then empty
            empty_result,                   # chunk_summaries: empty
        ])
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embed_finalize_job(job)

        mock_embedding.embed_texts.assert_called_once()  # synopses
        mock_embedding.embed_queries.assert_called_once()  # queries
        assert mock_vs.ensure_index.call_count == 4  # chunks, synopses, queries, chunk_summaries
        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")

    @pytest.mark.asyncio
    async def test_empty_synopses_and_queries(self):
        """Finalization with no synopses or queries should still create indexes."""
        from shu.re_embedding_handler import _handle_re_embed_finalize_job

        job = _make_job(action="re_embed_finalize")
        mock_kb = _make_kb(phase="chunks")

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()
        mock_vs.ensure_index = AsyncMock(return_value=True)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        empty_result = _make_scalar_result([])

        mock_session.execute = AsyncMock(return_value=empty_result)
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            await _handle_re_embed_finalize_job(job)

        assert mock_vs.ensure_index.call_count == 4  # chunks, synopses, queries, chunk_summaries
        mock_kb.mark_re_embedding_complete.assert_called_once_with("new-model")

    @pytest.mark.asyncio
    async def test_finalize_failure_marks_kb_error_with_lock(self):
        """Finalization failure on max attempts should mark KB error via FOR UPDATE."""
        from shu.re_embedding_handler import _handle_re_embed_finalize_job

        job = _make_job(action="re_embed_finalize")
        job.attempts = 3

        mock_kb = _make_kb(phase="chunks")

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(side_effect=RuntimeError("DB down"))

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)

        synopses_result = _make_scalar_result([_make_document("doc-1")])
        lock_result = _make_lock_result(mock_kb)

        mock_session.execute = AsyncMock(side_effect=[
            synopses_result,  # synopses query
            lock_result,      # FOR UPDATE on failure path
        ])
        mock_session.commit = AsyncMock()

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
        ):
            with pytest.raises(RuntimeError, match="DB down"):
                await _handle_re_embed_finalize_job(job)

        mock_kb.mark_re_embedding_failed.assert_called_once()


class TestReEmbeddingHeartbeat:
    """Tests for heartbeat lease renewal in re-embedding handlers."""

    @pytest.mark.asyncio
    async def test_heartbeat_started_and_cancelled(self):
        """Heartbeat task should be started before processing and cancelled on completion."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        mock_kb = _make_kb(chunks_total=0)

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()
        empty_result = _make_scalar_result([])
        lock_result = _make_lock_result(mock_kb)
        count_result = _make_count_result(0)
        mock_session.execute = AsyncMock(side_effect=[
            empty_result,   # chunk query: empty
            lock_result,    # FOR UPDATE workers_completed
            count_result,   # remaining chunks count
        ])
        mock_session.commit = AsyncMock()

        mock_queue_backend = AsyncMock()

        created_tasks = []
        original_create_task = asyncio.create_task

        def capture_create_task(coro, **kwargs):
            task = original_create_task(coro, **kwargs)
            created_tasks.append(task)
            return task

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("shu.core.queue_backend.get_queue_backend", new_callable=AsyncMock, return_value=mock_queue_backend),
            patch("asyncio.create_task", side_effect=capture_create_task),
        ):
            await _handle_re_embed_chunks_job(job)

        assert len(created_tasks) == 1
        assert created_tasks[0].cancelled()

    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_on_failure(self):
        """Heartbeat task should be cancelled even when processing fails."""
        from shu.re_embedding_handler import _handle_re_embed_chunks_job

        job = _make_job()
        job.attempts = 1

        mock_kb = _make_kb()

        mock_embedding = AsyncMock()
        mock_embedding.model_name = "new-model"
        mock_embedding.dimension = 1024
        mock_embedding.embed_texts = AsyncMock(side_effect=RuntimeError("GPU OOM"))

        mock_vs = AsyncMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_kb)
        mock_session.refresh = AsyncMock()
        chunks_result = _make_scalar_result([_make_chunk("c-1")])
        mock_session.execute = AsyncMock(side_effect=[
            chunks_result,  # chunk query
        ])
        mock_session.commit = AsyncMock()

        created_tasks = []
        original_create_task = asyncio.create_task

        def capture_create_task(coro, **kwargs):
            task = original_create_task(coro, **kwargs)
            created_tasks.append(task)
            return task

        with (
            patch("shu.core.database.get_async_session_local", return_value=_make_session_factory(mock_session)),
            patch("shu.core.embedding_service.get_embedding_service", new_callable=AsyncMock, return_value=mock_embedding),
            patch("shu.core.vector_store.get_vector_store", new_callable=AsyncMock, return_value=mock_vs),
            patch("asyncio.create_task", side_effect=capture_create_task),
        ):
            with pytest.raises(RuntimeError, match="GPU OOM"):
                await _handle_re_embed_chunks_job(job)

        assert len(created_tasks) == 1
        assert created_tasks[0].cancelled()


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
        assert _index_name("chunks", 384, "ivfflat") == "ix_document_chunks_embedding_ivfflat_384"

    @pytest.mark.asyncio
    async def test_ensure_index_creates_hnsw_partial_index(self):
        from shu.core.vector_store import PgVectorStore

        vs = PgVectorStore(index_type="hnsw")
        mock_db = AsyncMock()

        check_result = MagicMock()
        check_result.scalar_one_or_none.return_value = None
        count_result = MagicMock()
        count_result.scalar_one.return_value = 50
        mock_db.execute = AsyncMock(side_effect=[check_result, count_result, MagicMock()])

        created = await vs.ensure_index("chunks", 1024, db=mock_db)
        assert created is True

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
        check_result.scalar_one_or_none.return_value = 1
        mock_db.execute = AsyncMock(return_value=check_result)

        created = await vs.ensure_index("chunks", 1024, db=mock_db)
        assert created is False
        assert mock_db.execute.call_count == 1
