"""
Document Ingestion Pipeline Integration Tests

Tests the queue-based document ingestion pipeline end-to-end.
Verifies that documents progress through PENDING → EXTRACTING → EMBEDDING → READY
and that DocumentChunks are created.
"""

import asyncio
import logging
import sys
import uuid
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite, create_test_runner_script
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)

# Maximum time to wait for pipeline completion (seconds)
PIPELINE_TIMEOUT = 60

# Poll interval for status checks (seconds)
POLL_INTERVAL = 0.5


async def _wait_for_document_status(
    db,
    document_id: str,
    target_statuses: list[str],
    timeout: float = PIPELINE_TIMEOUT,
) -> dict:
    """Wait for a document to reach one of the target statuses.

    Args:
        db: Database session
        document_id: The document ID to check
        target_statuses: List of status values to wait for (e.g., ['ready', 'failed'])
        timeout: Maximum time to wait in seconds

    Returns:
        Dict with document status info

    Raises:
        TimeoutError: If document doesn't reach target status within timeout
    """
    start_time = asyncio.get_event_loop().time()

    while True:
        result = await db.execute(
            text("""
                SELECT status, error_message, processing_status, chunk_count
                FROM documents WHERE id = :doc_id
            """),
            {"doc_id": document_id},
        )
        row = result.fetchone()

        if row is None:
            raise ValueError(f"Document not found: {document_id}")

        status, error_message, processing_status, chunk_count = row

        if status in target_statuses:
            return {
                "status": status,
                "error_message": error_message,
                "processing_status": processing_status,
                "chunk_count": chunk_count,
            }

        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            raise TimeoutError(
                f"Document {document_id} did not reach status {target_statuses} "
                f"within {timeout}s. Current status: {status}"
            )

        await asyncio.sleep(POLL_INTERVAL)


async def test_full_pipeline_success(client, db, auth_headers):
    """Test that document ingestion progresses through pipeline to READY status.

    This test verifies:
    - Document is created with initial status
    - Pipeline processes the document through all stages
    - Final status is READY
    - DocumentChunks are created
    """
    from shu.services.ingestion_service import ingest_document

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Pipeline KB {unique}",
        "description": "Pipeline integration test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")
    assert kb_id, f"KB create did not return id: {kb_resp.text}"

    # Create test file content (simple text file)
    test_content = f"""
    Test Document for Pipeline Integration
    
    This is a test document created for integration testing.
    It contains enough text to generate multiple chunks.
    
    Section 1: Introduction
    This section introduces the test document and its purpose.
    The document is designed to test the full ingestion pipeline.
    
    Section 2: Content
    This section contains the main content of the document.
    It includes multiple paragraphs to ensure chunking works correctly.
    
    Section 3: Conclusion
    This section concludes the test document.
    The pipeline should process this document successfully.
    
    Unique identifier: {unique}
    """
    file_bytes = test_content.encode("utf-8")
    filename = f"test_pipeline_{unique}.txt"
    source_id = f"test-pipeline-{unique}"

    # Ingest the document
    result = await ingest_document(
        db,
        kb_id,
        plugin_name="test_pipeline",
        user_id="test-user",
        file_bytes=file_bytes,
        filename=filename,
        mime_type="text/plain",
        source_id=source_id,
    )

    assert result.get("document_id"), "Ingestion should return document_id"
    assert result.get("status") == "pending", f"Initial status should be pending, got {result.get('status')}"
    assert result.get("skipped") is False, "New document should not be skipped"

    doc_id = result["document_id"]
    logger.info(f"Document created with ID: {doc_id}, waiting for pipeline completion...")

    # Wait for pipeline to complete
    final_status = await _wait_for_document_status(
        db, doc_id, ["ready", "failed"], timeout=PIPELINE_TIMEOUT
    )

    # Verify final status is READY
    assert final_status["status"] == "ready", (
        f"Expected status 'ready', got '{final_status['status']}'. "
        f"Error: {final_status.get('error_message')}"
    )

    # Verify processing_status is synchronized
    assert final_status["processing_status"] == "processed", (
        f"Expected processing_status 'processed', got '{final_status['processing_status']}'"
    )

    # Verify DocumentChunks were created
    assert final_status["chunk_count"] > 0, "Document should have chunks after processing"

    # Double-check chunks exist in database
    chunk_result = await db.execute(
        text("SELECT COUNT(*) FROM document_chunks WHERE document_id = :doc_id"),
        {"doc_id": doc_id},
    )
    chunk_count = chunk_result.scalar()
    assert chunk_count > 0, f"Expected chunks in database, found {chunk_count}"

    logger.info(
        f"Test passed: Document {doc_id} processed successfully with {chunk_count} chunks"
    )


