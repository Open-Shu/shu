"""Unit tests for KnowledgeBaseService.

Covers performance-optimized stats recalculation and create_knowledge_base
behavior, including the SHU-742 Personal Knowledge defaults flow.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shu.models.document import Document
from shu.schemas.knowledge_base import KnowledgeBaseCreate
from shu.services.knowledge_base_service import KnowledgeBaseService


class TestRecalculateKBStats:
    """Tests for recalculate_kb_stats method."""

    @pytest.mark.asyncio
    async def test_recalculate_kb_stats_updates_denormalized_columns(self):
        """Verify recalculate_kb_stats updates KB document_count and total_chunks."""


        # Mock database session
        mock_db = AsyncMock()

        # Mock document count query result
        doc_count_result = MagicMock()
        doc_count_result.scalar.return_value = 42

        # Mock chunk count query result
        chunk_count_result = MagicMock()
        chunk_count_result.scalar.return_value = 1500

        # Mock KB object
        mock_kb = MagicMock()
        mock_kb.document_count = 0
        mock_kb.total_chunks = 0

        # Configure mock_db.execute to return different results for different queries
        mock_db.execute = AsyncMock(side_effect=[doc_count_result, chunk_count_result])
        mock_db.get = AsyncMock(return_value=mock_kb)
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        result = await service.recalculate_kb_stats("test-kb-id")

        # Verify the KB's update_document_stats was called with correct values
        mock_kb.update_document_stats.assert_called_once_with(42, 1500)

        # Verify commit was called
        mock_db.commit.assert_called_once()

        # Verify return value
        assert result == {"document_count": 42, "total_chunks": 1500}

    @pytest.mark.asyncio
    async def test_recalculate_kb_stats_handles_empty_kb(self):
        """Verify recalculate_kb_stats handles KB with no documents."""


        mock_db = AsyncMock()

        # Mock empty results
        doc_count_result = MagicMock()
        doc_count_result.scalar.return_value = 0

        chunk_count_result = MagicMock()
        chunk_count_result.scalar.return_value = 0

        mock_kb = MagicMock()

        mock_db.execute = AsyncMock(side_effect=[doc_count_result, chunk_count_result])
        mock_db.get = AsyncMock(return_value=mock_kb)
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        result = await service.recalculate_kb_stats("empty-kb-id")

        mock_kb.update_document_stats.assert_called_once_with(0, 0)
        assert result == {"document_count": 0, "total_chunks": 0}

    @pytest.mark.asyncio
    async def test_recalculate_kb_stats_handles_null_scalars(self):
        """Verify recalculate_kb_stats handles NULL scalar results gracefully."""


        mock_db = AsyncMock()

        # Mock NULL results (can happen with empty tables)
        doc_count_result = MagicMock()
        doc_count_result.scalar.return_value = None

        chunk_count_result = MagicMock()
        chunk_count_result.scalar.return_value = None

        mock_kb = MagicMock()

        mock_db.execute = AsyncMock(side_effect=[doc_count_result, chunk_count_result])
        mock_db.get = AsyncMock(return_value=mock_kb)
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        result = await service.recalculate_kb_stats("null-kb-id")

        # Should default to 0 when scalar returns None
        mock_kb.update_document_stats.assert_called_once_with(0, 0)
        assert result == {"document_count": 0, "total_chunks": 0}

    @pytest.mark.asyncio
    async def test_recalculate_kb_stats_handles_missing_kb(self):
        """Verify recalculate_kb_stats handles non-existent KB gracefully."""


        mock_db = AsyncMock()

        doc_count_result = MagicMock()
        doc_count_result.scalar.return_value = 5

        chunk_count_result = MagicMock()
        chunk_count_result.scalar.return_value = 50

        mock_db.execute = AsyncMock(side_effect=[doc_count_result, chunk_count_result])
        mock_db.get = AsyncMock(return_value=None)  # KB not found
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        result = await service.recalculate_kb_stats("missing-kb-id")

        # Should still return the counts even if KB not found
        assert result == {"document_count": 5, "total_chunks": 50}
        # Commit should not be called if KB is None
        mock_db.commit.assert_not_called()


class TestGetOverallKnowledgeBaseStats:
    """Tests for get_overall_knowledge_base_stats aggregation fix."""

    @pytest.mark.asyncio
    async def test_aggregates_from_denormalized_stats(self):
        """Verify get_overall_knowledge_base_stats aggregates from KB columns."""


        mock_db = AsyncMock()

        # Mock query results in order:
        # 1. total KB count
        # 2. active KB count
        # 3. sync enabled count
        # 4. SUM(document_count)
        # 5. SUM(total_chunks)
        results = [
            MagicMock(scalar=MagicMock(return_value=5)),   # total KBs
            MagicMock(scalar=MagicMock(return_value=4)),   # active KBs
            MagicMock(scalar=MagicMock(return_value=3)),   # sync enabled
            MagicMock(scalar=MagicMock(return_value=150)), # total documents
            MagicMock(scalar=MagicMock(return_value=5000)), # total chunks
        ]
        mock_db.execute = AsyncMock(side_effect=results)

        service = KnowledgeBaseService(mock_db)
        stats = await service.get_overall_knowledge_base_stats()

        assert stats["total_knowledge_bases"] == 5
        assert stats["active_knowledge_bases"] == 4
        assert stats["sync_enabled_count"] == 3
        assert stats["total_documents"] == 150
        assert stats["total_chunks"] == 5000
        assert stats["status_breakdown"] == {"active": 4, "inactive": 1}

    @pytest.mark.asyncio
    async def test_handles_null_sums(self):
        """Verify get_overall_knowledge_base_stats handles NULL sums (no KBs)."""


        mock_db = AsyncMock()

        results = [
            MagicMock(scalar=MagicMock(return_value=0)),    # total KBs
            MagicMock(scalar=MagicMock(return_value=0)),    # active KBs
            MagicMock(scalar=MagicMock(return_value=0)),    # sync enabled
            MagicMock(scalar=MagicMock(return_value=None)), # NULL sum
            MagicMock(scalar=MagicMock(return_value=None)), # NULL sum
        ]
        mock_db.execute = AsyncMock(side_effect=results)

        service = KnowledgeBaseService(mock_db)
        stats = await service.get_overall_knowledge_base_stats()

        # Should default to 0 when SUM returns NULL
        assert stats["total_documents"] == 0
        assert stats["total_chunks"] == 0


class TestGetDocumentFilterCondition:
    """Tests for document filter condition (title-only search)."""

    def test_search_uses_title_only(self):
        """Verify search filter only searches title, not content."""


        mock_db = MagicMock()
        service = KnowledgeBaseService(mock_db)

        condition = service.get_document_filter_condition(
            kb_id="test-kb",
            search_query="test search",
            filter_by="all"
        )

        # Convert to string to inspect the condition
        condition_str = str(condition)

        # Should contain title ILIKE
        assert "title" in condition_str.lower()
        # Should NOT contain content ILIKE (removed for performance)
        # The condition should be an AND of kb_id filter and title filter only


class TestDocumentToListDict:
    """Tests for Document.to_list_dict lightweight serialization."""

    def test_excludes_heavy_fields(self):
        """Verify to_list_dict excludes content, embeddings, and other heavy fields."""


        doc = Document(
            id="test-id",
            knowledge_base_id="kb-id",
            title="Test Document",
            source_type="plugin:test",
            source_id="src-123",
            file_type="pdf",
            content="This is a very long content string that should not be in list view...",
            processing_status="processed",
        )
        # Set heavy fields that should be excluded
        doc.synopsis = "A synopsis that might be included in full view"
        doc.capability_manifest = {"answers_questions_about": ["topic1", "topic2"]}
        doc.extraction_metadata = {"pages": 100, "details": "lots of data"}

        result = doc.to_list_dict()

        # Should include essential fields
        assert result["id"] == "test-id"
        assert result["title"] == "Test Document"
        assert result["source_type"] == "plugin:test"
        assert result["processing_status"] == "processed"

        # Should NOT include heavy fields
        assert "content" not in result
        assert "synopsis" not in result
        assert "synopsis_embedding" not in result
        assert "capability_manifest" not in result
        assert "extraction_metadata" not in result
        assert "source_metadata" not in result
        assert "relational_context" not in result

    def test_includes_all_list_view_fields(self):
        """Verify to_list_dict includes all fields needed for list views."""

        from datetime import datetime, UTC

        now = datetime.now(UTC)
        doc = Document(
            id="test-id",
            knowledge_base_id="kb-id",
            title="Test Document",
            source_type="plugin:test",
            source_id="src-123",
            file_type="pdf",
            file_size=1024,
            mime_type="application/pdf",
            content="content",
            processing_status="processed",
            extraction_method="ocr",
            extraction_engine="paddleocr",
            extraction_confidence=0.95,
            source_url="https://example.com/doc.pdf",
            word_count=500,
            character_count=3000,
            chunk_count=10,
            document_type="technical",
            profiling_status="complete",
        )
        doc.created_at = now
        doc.updated_at = now
        doc.processed_at = now
        doc.source_modified_at = now

        result = doc.to_list_dict()

        # Verify all expected fields are present
        expected_fields = [
            "id", "knowledge_base_id", "title", "source_type", "source_id",
            "file_type", "file_size", "mime_type", "processing_status",
            "processing_error", "extraction_method", "extraction_engine",
            "extraction_confidence", "source_url", "word_count", "character_count",
            "chunk_count", "document_type", "profiling_status",
            "created_at", "updated_at", "processed_at", "source_modified_at"
        ]
        for field in expected_fields:
            assert field in result, f"Missing field: {field}"


class TestAdjustDocumentStats:
    """Tests for adjust_document_stats method."""

    @pytest.mark.asyncio
    async def test_adjust_document_stats_increments(self):
        """Verify adjust_document_stats increments counts correctly."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        await service.adjust_document_stats("test-kb-id", doc_delta=1, chunk_delta=10)

        # Verify execute was called with an UPDATE statement
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_adjust_document_stats_decrements(self):
        """Verify adjust_document_stats decrements counts correctly."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        await service.adjust_document_stats("test-kb-id", doc_delta=-1, chunk_delta=-15)

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_adjust_document_stats_skips_zero_delta(self):
        """Verify adjust_document_stats does nothing when both deltas are zero."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        await service.adjust_document_stats("test-kb-id", doc_delta=0, chunk_delta=0)

        # Should not execute any queries when deltas are zero
        mock_db.execute.assert_not_called()
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_adjust_document_stats_handles_doc_only(self):
        """Verify adjust_document_stats works with only doc_delta."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        await service.adjust_document_stats("test-kb-id", doc_delta=5, chunk_delta=0)

        # Should still execute since doc_delta is non-zero
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_adjust_document_stats_handles_chunk_only(self):
        """Verify adjust_document_stats works with only chunk_delta."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        service = KnowledgeBaseService(mock_db)
        await service.adjust_document_stats("test-kb-id", doc_delta=0, chunk_delta=100)

        # Should still execute since chunk_delta is non-zero
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_adjust_document_stats_rollback_on_error(self):
        """Verify adjust_document_stats rolls back on error."""


        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))
        mock_db.rollback = AsyncMock()

        service = KnowledgeBaseService(mock_db)

        with pytest.raises(Exception, match="DB error"):
            await service.adjust_document_stats("test-kb-id", doc_delta=1, chunk_delta=10)

        mock_db.rollback.assert_called_once()


