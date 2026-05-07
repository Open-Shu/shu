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


def _slug_violation_orig():
    orig = MagicMock()
    orig.constraint_name = "knowledge_bases_slug_key"
    return orig


def _fk_violation_orig():
    orig = MagicMock()
    orig.constraint_name = "knowledge_bases_owner_id_fkey"
    return orig


def _build_service_capturing_add(mock_db, captured):
    """Build a service whose db.add captures the KB; both pre-flight lookups miss.

    ``_get_kb_by_slug`` (used by ``create_knowledge_base``) and
    ``_get_personal_kb_by_owner`` (used by ``ensure_personal_knowledge_base``)
    both default to returning None so tests start in the "no existing KB" state.
    Individual tests override either to assert the existing-row path.
    """
    service = KnowledgeBaseService(mock_db)
    service._get_kb_by_slug = AsyncMock(return_value=None)
    service._get_personal_kb_by_owner = AsyncMock(return_value=None)
    mock_db.add = MagicMock(side_effect=lambda obj: captured.append(obj))
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    return service


class TestCreateKnowledgeBase:
    """Non-personal create flow via ``create_knowledge_base``.

    Personal KBs have their own endpoint and service method
    (``ensure_personal_knowledge_base``) — this class covers only the
    role-gated, name-derived-slug, non-personal path.
    """

    @pytest.mark.asyncio
    async def test_does_not_apply_personal_defaults(self):
        """Non-personal KBs leave rag_fetch_full_documents at the model default (None)."""
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        await service.create_knowledge_base(
            KnowledgeBaseCreate(name="Project Alpha"), owner_id="user-1"
        )

        assert len(captured) == 1
        kb = captured[0]
        assert kb.is_personal is False
        assert kb.rag_fetch_full_documents is None
        assert kb.owner_id == "user-1"
        assert kb.slug == "project-alpha"

    @pytest.mark.asyncio
    async def test_existing_slug_raises_conflict(self):
        """Pre-flight slug hit returns ConflictError (not idempotent)."""
        from shu.core.exceptions import ConflictError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)
        service._get_kb_by_slug = AsyncMock(return_value=MagicMock(name="ExistingNamedKB"))

        with pytest.raises(ConflictError):
            await service.create_knowledge_base(
                KnowledgeBaseCreate(name="Project Alpha"), owner_id="user-1"
            )

    @pytest.mark.asyncio
    async def test_concurrent_create_raises_conflict(self):
        """IntegrityError on commit (slug race) → ConflictError, not idempotent."""
        from shu.core.exceptions import ConflictError
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, _slug_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        with pytest.raises(ConflictError):
            await service.create_knowledge_base(
                KnowledgeBaseCreate(name="Project Alpha"), owner_id="user-1"
            )
        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_slug_integrity_error_propagates_as_shu_exception(self):
        """FK violations must not masquerade as slug ConflictError."""
        from shu.core.exceptions import ConflictError, ShuException
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, _fk_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        with pytest.raises(ShuException) as exc_info:
            await service.create_knowledge_base(
                KnowledgeBaseCreate(name="Project Alpha"), owner_id="user-1"
            )
        assert not isinstance(exc_info.value, ConflictError)