async def test_ocr_failure_sets_failed_status(client, db, auth_headers):
    """Test that missing staged file results in FAILED status with error message.

    This test verifies:
    - When staged file is missing, document status is set to FAILED
    - Error message is populated with staging failure details

    Note: The text extractor is designed to be resilient and will attempt to
    extract text from any file content. To test failure handling, we simulate
    a staging failure by deleting the staged file after document creation but
    before the worker processes it.
    """
    from shu.core.cache_backend import get_cache_backend
    from shu.services.ingestion_service import ingest_document

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test OCR Failure KB {unique}",
        "description": "OCR failure test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")

    logger.info("=== EXPECTED TEST OUTPUT: The following staging failure is expected ===")

    # Create test file content
    test_content = f"Test content for failure test {unique}"
    file_bytes = test_content.encode("utf-8")
    filename = f"test_failure_{unique}.txt"
    source_id = f"test-ocr-failure-{unique}"

    # Ingest the document - this stages the file and enqueues the OCR job
    result = await ingest_document(
        db,
        kb_id,
        plugin_name="test_ocr_failure",
        user_id="test-user",
        file_bytes=file_bytes,
        filename=filename,
        mime_type="text/plain",
        source_id=source_id,
    )

    doc_id = result["document_id"]
    logger.info(f"Document created with ID: {doc_id}")

    # Delete the staged file to simulate staging failure
    # The staging key format is: file_staging:{document_id}
    staging_key = f"file_staging:{doc_id}"
    cache = await get_cache_backend()
    await cache.delete(staging_key)
    logger.info(f"Deleted staged file with key: {staging_key}")

    logger.info(f"Waiting for pipeline to fail due to missing staged file...")

    # Wait for pipeline to complete (expecting failure)
    try:
        final_status = await _wait_for_document_status(
            db, doc_id, ["ready", "failed"], timeout=PIPELINE_TIMEOUT
        )

        # Document should be in FAILED status
        assert final_status["status"] == "failed", (
            f"Expected status 'failed' for missing staged file, got '{final_status['status']}'"
        )

        # Error message should be populated
        assert final_status["error_message"], "Failed document should have error_message"
        # The error could mention "staging" or "not found" or similar
        error_lower = final_status["error_message"].lower()
        assert any(term in error_lower for term in ["staging", "not found", "missing", "retrieve"]), (
            f"Error message should mention staging/retrieval failure: {final_status['error_message']}"
        )

        logger.info(
            f"Test passed: Document {doc_id} correctly failed with error: "
            f"{final_status['error_message'][:100]}..."
        )

    except TimeoutError:
        # If timeout, check current status - it might still be processing
        result = await db.execute(
            text("SELECT status, error_message FROM documents WHERE id = :doc_id"),
            {"doc_id": doc_id},
        )
        row = result.fetchone()
        if row:
            status, error = row
            logger.warning(
                f"Document {doc_id} timed out with status '{status}', error: {error}"
            )
        raise

    logger.info("=== EXPECTED TEST OUTPUT: Staging failure test completed successfully ===")


