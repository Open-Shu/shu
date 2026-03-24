"""Unit tests for kb_import_handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHandleKBImportJob:
    """Tests for handle_kb_import_job."""

    @pytest.mark.asyncio
    async def test_delegates_to_execute_import(self) -> None:
        job = MagicMock()
        job.id = "job-1"
        job.payload = {
            "knowledge_base_id": "kb-123",
            "archive_path": "/tmp/test.zip",
            "skip_embeddings": True,
        }

        mock_service = MagicMock()
        mock_service.execute_import = AsyncMock()

        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.services.kb_import_export.KBImportExportService", return_value=mock_service),
            patch("shu.services.knowledge_base_service.KnowledgeBaseService"),
        ):
            from shu.workers.kb_import_handler import handle_kb_import_job

            await handle_kb_import_job(job)

        mock_service.execute_import.assert_called_once_with("/tmp/test.zip", "kb-123", True)

    @pytest.mark.asyncio
    async def test_raises_on_missing_payload_fields(self) -> None:
        job = MagicMock()
        job.id = "job-2"
        job.payload = {"knowledge_base_id": "kb-123"}  # missing archive_path

        from shu.workers.kb_import_handler import handle_kb_import_job

        with pytest.raises(ValueError, match="missing required payload fields"):
            await handle_kb_import_job(job)

    @pytest.mark.asyncio
    async def test_defaults_skip_embeddings_to_false(self) -> None:
        job = MagicMock()
        job.id = "job-3"
        job.payload = {
            "knowledge_base_id": "kb-456",
            "archive_path": "/tmp/test2.zip",
        }

        mock_service = MagicMock()
        mock_service.execute_import = AsyncMock()

        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("shu.core.database.get_async_session_local", return_value=mock_session_factory),
            patch("shu.services.kb_import_export.KBImportExportService", return_value=mock_service),
            patch("shu.services.knowledge_base_service.KnowledgeBaseService"),
        ):
            from shu.workers.kb_import_handler import handle_kb_import_job

            await handle_kb_import_job(job)

        mock_service.execute_import.assert_called_once_with("/tmp/test2.zip", "kb-456", False)
