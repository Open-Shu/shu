"""
Unit tests for embed handler error handling fixes.

Covers:
- KB-not-found fails permanently without retrying
- Document-not-found fails permanently without retrying
- EMBEDDING status set before processing begins
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.models.document import DocumentStatus


class MockJob:
    """Mock job object for testing."""

    def __init__(self, payload: dict, job_id: str = "test-job-embed", attempts: int = 1, max_attempts: int = 3):
        self.id = job_id
        self.payload = payload
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.queue_name = "shu:ingestion_embed"


def _make_embed_job(**overrides):
    payload = {
        "document_id": "doc-123",
        "knowledge_base_id": "kb-456",
        "action": "embed_document",
    }
    payload.update(overrides)
    return MockJob(payload=payload)


def _make_session_with_document(document):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = document

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_session_local, mock_session


class TestEmbedHandlerKBNotFound:
    """KB deleted between OCR and embed stages must fail permanently."""

    @pytest.mark.asyncio
    async def test_kb_not_found_marks_document_error_and_returns(self):
        """
        When process_and_update_chunks raises ValueError (KB not found),
        the document is marked ERROR and the handler returns without raising.
        No retry will occur.
        """
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.title = "Test Doc"
        mock_document.content = "Some content"
        mock_document.update_status = MagicMock()
        mock_document.mark_error = MagicMock()

        mock_session_local, mock_session = _make_session_with_document(mock_document)

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(
            side_effect=ValueError("Knowledge base kb-456 not found")
        )

        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = False

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
        ):
            from shu.worker import _handle_embed_job

            # Must return cleanly — no exception means no retry
            await _handle_embed_job(job)

        mock_document.mark_error.assert_called_once()
        error_msg = mock_document.mark_error.call_args[0][0]
        assert "knowledge base" in error_msg.lower() or "not found" in error_msg.lower()
        mock_session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_kb_not_found_does_not_enqueue_profiling_job(self):
        """
        When KB is not found, no profiling job must be enqueued.
        """
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.title = "Test Doc"
        mock_document.content = "Some content"
        mock_document.update_status = MagicMock()
        mock_document.mark_error = MagicMock()

        mock_session_local, _ = _make_session_with_document(mock_document)

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(
            side_effect=ValueError("Knowledge base not found")
        )

        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = True  # Would enqueue if KB existed

        mock_enqueue_job = AsyncMock()

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        mock_enqueue_job.assert_not_called()


class TestEmbedHandlerDocumentNotFound:
    """Document deleted between OCR and embed stages must fail permanently."""

    @pytest.mark.asyncio
    async def test_document_not_found_returns_without_raising(self):
        """
        When the document is not found in the DB, the handler must return
        without raising (permanent failure — no retry).
        """
        mock_session_local, _ = _make_session_with_document(None)

        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = False

        mock_enqueue_job = AsyncMock()

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
        ):
            from shu.worker import _handle_embed_job

            # Must return cleanly — no exception means no retry
            await _handle_embed_job(job)

        mock_enqueue_job.assert_not_called()


class TestEmbedHandlerEmbeddingStatusBeforeProcessing:
    """EMBEDDING status must be set before process_and_update_chunks runs."""

    @pytest.mark.asyncio
    async def test_embedding_status_set_before_processing(self):
        """
        The handler must call update_status(EMBEDDING) and commit before
        calling process_and_update_chunks, so a crash mid-embed leaves the
        document in a diagnosable EMBEDDING state.
        """
        call_order = []

        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.title = "Test Doc"
        mock_document.content = "Some content"

        def track_update_status(status):
            call_order.append(("update_status", status))

        mock_document.update_status = MagicMock(side_effect=track_update_status)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_document
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def track_commit():
            call_order.append(("commit",))

        mock_session.commit = AsyncMock(side_effect=track_commit)

        mock_session_local = MagicMock()
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        async def track_process(*args, **kwargs):
            call_order.append(("process_and_update_chunks",))
            return (100, 500, 5)

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(side_effect=track_process)

        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = False

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        # Find positions of EMBEDDING status set, commit, and processing
        embedding_idx = next(
            (i for i, e in enumerate(call_order) if e == ("update_status", DocumentStatus.EMBEDDING)),
            None,
        )
        commit_after_embedding_idx = next(
            (i for i, e in enumerate(call_order) if e == ("commit",) and i > (embedding_idx or -1)),
            None,
        )
        process_idx = next(
            (i for i, e in enumerate(call_order) if e == ("process_and_update_chunks",)),
            None,
        )

        assert embedding_idx is not None, "update_status(EMBEDDING) was never called"
        assert commit_after_embedding_idx is not None, "commit after EMBEDDING was never called"
        assert process_idx is not None, "process_and_update_chunks was never called"
        assert embedding_idx < process_idx, (
            f"EMBEDDING status must be set before processing, "
            f"but update_status was at {embedding_idx}, processing at {process_idx}"
        )
        assert commit_after_embedding_idx < process_idx, (
            f"commit must happen before processing, "
            f"but commit was at {commit_after_embedding_idx}, processing at {process_idx}"
        )