async def test_text_ingestion_skips_ocr(client, db, auth_headers):
    """Test that ingest_text() skips OCR and goes directly to embedding.

    This test verifies:
    - Initial status is EMBEDDING (not PENDING)
    - Document progresses to READY without OCR stage
    - DocumentChunks are created
    """
    from shu.services.ingestion_service import ingest_text

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Text Ingestion KB {unique}",
        "description": "Text ingestion test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")

    # Create text content
    content = f"""
    Text Ingestion Test Document
    
    This document is ingested directly as text, bypassing OCR.
    It should go directly to the embedding stage.
    
    The pipeline should process this efficiently since no
    text extraction is needed.
    
    Unique identifier: {unique}
    """
    title = f"Test Text Document {unique}"
    source_id = f"test-text-{unique}"

    # Ingest the text
    result = await ingest_text(
        db,
        kb_id,
        plugin_name="test_text_ingestion",
        user_id="test-user",
        title=title,
        content=content,
        source_id=source_id,
    )

    assert result.get("document_id"), "Ingestion should return document_id"
    # Text ingestion should start at EMBEDDING status (skipping OCR)
    assert result.get("status") == "embedding", (
        f"Text ingestion should start at 'embedding' status, got {result.get('status')}"
    )
    assert result.get("skipped") is False, "New document should not be skipped"

    doc_id = result["document_id"]
    logger.info(
        f"Document created with ID: {doc_id}, status: {result.get('status')}, "
        "waiting for embedding completion..."
    )

    # Wait for pipeline to complete
    final_status = await _wait_for_document_status(
        db, doc_id, ["ready", "failed"], timeout=PIPELINE_TIMEOUT
    )

    # Verify final status is READY
    assert final_status["status"] == "ready", (
        f"Expected status 'ready', got '{final_status['status']}'. "
        f"Error: {final_status.get('error_message')}"
    )

    # Verify chunks were created
    assert final_status["chunk_count"] > 0, "Document should have chunks after processing"

    logger.info(
        f"Test passed: Text document {doc_id} processed successfully "
        f"(skipped OCR, {final_status['chunk_count']} chunks)"
    )


async def test_status_polling_via_api(client, db, auth_headers):
    """Test that document status can be polled via API endpoint.

    This test verifies:
    - Document status is visible via GET endpoint
    - Status updates are reflected in API responses
    """
    from shu.services.ingestion_service import ingest_text

    # Create a knowledge base
    unique = str(uuid.uuid4())[:8]
    kb_payload = {
        "name": f"Test Status Polling KB {unique}",
        "description": "Status polling test",
        "sync_enabled": False,
    }
    kb_resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert kb_resp.status_code == 201, kb_resp.text
    kb = kb_resp.json().get("data") or {}
    kb_id = kb.get("id")

    # Create text content
    content = f"Status polling test document. Unique: {unique}"
    title = f"Test Status Polling {unique}"
    source_id = f"test-status-{unique}"

    # Ingest the text
    result = await ingest_text(
        db,
        kb_id,
        plugin_name="test_status_polling",
        user_id="test-user",
        title=title,
        content=content,
        source_id=source_id,
    )

    doc_id = result["document_id"]
    logger.info(f"Document created with ID: {doc_id}, polling status via API...")

    # Poll the document preview endpoint to check status
    start_time = asyncio.get_event_loop().time()
    final_status = None

    while True:
        # Use the document preview endpoint to get status
        preview_resp = await client.get(
            f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/preview",
            headers=auth_headers,
        )

        if preview_resp.status_code == 200:
            preview_data = extract_data(preview_resp)
            processing_info = preview_data.get("processing_info", {})
            status = processing_info.get("status")

            logger.debug(f"Polled status: {status}")

            if status in ["processed", "error"]:
                final_status = status
                break

        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > PIPELINE_TIMEOUT:
            raise TimeoutError(
                f"Document {doc_id} did not complete within {PIPELINE_TIMEOUT}s"
            )

        await asyncio.sleep(POLL_INTERVAL)

    # Verify we got a final status
    assert final_status == "processed", (
        f"Expected processing_status 'processed', got '{final_status}'"
    )

    # Verify document details via API
    preview_resp = await client.get(
        f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/preview",
        headers=auth_headers,
    )
    assert preview_resp.status_code == 200
    preview_data = extract_data(preview_resp)

    # Verify chunk count is visible
    processing_info = preview_data.get("processing_info", {})
    chunk_count = processing_info.get("chunk_count", 0)
    assert chunk_count > 0, f"Expected chunks via API, got {chunk_count}"

    logger.info(
        f"Test passed: Document {doc_id} status polled successfully via API "
        f"(status: {final_status}, chunks: {chunk_count})"
    )


class IngestionPipelineTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for queue-based document ingestion pipeline."""

    def get_test_functions(self) -> list[Callable]:
        """Return all ingestion pipeline test functions."""
        return [
            test_full_pipeline_success,
            test_ocr_failure_sets_failed_status,
            test_text_ingestion_skips_ocr,
            test_status_polling_via_api,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Ingestion Pipeline Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return (
            "End-to-end integration tests for the queue-based document ingestion "
            "pipeline, verifying status transitions and chunk creation."
        )


if __name__ == "__main__":
    create_test_runner_script(IngestionPipelineTestSuite, globals())
