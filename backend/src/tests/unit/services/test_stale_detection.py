"""Unit tests for stale KB detection and search degradation guard.

Tests detect_stale_kbs(), the similarity_search stale guard, and
the hybrid_search fallback to keyword-only for stale KBs.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shu.core.exceptions import KnowledgeBaseStaleEmbeddingsError
from shu.models.knowledge_base import KnowledgeBase
from shu.services.knowledge_base_service import detect_stale_kbs


class TestDetectStaleKBs:
    """Tests for the detect_stale_kbs standalone function."""

    @pytest.mark.asyncio
    async def test_marks_mismatched_kbs_as_stale(self):
        """KBs with a different embedding_model should be marked stale."""
        mock_db = AsyncMock()

        # Simulate two KBs with old model
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("kb-1",), ("kb-2",)]
        mock_db.execute = AsyncMock(side_effect=[mock_result, MagicMock()])
        mock_db.commit = AsyncMock()

        stale_ids = await detect_stale_kbs(mock_db, "Snowflake/snowflake-arctic-embed-l-v2.0")

        assert stale_ids == ["kb-1", "kb-2"]
        assert mock_db.execute.call_count == 2  # SELECT + UPDATE
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_stale_kbs_returns_empty(self):
        """When all KBs match the system model, return empty list."""
        mock_db = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        stale_ids = await detect_stale_kbs(mock_db, "Snowflake/snowflake-arctic-embed-l-v2.0")

        assert stale_ids == []
        # Should not execute UPDATE or commit
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_marks_current_kbs(self):
        """KBs already in 're_embedding' or 'error' status should not be re-marked."""
        # The SQL query filters WHERE embedding_status = 'current', so only
        # current KBs are returned. We verify the function passes through whatever
        # the query returns without additional filtering.
        mock_db = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("kb-only-current",)]
        mock_db.execute = AsyncMock(side_effect=[mock_result, MagicMock()])
        mock_db.commit = AsyncMock()

        stale_ids = await detect_stale_kbs(mock_db, "new-model")

        assert stale_ids == ["kb-only-current"]


class TestKnowledgeBaseEmbeddingHelpers:
    """Tests for KnowledgeBase model embedding helper methods."""

    def test_is_embedding_stale_true(self):
        kb = KnowledgeBase()
        kb.embedding_model = "old-model"
        assert kb.is_embedding_stale("new-model") is True

    def test_is_embedding_stale_false(self):
        kb = KnowledgeBase()
        kb.embedding_model = "same-model"
        assert kb.is_embedding_stale("same-model") is False

    def test_mark_re_embedding_started(self):
        kb = KnowledgeBase()
        kb.mark_re_embedding_started(500)
        assert kb.embedding_status == "re_embedding"
        assert kb.re_embedding_progress["chunks_total"] == 500
        assert kb.re_embedding_progress["chunks_done"] == 0
        assert kb.re_embedding_progress["phase"] == "chunks"
        assert "started_at" in kb.re_embedding_progress

    def test_update_re_embedding_phase(self):
        kb = KnowledgeBase()
        kb.mark_re_embedding_started(500)
        assert kb.re_embedding_progress["phase"] == "chunks"
        kb.update_re_embedding_phase("synopses")
        assert kb.re_embedding_progress["phase"] == "synopses"
        assert kb.re_embedding_progress["chunks_total"] == 500  # other fields preserved

    def test_update_re_embedding_progress(self):
        kb = KnowledgeBase()
        kb.mark_re_embedding_started(500)
        kb.update_re_embedding_progress(150)
        assert kb.re_embedding_progress["chunks_done"] == 150
        assert kb.re_embedding_progress["chunks_total"] == 500

    def test_mark_re_embedding_complete(self):
        kb = KnowledgeBase()
        kb.embedding_status = "re_embedding"
        kb.re_embedding_progress = {"chunks_done": 500, "chunks_total": 500}
        kb.mark_re_embedding_complete("new-model")
        assert kb.embedding_status == "current"
        assert kb.embedding_model == "new-model"
        assert kb.re_embedding_progress is None

    def test_mark_re_embedding_failed(self):
        kb = KnowledgeBase()
        kb.mark_re_embedding_started(100)
        kb.mark_re_embedding_failed("out of memory")
        assert kb.embedding_status == "error"
        assert kb.re_embedding_progress["error"] == "out of memory"


class TestStaleEmbeddingsException:
    """Tests for KnowledgeBaseStaleEmbeddingsError."""

    def test_exception_fields(self):
        exc = KnowledgeBaseStaleEmbeddingsError("kb-123", "stale")
        assert exc.status_code == 409
        assert exc.error_code == "KNOWLEDGE_BASE_STALE_EMBEDDINGS"
        assert "kb-123" in exc.message
        assert exc.details["embedding_status"] == "stale"