class TestCreatePersonalKnowledgeBaseDefaults:
    """SHU-742: Personal Knowledge KBs apply config-sourced defaults at create time."""

    def _build_service(self, mock_db, captured):
        """Build a service whose db.add captures the KB passed to it.

        ``_get_kb_by_slug`` is patched to return None so the create path
        proceeds through to instantiation without a slug-conflict early exit.
        """
        service = KnowledgeBaseService(mock_db)
        service._get_kb_by_slug = AsyncMock(return_value=None)

        def _capture_add(obj):
            captured.append(obj)

        mock_db.add = MagicMock(side_effect=_capture_add)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_personal_kb_applies_full_doc_fetch_default_from_config(self):
        """is_personal=True copies personal_kb_rag_fetch_full_documents onto the KB row."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        mock_settings = MagicMock()
        mock_settings.personal_kb_rag_fetch_full_documents = True

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            await service.create_knowledge_base(kb_data, owner_id="user-1")

        assert len(captured) == 1
        kb = captured[0]
        assert kb.is_personal is True
        assert kb.rag_fetch_full_documents is True
        assert kb.owner_id == "user-1"

    @pytest.mark.asyncio
    async def test_non_personal_kb_does_not_apply_personal_defaults(self):
        """is_personal=False leaves rag_fetch_full_documents at its model default (None)."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        kb_data = KnowledgeBaseCreate(name="Project Alpha")  # is_personal defaults to False

        await service.create_knowledge_base(kb_data, owner_id="user-1")

        assert len(captured) == 1
        kb = captured[0]
        assert kb.is_personal is False
        # rag_fetch_full_documents is nullable; we must NOT set it explicitly
        # so the centralized cascade in ConfigurationManager remains in charge.
        assert kb.rag_fetch_full_documents is None

    @pytest.mark.asyncio
    async def test_personal_kb_respects_config_value_false(self):
        """If ops disable the personal default via config, the KB reflects that."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        mock_settings = MagicMock()
        mock_settings.personal_kb_rag_fetch_full_documents = False

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            await service.create_knowledge_base(kb_data, owner_id="user-1")

        kb = captured[0]
        assert kb.is_personal is True
        assert kb.rag_fetch_full_documents is False

    @pytest.mark.asyncio
    async def test_personal_kb_slug_is_owner_scoped(self):
        """Personal KB slugs are derived from owner_id, not the display name."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)
        await service.create_knowledge_base(kb_data, owner_id="user-aaaa")

        kb = captured[0]
        assert kb.slug == "personal-knowledge-user-aaaa"

    @pytest.mark.asyncio
    async def test_two_users_with_same_name_can_both_create_personal_kbs(self):
        """Two distinct owners with colliding display names each get unique slugs."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        await service.create_knowledge_base(
            KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True),
            owner_id="user-aaaa",
        )
        await service.create_knowledge_base(
            KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True),
            owner_id="user-bbbb",
        )

        assert len(captured) == 2
        assert captured[0].slug == "personal-knowledge-user-aaaa"
        assert captured[1].slug == "personal-knowledge-user-bbbb"
        # Display name allowed to collide; the slug uniqueness check uses
        # the slug only, so neither create raises ConflictError.
        assert captured[0].name == captured[1].name == "Eric's Knowledge"

    @pytest.mark.asyncio
    async def test_non_personal_kb_still_uses_name_based_slug(self):
        """Non-personal KBs use the historic name-derived slug, unchanged."""
        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        kb_data = KnowledgeBaseCreate(name="Project Alpha", is_personal=False)
        await service.create_knowledge_base(kb_data, owner_id="user-1")

        kb = captured[0]
        assert kb.slug == "project-alpha"

    @pytest.mark.asyncio
    async def test_personal_kb_without_owner_id_raises_validation_error(self):
        """is_personal=True with no owner_id is invalid — would create an orphan."""
        from shu.core.exceptions import ValidationError

        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)

        with pytest.raises(ValidationError, match="owner_id"):
            await service.create_knowledge_base(kb_data, owner_id=None)

        # Nothing should have been added to the session.
        assert captured == []

    @staticmethod
    def _slug_violation_orig():
        """Build an asyncpg-shaped error with constraint_name set to the slug index."""
        orig = MagicMock()
        orig.constraint_name = "knowledge_bases_slug_key"
        return orig

    @staticmethod
    def _fk_violation_orig():
        """Build an asyncpg-shaped error with constraint_name set to a non-slug FK."""
        orig = MagicMock()
        orig.constraint_name = "knowledge_bases_owner_id_fkey"
        return orig

    @pytest.mark.asyncio
    async def test_personal_kb_concurrent_create_returns_existing_row(self):
        """IntegrityError on commit (slug race) → fetch winning row, succeed idempotently."""
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        # First _get_kb_by_slug (pre-insert) finds nothing → proceed to commit.
        # Second call (post-IntegrityError, after race) returns the winning row.
        existing_kb = MagicMock(name="ExistingPersonalKB")
        service._get_kb_by_slug = AsyncMock(side_effect=[None, existing_kb])

        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, self._slug_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)
        result = await service.create_knowledge_base(kb_data, owner_id="user-1")

        assert result is existing_kb
        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_personal_kb_concurrent_create_raises_conflict(self):
        """IntegrityError on commit for non-personal KB → ConflictError (not idempotent)."""
        from shu.core.exceptions import ConflictError
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        service._get_kb_by_slug = AsyncMock(return_value=None)
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, self._slug_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        kb_data = KnowledgeBaseCreate(name="Project Alpha", is_personal=False)

        with pytest.raises(ConflictError):
            await service.create_knowledge_base(kb_data, owner_id="user-1")

        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_slug_integrity_error_propagates_as_shu_exception(self):
        """FK or other constraint violations must not masquerade as a slug conflict."""
        from shu.core.exceptions import ConflictError, ShuException
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = self._build_service(mock_db, captured)

        service._get_kb_by_slug = AsyncMock(return_value=None)
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, self._fk_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        kb_data = KnowledgeBaseCreate(name="Eric's Knowledge", is_personal=True)

        with pytest.raises(ShuException) as exc_info:
            await service.create_knowledge_base(kb_data, owner_id="user-1")

        # Must NOT have been converted to a 409 ConflictError.
        assert not isinstance(exc_info.value, ConflictError)
        assert exc_info.value.error_code == "KNOWLEDGE_BASE_CREATE_ERROR"
        mock_db.rollback.assert_awaited_once()
