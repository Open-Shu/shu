"""Unit tests for DocumentService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.document_service import DocumentService


class TestProcessAndUpdateChunks:
    """process_and_update_chunks uses the public raw KB fetch path."""

    @pytest.mark.asyncio
    async def test_uses_public_raw_kb_fetch(self) -> None:
        db = AsyncMock()
        db.add = MagicMock()

        service = DocumentService(db)

        document = MagicMock()
        document.id = "doc-1"
        document.update_content_stats = MagicMock()

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
        document.update_content_stats.assert_called_once_with(2, 11, 0)
        assert result == (2, 11, 0)


# SHU-776: document_count_limit enforcement on create_document. The gate runs
# only for genuinely new documents — an existing (idempotent) row returns
# first, leaving the count unchanged. Self-hosted (no cache) bypasses entirely.

_P_STATE_SERVICE = "shu.billing.state_service.BillingStateService.get_for_update"
_P_KB_VERIFY = "shu.utils.KnowledgeBaseVerifier.verify_exists"
_P_FROM_ORM = "shu.services.document_service.DocumentResponse.from_orm"


def _doc_create(**overrides):
    from shu.schemas.document import DocumentCreate

    base = dict(
        title="Doc",
        file_type="txt",
        source_type="manual",
        source_id="src-1",
        knowledge_base_id="kb-1",
        content="hello world",
    )
    base.update(overrides)
    return DocumentCreate(**base)


def _state_with_doc_limit(cap: int):
    import dataclasses

    from shu.billing.cp_client import HEALTHY_DEFAULT
    from shu.billing.entitlements import LimitSet

    return dataclasses.replace(HEALTHY_DEFAULT, limits=LimitSet(document_count_limit=cap))


def _db_for_create(count: int, existing=None) -> AsyncMock:
    """db mock: existing-doc lookup → `existing`; cap count query → `count`."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    result.scalar.return_value = count
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@patch(_P_STATE_SERVICE, new_callable=AsyncMock)
class TestCreateDocumentLimit:
    """create_document honours document_count_limit when a billing cache is present."""

    @pytest.mark.asyncio
    async def test_at_cap_raises_and_adds_nothing(self, _mock_lock, install_stub_cache):
        from shu.billing.entitlements import LimitExceededError

        install_stub_cache(_state_with_doc_limit(10))
        db = _db_for_create(count=10)
        service = DocumentService(db)

        with patch(_P_KB_VERIFY, new=AsyncMock()):
            with pytest.raises(LimitExceededError):
                await service.create_document(_doc_create())
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_under_cap_creates(self, _mock_lock, install_stub_cache):
        install_stub_cache(_state_with_doc_limit(10))
        db = _db_for_create(count=3)
        service = DocumentService(db)

        with patch(_P_KB_VERIFY, new=AsyncMock()), patch(_P_FROM_ORM, return_value=MagicMock()):
            await service.create_document(_doc_create())
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_document_bypasses_cap(self, _mock_lock, install_stub_cache):
        """Re-ingesting an existing doc returns it without the cap check: the
        count is unchanged, so even cap=0 must not block this path.
        """
        install_stub_cache(_state_with_doc_limit(0))
        db = _db_for_create(count=0, existing=MagicMock())
        service = DocumentService(db)

        with patch(_P_KB_VERIFY, new=AsyncMock()), patch(_P_FROM_ORM, return_value="existing-response"):
            result = await service.create_document(_doc_create())
        assert result == "existing-response"
        db.add.assert_not_called()
