"""
Unit tests for KB-deleted early exit in OCR and embed job handlers.

Covers:
- OCR handler discards job and deletes staged file when KB is gone
- OCR handler does not retry on KB-deleted exit
- Embed handler discards job without retry when KB is gone
- Embed handler does not call DocumentService when KB is gone
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockJob:
    """Minimal mock job for testing."""

    def __init__(self, payload: dict, job_id: str = "test-job-001", attempts: int = 1, max_attempts: int = 3):
        self.id = job_id
        self.payload = payload
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.queue_name = "shu:ingestion_ocr"


def _make_ocr_job(**overrides):
    payload = {
        "document_id": "doc-123",
        "knowledge_base_id": "kb-456",
        "staging_key": "file_staging:doc-123",
        "filename": "test.pdf",
        "mime_type": "application/pdf",
        "action": "extract_text",
    }
    payload.update(overrides)
    return MockJob(payload=payload)


def _make_embed_job(**overrides):
    payload = {
        "document_id": "doc-123",
        "knowledge_base_id": "kb-456",
        "action": "embed_document",
    }
    payload.update(overrides)
    return MockJob(payload=payload, job_id="test-job-embed-001")


def _make_session(document, kb):
    """
    Build a mock async session where:
    - session.execute() returns the given document (for select(Document)...)
    - session.get(KnowledgeBase, ...) returns the given kb object
    """
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = document

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=kb)
    mock_session.commit = AsyncMock()

    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_session_local, mock_session


def _make_document(doc_id: str = "doc-123"):
    doc = MagicMock()
    doc.id = doc_id
    doc.update_status = MagicMock()
    doc.mark_error = MagicMock()
    return doc


class TestOCRHandlerKBDeletedEarlyExit:
    """OCR handler must discard job and clean up staging when KB is deleted."""

    @pytest.mark.asyncio
    async def test_ocr_job_discarded_when_kb_deleted(self):
        """
        When the KB is gone, _handle_ocr_job must return without raising
        (no retry) and must not call retrieve_file.
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)

        mock_staging_service = AsyncMock()
        mock_staging_service.retrieve_file = AsyncMock()
        mock_staging_service.delete_staged_file = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
        ):
            from shu.worker import _handle_ocr_job

            # Must return cleanly — no exception means no retry
            await _handle_ocr_job(job)

        # Staged bytes must NOT have been retrieved
        mock_staging_service.retrieve_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_ocr_job_deletes_staged_file_when_kb_deleted(self):
        """
        When the KB is gone, the staged file must be deleted immediately
        to return disk space and prevent stale files.
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)

        mock_staging_service = AsyncMock()
        mock_staging_service.delete_staged_file = AsyncMock()

        job = _make_ocr_job(staging_key="file_staging:doc-123")

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
        ):
            from shu.worker import _handle_ocr_job

            await _handle_ocr_job(job)

        mock_staging_service.delete_staged_file.assert_called_once_with("file_staging:doc-123")

    @pytest.mark.asyncio
    async def test_ocr_job_does_not_enqueue_embed_when_kb_deleted(self):
        """
        When the KB is gone, no embed job must be enqueued.
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)
        mock_enqueue_job = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.services.file_staging_service.FileStagingService", return_value=AsyncMock()),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
        ):
            from shu.worker import _handle_ocr_job

            await _handle_ocr_job(job)

        mock_enqueue_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_ocr_job_staging_delete_failure_is_non_fatal(self):
        """
        If delete_staged_file raises during KB-deleted early exit, the handler
        must still return cleanly (non-fatal — file will TTL-expire).
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)

        mock_staging_service = AsyncMock()
        mock_staging_service.delete_staged_file = AsyncMock(side_effect=Exception("Redis unavailable"))

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
        ):
            from shu.worker import _handle_ocr_job

            # Must not raise even if staging delete fails
            await _handle_ocr_job(job)

    @pytest.mark.asyncio
    async def test_ocr_job_proceeds_normally_when_kb_exists(self):
        """
        When the KB exists, the OCR handler must proceed to retrieve staged bytes
        (i.e., the early exit must not fire for valid jobs).
        """
        document = _make_document()
        mock_kb = MagicMock()
        mock_kb.id = "kb-456"
        mock_session_local, _ = _make_session(document, kb=mock_kb)

        mock_staging_service = AsyncMock()
        mock_staging_service.retrieve_file = AsyncMock(return_value=b"%PDF fake")
        mock_staging_service.delete_staged_file = AsyncMock()

        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(return_value={"text": "text", "metadata": {}})

        mock_enqueue_job = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
            patch("shu.processors.text_extractor.TextExtractor", return_value=mock_extractor),
        ):
            from shu.worker import _handle_ocr_job

            await _handle_ocr_job(job)

        # retrieve_file must have been called — job was not discarded
        mock_staging_service.retrieve_file.assert_called_once()


class TestEmbedHandlerKBDeletedEarlyExit:
    """Embed handler must discard job without retry when KB is deleted."""

    @pytest.mark.asyncio
    async def test_embed_job_discarded_when_kb_deleted(self):
        """
        When the KB is gone, _handle_embed_job must return without raising
        (no retry) and must not call DocumentService.
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)

        mock_doc_service = AsyncMock()
        mock_doc_service.process_and_update_chunks = AsyncMock()

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
        ):
            from shu.worker import _handle_embed_job

            # Must return cleanly — no exception means no retry
            await _handle_embed_job(job)

        mock_doc_service.process_and_update_chunks.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_job_does_not_enqueue_profiling_when_kb_deleted(self):
        """
        When the KB is gone, no profiling job must be enqueued.
        """
        document = _make_document()
        mock_session_local, _ = _make_session(document, kb=None)
        mock_enqueue_job = AsyncMock()

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        mock_enqueue_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_job_proceeds_normally_when_kb_exists(self):
        """
        When the KB exists, the embed handler must proceed to call
        process_and_update_chunks (i.e., the early exit must not fire).
        """
        document = _make_document()
        document.title = "Test Doc"
        document.content = "Some content"

        mock_kb = MagicMock()
        mock_kb.id = "kb-456"
        mock_session_local, _ = _make_session(document, kb=mock_kb)

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(return_value=(100, 500, 5))

        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = False

        job = _make_embed_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=AsyncMock())),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        mock_doc_service.process_and_update_chunks.assert_called_once()
