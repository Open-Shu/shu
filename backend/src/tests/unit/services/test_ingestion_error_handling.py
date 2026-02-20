"""
Unit tests for ingestion service error handling fixes.

Covers:
- ERROR-state documents with matching hash are skipped (not reprocessed)
- ingest_email marks document ERROR when process_and_update_chunks fails
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_document(
    *,
    processing_status: str,
    content_hash: str = "abc123",
    source_hash: str | None = None,
    processing_error: str | None = None,
):
    """Build a minimal mock Document with the given status and hash."""
    doc = MagicMock()
    doc.id = "doc-existing"
    doc.processing_status = processing_status
    doc.content_hash = content_hash
    doc.source_hash = source_hash
    doc.word_count = 10
    doc.character_count = 50
    doc.chunk_count = 2
    doc.is_processed = processing_status == "processed"
    doc.has_error = processing_status == "error"
    doc.processing_error = processing_error
    return doc


class TestCheckSkipErrorState:
    """ERROR-state documents with matching hash must be skipped."""

    def test_deterministic_error_matching_hash_returns_skip_result(self):
        """
        _check_skip must return a skip result for an ERROR-state document with a
        deterministic error (extraction failure) when the content hash matches.
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(
            processing_status="error",
            content_hash="abc123",
            processing_error="Text extraction failed after 3 attempts: corrupt PDF",
        )

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="abc123",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is not None, "Expected skip result for deterministic ERROR with matching hash"
        assert result["skipped"] is True
        assert result["skip_reason"] == "hash_match_error_state"

    def test_transient_enqueue_error_allows_retry(self):
        """
        _check_skip must NOT skip an ERROR-state document when the error was a
        transient staging/enqueue failure — next sync should retry.
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(
            processing_status="error",
            content_hash="abc123",
            processing_error="Failed to stage/enqueue: ConnectionError('Redis unavailable')",
        )

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="abc123",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is None, "Transient enqueue failure should allow retry"

    def test_transient_staging_error_allows_retry(self):
        """
        _check_skip must NOT skip when the error was a file staging failure.
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(
            processing_status="error",
            content_hash="abc123",
            processing_error="File staging failed: FileNotFoundError('staging file missing')",
        )

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="abc123",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is None, "Transient staging failure should allow retry"

    def test_error_state_different_hash_does_not_skip(self):
        """
        _check_skip must NOT skip an ERROR-state document when the hash differs
        (content changed — user re-uploaded a fixed version).
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(
            processing_status="error",
            content_hash="old_hash",
            processing_error="Text extraction failed after 3 attempts: corrupt",
        )

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="new_hash",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is None, "Must not skip when hash differs (content changed)"

    def test_error_state_force_reingest_does_not_skip(self):
        """
        _check_skip must NOT skip when force_reingest=True, even for ERROR-state
        documents with matching hash.
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(processing_status="error", content_hash="abc123")

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="abc123",
            force_reingest=True,
            ko_id="ko-test",
        )

        assert result is None, "Must not skip when force_reingest=True"

    def test_processed_state_matching_hash_still_skips(self):
        """
        _check_skip must still skip PROCESSED documents with matching hash
        (existing behavior must not be broken).
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(processing_status="processed", content_hash="abc123")

        result = _check_skip(
            existing=existing,
            source_hash=None,
            content_hash="abc123",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is not None
        assert result["skipped"] is True
        assert result["skip_reason"] == "hash_match"

    def test_error_state_source_hash_match_skips(self):
        """
        _check_skip must skip ERROR-state documents when source_hash matches
        (provider-supplied hash takes priority over content_hash).
        """
        from shu.services.ingestion_service import _check_skip

        existing = _make_mock_document(
            processing_status="error",
            content_hash="content_abc",
            source_hash="provider_hash_xyz",
        )

        result = _check_skip(
            existing=existing,
            source_hash="provider_hash_xyz",
            content_hash="content_abc",
            force_reingest=False,
            ko_id="ko-test",
        )

        assert result is not None
        assert result["skipped"] is True
        assert result["skip_reason"] == "hash_match_error_state"


class TestIngestEmailErrorHandling:
    """ingest_email must mark document ERROR when chunk processing fails."""

    @pytest.mark.asyncio
    async def test_chunk_processing_failure_marks_document_error(self):
        """
        When process_and_update_chunks raises in ingest_email, the document
        must be marked ERROR and the exception re-raised to the caller.
        """
        from shu.services.ingestion_service import ingest_email

        mock_document = MagicMock()
        mock_document.id = "doc-email-123"
        mock_document.mark_error = MagicMock()

        mock_upsert_result = MagicMock()
        mock_upsert_result.document = mock_document
        mock_upsert_result.extraction = {}
        mock_upsert_result.skipped = False
        mock_upsert_result.skip_reason = None

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(
            side_effect=RuntimeError("Embedding model unavailable")
        )

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with (
            patch("shu.services.ingestion_service.DocumentService", return_value=mock_doc_service),
            patch(
                "shu.services.ingestion_service._upsert_document_record",
                AsyncMock(return_value=mock_upsert_result),
            ),
        ):
            with pytest.raises(RuntimeError, match="Embedding model unavailable"):
                await ingest_email(
                    mock_db,
                    "kb-123",
                    plugin_name="gmail",
                    user_id="user-123",
                    subject="Test Email",
                    sender="sender@example.com",
                    recipients={"to": ["recipient@example.com"]},
                    date=None,
                    message_id="msg-123",
                    thread_id=None,
                    body_text="Email body content",
                )

        mock_document.mark_error.assert_called_once()
        error_msg = mock_document.mark_error.call_args[0][0]
        assert "chunk" in error_msg.lower() or "processing" in error_msg.lower() or "failed" in error_msg.lower()
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_chunk_processing_failure_commits_error_status(self):
        """
        When process_and_update_chunks fails, the ERROR status must be committed
        to the DB so the document doesn't remain in an ambiguous state.
        """
        from shu.services.ingestion_service import ingest_email

        mock_document = MagicMock()
        mock_document.id = "doc-email-456"
        mock_document.mark_error = MagicMock()

        mock_upsert_result = MagicMock()
        mock_upsert_result.document = mock_document
        mock_upsert_result.extraction = {}
        mock_upsert_result.skipped = False

        mock_doc_service = MagicMock()
        mock_doc_service.process_and_update_chunks = AsyncMock(
            side_effect=Exception("DB connection lost")
        )

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with (
            patch("shu.services.ingestion_service.DocumentService", return_value=mock_doc_service),
            patch(
                "shu.services.ingestion_service._upsert_document_record",
                AsyncMock(return_value=mock_upsert_result),
            ),
        ):
            with pytest.raises(Exception):
                await ingest_email(
                    mock_db,
                    "kb-123",
                    plugin_name="gmail",
                    user_id="user-123",
                    subject="Test Email",
                    sender=None,
                    recipients={},
                    date=None,
                    message_id="msg-456",
                    thread_id=None,
                    body_text="Email body",
                )

        # mark_error must have been called and committed
        mock_document.mark_error.assert_called_once()
        mock_db.add.assert_called_with(mock_document)
        mock_db.commit.assert_called()
