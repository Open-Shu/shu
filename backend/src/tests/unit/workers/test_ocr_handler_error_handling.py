"""
Unit tests for OCR handler error handling fixes.

Covers:
- Staging cleanup failure after successful OCR does not mark document ERROR
- Document-not-found in OCR handler fails permanently without retrying
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockJob:
    """Mock job object for testing."""

    def __init__(self, payload: dict, job_id: str = "test-job-ocr", attempts: int = 1, max_attempts: int = 3):
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


def _make_session_with_document(document):
    """Build a mock async session that returns the given document."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = document

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_session_local, mock_session


class TestOCRHandlerStagingCleanupFailure:
    """Staging cleanup failure after successful OCR must not mark document ERROR."""

    @pytest.mark.asyncio
    async def test_staging_delete_failure_does_not_mark_document_error(self):
        """
        When delete_staged_file raises after OCR and enqueue both succeed,
        the document must NOT be marked ERROR. The exception is swallowed and
        a warning is logged.
        """
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.update_status = MagicMock()
        mock_document.mark_error = MagicMock()

        mock_session_local, mock_session = _make_session_with_document(mock_document)

        mock_staging_service = AsyncMock()
        mock_staging_service.retrieve_to_path = AsyncMock(return_value="/tmp/fake_staged.bin")
        mock_staging_service.delete_staged_file = AsyncMock(
            side_effect=Exception("Redis connection lost")
        )

        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(
            return_value={"text": "Extracted text " * 20, "metadata": {}}
        )

        mock_enqueue_job = AsyncMock()
        mock_queue = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=mock_queue)),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
            patch("shu.core.ocr_service.TextExtractor", return_value=mock_extractor),
        ):
            from shu.worker import _handle_ocr_job

            # Must not raise — cleanup failure is non-fatal
            await _handle_ocr_job(job)

        # Document must NOT have been marked ERROR
        mock_document.mark_error.assert_not_called()

        # Embed job must still have been enqueued
        mock_enqueue_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_staging_delete_failure_logs_warning(self, caplog):
        """
        When delete_staged_file raises after successful OCR, a warning is logged
        with the staging key and error details.
        """
        import logging

        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.update_status = MagicMock()
        mock_document.mark_error = MagicMock()

        mock_session_local, mock_session = _make_session_with_document(mock_document)

        mock_staging_service = AsyncMock()
        mock_staging_service.retrieve_to_path = AsyncMock(return_value="/tmp/fake_staged.bin")
        mock_staging_service.delete_staged_file = AsyncMock(
            side_effect=Exception("TTL expired")
        )

        mock_extractor = MagicMock()
        mock_extractor.extract_text = AsyncMock(
            return_value={"text": "Extracted text " * 20, "metadata": {}}
        )

        mock_enqueue_job = AsyncMock()
        mock_queue = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=mock_queue)),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
            patch("shu.core.ocr_service.TextExtractor", return_value=mock_extractor),
        ):
            from shu.worker import _handle_ocr_job

            with caplog.at_level(logging.WARNING, logger="shu.worker"):
                await _handle_ocr_job(job)

        # A warning about the cleanup failure must appear in logs
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("staged file" in m.lower() or "staging" in m.lower() for m in warning_messages), (
            f"Expected a staging cleanup warning, got: {warning_messages}"
        )


class TestOCRHandlerDocumentNotFound:
    """Document-not-found in OCR handler must fail permanently without retrying."""

    @pytest.mark.asyncio
    async def test_document_not_found_returns_without_raising(self):
        """
        When the document is not found in the DB, the handler must return
        without raising (permanent failure — no retry).
        """
        mock_session_local, mock_session = _make_session_with_document(None)

        mock_staging_service = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
        ):
            from shu.worker import _handle_ocr_job

            # Must return cleanly — no exception means no retry
            await _handle_ocr_job(job)

        # Staging service must not have been called (early return)
        mock_staging_service.retrieve_to_path.assert_not_called()

    @pytest.mark.asyncio
    async def test_document_not_found_does_not_enqueue_embed_job(self):
        """
        When the document is not found, no embed job must be enqueued.
        """
        mock_session_local, _ = _make_session_with_document(None)
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


class TestOCRHandlerPassesPathNotBytes:
    """SHU-710 regression guard: the ingestion happy path must resolve the
    staged file to a disk path and call extract_text_with_ocr_fallback with
    ``file_path=...`` (not ``file_bytes=...``). Reading the full file into
    Python memory regresses memory usage by ~file-size per concurrent job.
    """

    @pytest.mark.asyncio
    async def test_ingestion_happy_path_uses_file_path_not_bytes(self):
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.update_status = MagicMock()
        mock_document.mark_error = MagicMock()

        mock_session_local, _ = _make_session_with_document(mock_document)

        mock_staging_service = AsyncMock()
        mock_staging_service.retrieve_to_path = AsyncMock(return_value="/tmp/staged_doc.bin")
        mock_staging_service.delete_staged_file = AsyncMock()

        mock_fallback = AsyncMock(return_value={"text": "Extracted text", "metadata": {}})
        mock_enqueue_job = AsyncMock()

        job = _make_ocr_job()

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.cache_backend.get_cache_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=AsyncMock())),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.file_staging_service.FileStagingService", return_value=mock_staging_service),
            patch("shu.core.ocr_service.extract_text_with_ocr_fallback", mock_fallback),
        ):
            from shu.worker import _handle_ocr_job

            await _handle_ocr_job(job)

        mock_staging_service.retrieve_to_path.assert_awaited_once()
        mock_fallback.assert_awaited_once()
        kwargs = mock_fallback.call_args.kwargs
        assert kwargs.get("file_path") == "/tmp/staged_doc.bin", (
            "Ingestion worker must pass file_path to extract_text_with_ocr_fallback (not file_bytes)"
        )
        assert kwargs.get("file_bytes") is None, (
            "Ingestion worker must not load the staged file into memory"
        )
