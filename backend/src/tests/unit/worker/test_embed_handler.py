"""
Unit tests for the embedding job handler profiling branch logic.

These tests verify that the _handle_embed_job function correctly routes
to profiling or sets status to READY based on configuration.

Feature: queue-ingestion-pipeline
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.models.document import DocumentStatus


class MockJob:
    """Mock job object for testing."""

    def __init__(self, payload: dict, job_id: str = "test-job-123", attempts: int = 1, max_attempts: int = 3):
        self.id = job_id
        self.payload = payload
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.queue_name = "shu:ingestion_embed"


class TestEmbedHandlerProfilingBranch:
    """Tests for profiling branch logic in _handle_embed_job."""

    @pytest.mark.asyncio
    async def test_profiling_enabled_enqueues_profiling_job(self):
        """
        Test that when profiling is enabled, the handler enqueues a PROFILING job.

        Validates: Requirements 4.5, 4.6
        """
        # Create mock document
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.title = "Test Document"
        mock_document.content = "Test content for embedding"
        mock_document.update_status = MagicMock()

        # Create mock session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_document
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Create mock session context manager
        mock_session_local = MagicMock()
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        # Create mock DocumentService
        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(return_value=(100, 500, 5))

        # Create mock settings with profiling enabled
        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = True

        # Create mock queue backend
        mock_queue = AsyncMock()

        # Create mock enqueue_job
        mock_enqueue_job = AsyncMock()

        job = MockJob(
            payload={
                "document_id": "doc-123",
                "knowledge_base_id": "kb-456",
                "action": "embed_document",
            }
        )

        # Patch at the module level where imports happen
        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.core.queue_backend.get_queue_backend", AsyncMock(return_value=mock_queue)),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        # Verify document status was updated to PROFILING
        mock_document.update_status.assert_called_once_with(DocumentStatus.PROFILING)

        # Verify profiling job was enqueued
        mock_enqueue_job.assert_called_once()
        call_args = mock_enqueue_job.call_args
        assert call_args[0][1].value == "profiling"  # WorkloadType.PROFILING
        assert call_args[1]["payload"]["document_id"] == "doc-123"
        assert call_args[1]["payload"]["action"] == "profile_document"

    @pytest.mark.asyncio
    async def test_profiling_disabled_sets_status_ready(self):
        """
        Test that when profiling is disabled, the handler sets status to READY directly.

        Validates: Requirements 4.7
        """
        # Create mock document
        mock_document = MagicMock()
        mock_document.id = "doc-123"
        mock_document.title = "Test Document"
        mock_document.content = "Test content for embedding"
        mock_document.update_status = MagicMock()

        # Create mock session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_document
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Create mock session context manager
        mock_session_local = MagicMock()
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        # Create mock DocumentService
        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(return_value=(100, 500, 5))

        # Create mock settings with profiling DISABLED
        mock_settings = MagicMock()
        mock_settings.enable_document_profiling = False

        # Create mock enqueue_job to verify it's NOT called
        mock_enqueue_job = AsyncMock()

        job = MockJob(
            payload={
                "document_id": "doc-123",
                "knowledge_base_id": "kb-456",
                "action": "embed_document",
            }
        )

        # Patch at the module level where imports happen
        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_local),
            patch("shu.core.config.get_settings_instance", return_value=mock_settings),
            patch("shu.core.workload_routing.enqueue_job", mock_enqueue_job),
            patch("shu.services.document_service.DocumentService", return_value=mock_doc_service),
        ):
            from shu.worker import _handle_embed_job

            await _handle_embed_job(job)

        # Verify document status was updated to READY (not PROFILING)
        mock_document.update_status.assert_called_once_with(DocumentStatus.PROCESSED)

        # Verify NO profiling job was enqueued
        mock_enqueue_job.assert_not_called()
