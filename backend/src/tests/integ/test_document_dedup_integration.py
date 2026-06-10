"""
Document Deduplication Integration Tests

Tests hash-based document deduplication in the ingestion service.
Verifies that unchanged documents are skipped and force_reingest works.
"""

import uuid

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data
from shu.core.logging import get_logger

logger = get_logger(__name__)

# SHU-817: manual-upload dedup-on-update exercised through the real upload API.
_TXT = ("kbnotes.txt", b"Personal Knowledge dedup test content.\n", "text/plain")


async def _create_kb_for_dedup(client, headers, name):
    resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": name, "description": "SHU-817 dedup test KB", "sync_enabled": False},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return extract_data(resp)["id"]


async def _upload_file(client, headers, kb_id, file_tuple=_TXT):
    return await client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/upload",
        files={"files": file_tuple},
        headers=headers,
    )


async def _kb_doc_total(client, headers, kb_id):
    resp = await client.get(f"/api/v1/knowledge-bases/{kb_id}/documents", headers=headers)
    assert resp.status_code == 200, resp.text
    return extract_data(resp).get("total", 0)


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


async def test_manual_reupload_same_file_does_not_duplicate(client, db, auth_headers):
    """Re-uploading the same filename updates/skips in place — never duplicates (SHU-817)."""
    kb_id = await _create_kb_for_dedup(client, auth_headers, f"Test Dedup Reupload KB {uuid.uuid4().hex[:8]}")

    r1 = await _upload_file(client, auth_headers, kb_id)
    assert r1.status_code == 200, r1.text
    res1 = extract_data(r1)["results"][0]
    assert res1["success"] is True, res1
    assert res1.get("action") == "added", f"first upload should be 'added': {res1}"
    doc_id = res1["document_id"]

    r2 = await _upload_file(client, auth_headers, kb_id)
    assert r2.status_code == 200, r2.text
    res2 = extract_data(r2)["results"][0]
    assert res2.get("action") != "added", f"re-upload must not create a new doc: {res2}"
    assert res2.get("document_id") == doc_id, f"re-upload should resolve to the same doc: {res2}"
    assert await _kb_doc_total(client, auth_headers, kb_id) == 1, "re-upload must not duplicate"
    logger.info("Test passed: manual re-upload of the same file did not duplicate (doc_id=%s)", doc_id)


async def test_manual_upload_distinct_filenames_are_distinct_docs(client, db, auth_headers):
    """Distinct filenames are distinct documents."""
    kb_id = await _create_kb_for_dedup(client, auth_headers, f"Test Dedup Distinct KB {uuid.uuid4().hex[:8]}")
    a = await _upload_file(client, auth_headers, kb_id, ("alpha.txt", b"alpha contents", "text/plain"))
    b = await _upload_file(client, auth_headers, kb_id, ("beta.txt", b"beta contents", "text/plain"))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert await _kb_doc_total(client, auth_headers, kb_id) == 2
    logger.info("Test passed: distinct filenames created distinct documents")


async def test_manual_upload_intra_batch_duplicate_flagged(client, db, auth_headers):
    """Two same-named files in one upload: first kept, the rest flagged (SHU-817 M1)."""
    kb_id = await _create_kb_for_dedup(client, auth_headers, f"Test Dedup Batch KB {uuid.uuid4().hex[:8]}")
    files = [
        ("files", ("dup.txt", b"first copy", "text/plain")),
        ("files", ("dup.txt", b"second copy", "text/plain")),
    ]
    r = await client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/upload", files=files, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    actions = sorted(x.get("action") for x in extract_data(r)["results"])
    assert actions == ["added", "duplicate_in_batch"], f"expected one added + one duplicate flag: {actions}"
    assert await _kb_doc_total(client, auth_headers, kb_id) == 1, "intra-batch duplicate must not create a second doc"
    logger.info("Test passed: intra-batch duplicate filename flagged, single doc created")


class DocumentDedupTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self):
        return [
            test_ingest_text_skips_unchanged_document,
            test_ingest_text_processes_changed_document,
            test_ingest_text_force_reingest_bypasses_hash_check,
            test_manual_reupload_same_file_does_not_duplicate,
            test_manual_upload_distinct_filenames_are_distinct_docs,
            test_manual_upload_intra_batch_duplicate_flagged,
        ]

    def get_suite_name(self) -> str:
        return "Document Deduplication"

    def get_suite_description(self) -> str:
        return "Integration tests for hash-based document deduplication in ingestion"


if __name__ == "__main__":
    create_test_runner_script(DocumentDedupTestSuite, globals())