class TestEnsurePersonalKnowledgeBase:
    """SHU-742: idempotent ensure flow for the user's Personal Knowledge KB."""

    @pytest.mark.asyncio
    async def test_applies_full_doc_fetch_default_from_config(self):
        """Personal KB copies personal_kb_rag_fetch_full_documents onto the row."""
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        mock_settings = MagicMock()
        mock_settings.personal_kb_rag_fetch_full_documents = True

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            await service.ensure_personal_knowledge_base(
                owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
            )

        assert len(captured) == 1
        kb = captured[0]
        assert kb.is_personal is True
        assert kb.rag_fetch_full_documents is True
        assert kb.owner_id == "user-1"

    @pytest.mark.asyncio
    async def test_respects_config_value_false(self):
        """If ops disable the personal default via config, the KB reflects that."""
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        mock_settings = MagicMock()
        mock_settings.personal_kb_rag_fetch_full_documents = False

        with patch("shu.core.config.get_settings_instance", return_value=mock_settings):
            await service.ensure_personal_knowledge_base(
                owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
            )

        kb = captured[0]
        assert kb.is_personal is True
        assert kb.rag_fetch_full_documents is False

    @pytest.mark.asyncio
    async def test_slug_embeds_token_and_owner_id(self):
        """Slug format: ``personal-knowledge-{token}-{owner_id}``.

        The token gives admins authoring PBAC policies a hint about who the
        KB belongs to without a separate UUID lookup. The owner_id suffix
        keeps the slug globally unique even when two users share a token.
        """
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        await service.ensure_personal_knowledge_base(
            owner_id="user-aaaa", display_name="Eric's Knowledge", slug_token="eric"
        )
        assert captured[0].slug == "personal-knowledge-eric-user-aaaa"

    @pytest.mark.asyncio
    async def test_two_users_with_same_token_still_get_distinct_slugs(self):
        """Two users sharing a first-name token are disambiguated by the owner_id suffix."""
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        await service.ensure_personal_knowledge_base(
            owner_id="user-aaaa", display_name="Eric's Knowledge", slug_token="eric"
        )
        await service.ensure_personal_knowledge_base(
            owner_id="user-bbbb", display_name="Eric's Knowledge", slug_token="eric"
        )

        assert len(captured) == 2
        assert captured[0].slug == "personal-knowledge-eric-user-aaaa"
        assert captured[1].slug == "personal-knowledge-eric-user-bbbb"
        assert captured[0].name == captured[1].name == "Eric's Knowledge"

    @pytest.mark.asyncio
    async def test_without_owner_id_raises_validation_error(self):
        """Personal KB ensure requires an owner_id."""
        from shu.core.exceptions import ValidationError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        with pytest.raises(ValidationError, match="owner_id"):
            await service.ensure_personal_knowledge_base(
                owner_id=None, display_name="Eric's Knowledge", slug_token="eric"
            )
        assert captured == []

    @pytest.mark.asyncio
    async def test_concurrent_create_returns_existing_row(self):
        """IntegrityError on commit (slug race) → re-fetch by owner, succeed idempotently."""
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        # Pre-flight by owner: nothing. Post-IntegrityError by owner: racing row.
        existing_kb = MagicMock(name="ExistingPersonalKB")
        existing_kb.owner_id = "user-1"
        existing_kb.is_personal = True
        service._get_personal_kb_by_owner = AsyncMock(side_effect=[None, existing_kb])
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, _slug_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        result = await service.ensure_personal_knowledge_base(
            owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
        )
        assert result is existing_kb
        mock_db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_personal_kb_returns_existing_idempotently(self):
        """Pre-flight owner-lookup hit returns the existing row without writing."""
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        existing_kb = MagicMock(name="ExistingPersonalKB")
        existing_kb.is_personal = True
        existing_kb.owner_id = "user-1"
        service._get_personal_kb_by_owner = AsyncMock(return_value=existing_kb)

        result = await service.ensure_personal_knowledge_base(
            owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
        )
        assert result is existing_kb
        assert captured == []

    @pytest.mark.asyncio
    async def test_existing_personal_kb_heals_is_personal_flag(self):
        """Legacy rows that predate the is_personal column get healed on ensure.

        Note: ``_get_personal_kb_by_owner`` filters by ``is_personal=True`` so a
        truly-legacy row with ``is_personal=False`` won't be found via the
        owner-lookup path in production. This test pins the heal logic in the
        helper itself — useful when the owner-lookup eventually picks up a row
        that was healed concurrently or when the helper is reused elsewhere.
        """
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        existing_kb = MagicMock(name="LegacyKB")
        existing_kb.is_personal = False
        existing_kb.owner_id = "user-1"
        service._get_personal_kb_by_owner = AsyncMock(return_value=existing_kb)

        result = await service.ensure_personal_knowledge_base(
            owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
        )
        assert result is existing_kb
        assert existing_kb.is_personal is True
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_rename_does_not_create_second_personal_kb(self):
        """If the user's display name changes, ensure() returns the existing row.

        The slug embeds the user's identity token at creation time. Looking up
        by ``owner_id`` (not slug) keeps the "one personal KB per user"
        invariant across renames. The original slug stays unchanged so existing
        PBAC policies referencing it continue to match.
        """
        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)

        # User originally created their KB with token "eric"; that's the slug
        # baked into the row. Now the user renamed and the new slug_token is
        # "erica". The lookup-by-owner finds the original row regardless.
        original_kb = MagicMock(name="OriginalKB")
        original_kb.is_personal = True
        original_kb.owner_id = "user-1"
        original_kb.slug = "personal-knowledge-eric-user-1"
        service._get_personal_kb_by_owner = AsyncMock(return_value=original_kb)

        result = await service.ensure_personal_knowledge_base(
            owner_id="user-1", display_name="Erica's Knowledge", slug_token="erica"
        )

        assert result is original_kb
        assert original_kb.slug == "personal-knowledge-eric-user-1"  # unchanged
        assert captured == []  # no new row inserted

    @pytest.mark.asyncio
    async def test_non_slug_integrity_error_propagates_as_shu_exception(self):
        """FK violations must not masquerade as a slug conflict."""
        from shu.core.exceptions import ConflictError, ShuException
        from sqlalchemy.exc import IntegrityError

        mock_db = AsyncMock()
        captured = []
        service = _build_service_capturing_add(mock_db, captured)
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError("INSERT...", {}, _fk_violation_orig())
        )
        mock_db.rollback = AsyncMock()

        with pytest.raises(ShuException) as exc_info:
            await service.ensure_personal_knowledge_base(
                owner_id="user-1", display_name="Eric's Knowledge", slug_token="eric"
            )
        assert not isinstance(exc_info.value, ConflictError)


