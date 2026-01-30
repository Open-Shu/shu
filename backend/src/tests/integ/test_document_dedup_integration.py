"""
Document Deduplication Integration Tests

Tests hash-based document deduplication in the ingestion service.
Verifies that unchanged documents are skipped and force_reingest works.
"""

import logging
import uuid

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script

logger = logging.getLogger(__name__)


async def test_ingest_text_skips_unchanged_document(client, db, auth_headers):
    """Test that ingesting the same text document twice skips the second ingestion."""
    from shu.services.ingestion_service import ingest_text

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Dedup KB {unique}",
        "description": "Dedup test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")
    assert kb_id, f"KB create did not return id: {kb_resp.text}"

    # First ingestion - should create document
    source_id = f"test-doc-{unique}"
    title = f"Test Document {unique}"
    content = f"This is test content for deduplication testing. Unique: {unique}"

    result1 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title=title,
        content=content,
        source_id=source_id,
    )

    assert result1.get("document_id"), "First ingestion should return document_id"
    assert result1.get("skipped") is False, "First ingestion should not be skipped"
    assert result1.get("word_count", 0) > 0, "First ingestion should have word_count"
    assert result1.get("chunk_count", 0) > 0, "First ingestion should have chunk_count"

    doc_id = result1["document_id"]

    # Second ingestion with same content - should be skipped
    result2 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title=title,
        content=content,
        source_id=source_id,
    )

    assert result2.get("document_id") == doc_id, "Second ingestion should return same document_id"
    assert result2.get("skipped") is True, "Second ingestion should be skipped"
    assert result2.get("skip_reason") == "hash_match", "Skip reason should be hash_match"
    # Stats should be preserved from first ingestion
    assert result2.get("word_count", 0) > 0, "Skipped ingestion should return existing word_count"
    assert result2.get("chunk_count", 0) > 0, "Skipped ingestion should return existing chunk_count"

    logger.info(f"Test passed: unchanged document was skipped (doc_id={doc_id})")


async def test_ingest_text_processes_changed_document(client, db, auth_headers):
    """Test that ingesting a document with changed content reprocesses it."""
    from shu.services.ingestion_service import ingest_text

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Dedup Changed KB {unique}",
        "description": "Dedup changed test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")

    source_id = f"test-doc-changed-{unique}"

    # First ingestion
    result1 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title="Original Title",
        content="Original content for testing.",
        source_id=source_id,
    )

    assert result1.get("skipped") is False, "First ingestion should not be skipped"
    doc_id = result1["document_id"]

    # Second ingestion with CHANGED content - should NOT be skipped
    result2 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title="Updated Title",
        content="Updated content that is different from the original.",
        source_id=source_id,
    )

    assert result2.get("document_id") == doc_id, "Should update same document"
    assert result2.get("skipped") is False, "Changed document should NOT be skipped"

    logger.info(f"Test passed: changed document was reprocessed (doc_id={doc_id})")


async def test_ingest_text_force_reingest_bypasses_hash_check(client, db, auth_headers):
    """Test that force_reingest=True bypasses hash check and reprocesses."""
    from shu.services.ingestion_service import ingest_text

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Force Reingest KB {unique}",
        "description": "Force reingest test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")

    source_id = f"test-doc-force-{unique}"
    content = f"Content for force reingest test. Unique: {unique}"

    # First ingestion
    result1 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title="Test Doc",
        content=content,
        source_id=source_id,
    )

    assert result1.get("skipped") is False
    doc_id = result1["document_id"]

    # Second ingestion with same content but force_reingest=True
    result2 = await ingest_text(
        db,
        kb_id,
        plugin_name="test_dedup",
        user_id="test-user",
        title="Test Doc",
        content=content,
        source_id=source_id,
        attributes={"force_reingest": True},
    )

    assert result2.get("document_id") == doc_id, "Should update same document"
    assert result2.get("skipped") is False, "force_reingest should bypass hash check"

    logger.info(f"Test passed: force_reingest bypassed hash check (doc_id={doc_id})")


class DocumentDedupTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_ingest_text_skips_unchanged_document,
            test_ingest_text_processes_changed_document,
            test_ingest_text_force_reingest_bypasses_hash_check,
        ]

    def get_suite_name(self) -> str:
        return "Document Deduplication"

    def get_suite_description(self) -> str:
        return "Integration tests for hash-based document deduplication in ingestion"


if __name__ == "__main__":
    create_test_runner_script(DocumentDedupTestSuite, globals())
