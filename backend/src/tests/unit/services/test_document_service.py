"""Unit tests for DocumentService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.document_service import DocumentService


class TestProcessAndUpdateChunks:
    """process_and_update_chunks uses the public raw KB fetch path."""

    @pytest.mark.asyncio
    async def test_uses_public_raw_kb_fetch(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()

        service = DocumentService(db)
        service.mark_document_processed = AsyncMock()

        document = MagicMock()
        document.id = "doc-1"

        kb = MagicMock()
        kb.id = "kb-1"
        kb.chunk_size = 256
        kb.chunk_overlap = 32

        fetch_raw_mock = AsyncMock(return_value=kb)
        private_fetch_mock = AsyncMock(side_effect=AssertionError("private KB fetch should not be used"))
        process_document_mock = AsyncMock(return_value=[])

        with patch(
            "shu.services.knowledge_base_service.KnowledgeBaseService.fetch_raw_knowledge_base",
            new=fetch_raw_mock,
        ), patch(
            "shu.services.knowledge_base_service.KnowledgeBaseService._get_knowledge_base",
            new=private_fetch_mock,
        ), patch(
            "shu.core.embedding_service.get_embedding_service",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "shu.services.rag_processing_service.RAGProcessingService.process_document",
            new=process_document_mock,
        ):
            result = await service.process_and_update_chunks(
                knowledge_base_id="kb-1",
                document=document,
                title="Title",
                content="hello world",
            )

        fetch_raw_mock.assert_awaited_once_with("kb-1")
        private_fetch_mock.assert_not_awaited()
        service.mark_document_processed.assert_awaited_once_with(
            "doc-1",
            word_count=2,
            character_count=11,
            chunk_count=0,
        )
        assert result == (2, 11, 0)