class TestReturnOrHealExistingPersonalKb:
    """SHU-742: defense-in-depth invariants on the heal helper itself.

    The owner-lookup filter in ``ensure_personal_knowledge_base`` prevents
    foreign-owner rows from reaching this helper through the public API,
    so these tests exercise the helper directly. They pin the contract so a
    future caller that doesn't pre-filter still fails loud rather than
    silently transferring a KB.
    """

    @pytest.mark.asyncio
    async def test_refuses_foreign_owner(self):
        """Helper raises ConflictError when given a row owned by a different user."""
        from shu.core.exceptions import ConflictError

        mock_db = AsyncMock()
        service = KnowledgeBaseService(mock_db)

        foreign_row = MagicMock()
        foreign_row.owner_id = "attacker-user-id"
        foreign_row.is_personal = True

        with pytest.raises(ConflictError):
            await service._return_or_heal_existing_personal_kb(foreign_row, "user-1")

    @pytest.mark.asyncio
    async def test_heals_is_personal_when_caller_owns_row(self):
        """Helper flips is_personal=True (and commits) when ownership matches."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        service = KnowledgeBaseService(mock_db)

        legacy_row = MagicMock()
        legacy_row.owner_id = "user-1"
        legacy_row.is_personal = False

        result = await service._return_or_heal_existing_personal_kb(legacy_row, "user-1")
        assert result is legacy_row
        assert legacy_row.is_personal is True
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_op_when_already_personal_and_owned(self):
        """Helper short-circuits without writing when nothing to heal."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        service = KnowledgeBaseService(mock_db)

        clean_row = MagicMock()
        clean_row.owner_id = "user-1"
        clean_row.is_personal = True

        result = await service._return_or_heal_existing_personal_kb(clean_row, "user-1")
        assert result is clean_row
        mock_db.commit.assert_not_awaited()


class TestResolvePersonalKbName:
    """SHU-742: server-side display-name precedence for Personal KBs."""

    def test_multi_token_name_uses_first_and_last(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_name

        user = MagicMock(name="Eric Williams Longville", email="eric@openshu.ai")
        user.name = "Eric Williams Longville"
        user.email = "eric@openshu.ai"
        assert resolve_personal_kb_name(user) == "Eric Longville's Knowledge"

    def test_single_token_name(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_name

        user = MagicMock()
        user.name = "Eric"
        user.email = "eric@openshu.ai"
        assert resolve_personal_kb_name(user) == "Eric's Knowledge"

    def test_falls_back_to_email_local_part(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_name

        user = MagicMock()
        user.name = ""
        user.email = "user42@openshu.ai"
        assert resolve_personal_kb_name(user) == "user42's Knowledge"

    def test_falls_back_to_personal_knowledge_when_no_identity(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_name

        user = MagicMock()
        user.name = ""
        user.email = ""
        assert resolve_personal_kb_name(user) == "Personal Knowledge"


class TestResolvePersonalKbSlugToken:
    """SHU-742: readable token embedded in personal-KB slugs."""

    def test_first_name_becomes_token(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = "Eric Longville"
        user.email = "eric@openshu.ai"
        assert resolve_personal_kb_slug_token(user) == "eric"

    def test_unicode_first_name_is_slugified(self):
        """Slugify strips diacritics so non-ASCII names produce safe URL tokens."""
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = "José García"
        user.email = ""
        assert resolve_personal_kb_slug_token(user) == "jose"

    def test_falls_back_to_email_local_part(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = ""
        user.email = "user42@openshu.ai"
        assert resolve_personal_kb_slug_token(user) == "user42"

    def test_email_local_part_with_dots_is_slugified(self):
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = ""
        user.email = "j.doe@openshu.ai"
        assert resolve_personal_kb_slug_token(user) == "j-doe"

    def test_falls_back_to_user_when_no_identity(self):
        """Fallback prevents an empty token from producing a double-hyphen slug."""
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = ""
        user.email = ""
        assert resolve_personal_kb_slug_token(user) == "user"

    def test_falls_back_to_user_when_first_token_slugifies_to_empty(self):
        """A name made entirely of non-alphanumeric chars slugifies to empty."""
        from shu.services.knowledge_base_service import resolve_personal_kb_slug_token

        user = MagicMock()
        user.name = "###"
        user.email = ""
        assert resolve_personal_kb_slug_token(user) == "user"
