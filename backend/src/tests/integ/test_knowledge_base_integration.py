"""
Knowledge Base Integration Tests for Shu

These tests cover knowledge base CRUD operations, document management,
and the complete knowledge base lifecycle.
"""

import sys
import uuid
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.expected_error_context import (
    expect_duplicate_errors,
)
from integ.helpers.auth import create_active_user_headers, create_active_user_with_id
from integ.response_utils import extract_data
from shu.core.logging import get_logger

logger = get_logger(__name__)

# SHU-817: personal-KB document management (delete authz, re-ingest, GET /personal)
# exercised through the real API.
_TXT = ("kbnotes.txt", b"Personal Knowledge management test content.\n", "text/plain")


async def _kb_create(client, headers, name):
    resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": name, "description": "SHU-817 test KB", "sync_enabled": False},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return extract_data(resp)["id"]


async def _kb_upload(client, headers, kb_id, file_tuple=_TXT):
    return await client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/upload",
        files={"files": file_tuple},
        headers=headers,
    )


async def _kb_doc_total(client, headers, kb_id):
    resp = await client.get(f"/api/v1/knowledge-bases/{kb_id}/documents", headers=headers)
    assert resp.status_code == 200, resp.text
    return extract_data(resp).get("total", 0)


async def test_health_endpoint(client, db, auth_headers):
    """Test that the health endpoint is accessible."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["status"] in ["healthy", "warning"]


async def test_list_knowledge_bases_structure(client, db, auth_headers):
    """Test that the knowledge base list API returns the correct structure."""
    response = await client.get("/api/v1/knowledge-bases", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]

    # Verify response structure
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "size" in data
    assert "pages" in data

    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)
    assert data["total"] >= 0  # Could be 0 or more
    assert data["page"] >= 1
    assert data["size"] > 0


async def test_create_knowledge_base_success(client, db, auth_headers):
    """Test successful knowledge base creation."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Test Knowledge Base {unique_id}",
        "description": "A test knowledge base for integration testing",
        "sync_enabled": True,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "chunk_size": 1000,
        "chunk_overlap": 200,
    }

    response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert response.status_code == 201

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["name"] == kb_data["name"]
    assert data["description"] == kb_data["description"]
    assert data["sync_enabled"] == kb_data["sync_enabled"]
    assert data["embedding_model"] == kb_data["embedding_model"]
    assert data["chunk_size"] == kb_data["chunk_size"]
    assert data["chunk_overlap"] == kb_data["chunk_overlap"]
    assert "id" in data
    assert "created_at" in data
    assert "status" in data

    # Verify in database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"), {"id": data["id"]})
    kb_record = result.fetchone()
    assert kb_record is not None
    assert kb_record.name == kb_data["name"]


async def test_get_knowledge_base_by_id(client, db, auth_headers):
    """Test retrieving a knowledge base by ID."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # First create a knowledge base
    kb_data = {
        "name": f"Test KB for Retrieval {unique_id}",
        "description": "Testing retrieval functionality",
        "sync_enabled": True,
    }

    create_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Now retrieve it
    response = await client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["id"] == kb_id
    assert data["name"] == kb_data["name"]
    assert data["description"] == kb_data["description"]
    assert data["sync_enabled"] == kb_data["sync_enabled"]


async def test_update_knowledge_base(client, db, auth_headers):
    """Test updating a knowledge base."""
    # Create a knowledge base
    kb_data = {
        "name": "Original Test KB Name",
        "description": "Original description",
        "sync_enabled": True,
        "chunk_size": 1000,
    }

    create_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Update it
    update_data = {
        "name": "Updated Test KB Name",
        "description": "Updated description",
        "sync_enabled": False,
        "chunk_size": 1500,
    }

    response = await client.put(f"/api/v1/knowledge-bases/{kb_id}", json=update_data, headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["name"] == update_data["name"]
    assert data["description"] == update_data["description"]
    assert data["sync_enabled"] == update_data["sync_enabled"]
    assert data["chunk_size"] == update_data["chunk_size"]

    # Verify in database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"), {"id": kb_id})
    kb_record = result.fetchone()
    assert kb_record.name == update_data["name"]


async def test_delete_knowledge_base(client, db, auth_headers):
    """Test deleting a knowledge base."""
    # Create a knowledge base
    kb_data = {"name": "KB to Delete", "description": "This will be deleted", "sync_enabled": True}

    create_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    # Delete it
    response = await client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert response.status_code == 204

    # Verify it's gone from database
    result = await db.execute(text("SELECT * FROM knowledge_bases WHERE id = :id"), {"id": kb_id})
    kb_record = result.fetchone()
    assert kb_record is None

    # Verify 404 on subsequent GET
    get_response = await client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert get_response.status_code == 404


async def test_create_knowledge_base_duplicate_name(client, db, auth_headers):
    """Test that duplicate knowledge base names are handled appropriately."""
    logger.info("=== EXPECTED TEST OUTPUT: Testing duplicate knowledge base name handling ===")

    import uuid

    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Duplicate Name Test {unique_id}",
        "description": "First KB with this name",
        "sync_enabled": True,
    }

    # Create first KB
    response1 = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert response1.status_code == 201

    with expect_duplicate_errors():
        # Try to create second KB with same name
        kb_data2 = {
            "name": f"Duplicate Name Test {unique_id}",
            "description": "Second KB with same name",
            "sync_enabled": False,
        }

        response2 = await client.post("/api/v1/knowledge-bases", json=kb_data2, headers=auth_headers)
        # Should fail since duplicate names aren't allowed
        assert response2.status_code in [400, 500]  # Accept either 400 or 500 for duplicate names

    logger.info("=== EXPECTED TEST OUTPUT: Duplicate name test completed successfully ===")


async def test_create_knowledge_base_invalid_data(client, db, auth_headers):
    """Test knowledge base creation with invalid data."""
    logger.info("=== EXPECTED TEST OUTPUT: The following validation errors (400/422) are expected ===")

    invalid_data_sets = [
        {},  # Empty data
        {"name": ""},  # Empty name
        {"name": "Valid Name", "chunk_size": -1},  # Invalid chunk size
        {"name": "Valid Name", "chunk_overlap": 2000, "chunk_size": 1000},  # Overlap >= chunk_size
    ]

    for i, invalid_data in enumerate(invalid_data_sets):
        response = await client.post("/api/v1/knowledge-bases", json=invalid_data, headers=auth_headers)
        assert response.status_code in [
            400,
            422,
        ], f"Invalid data should be rejected: {invalid_data}"
        logger.info(f"=== EXPECTED TEST OUTPUT: Validation error {i+1}/4 for invalid data occurred as expected ===")


async def test_unauthorized_access(client, db, auth_headers):
    """Test that knowledge base endpoints require authentication."""
    logger.info("=== EXPECTED TEST OUTPUT: The following 401 authentication errors are expected ===")

    # Test without auth headers
    response = await client.get("/api/v1/knowledge-bases")
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated GET occurred as expected ===")

    response = await client.post("/api/v1/knowledge-bases", json={"name": "Test", "access_level": "RESEARCH"})
    assert response.status_code == 401
    logger.info("=== EXPECTED TEST OUTPUT: 401 error for unauthenticated POST occurred as expected ===")


async def test_knowledge_base_embedding_models(client, db, auth_headers):
    """Test different embedding models for knowledge bases."""
    embedding_models = [
        "sentence-transformers/all-MiniLM-L6-v2",
        "sentence-transformers/all-mpnet-base-v2",
    ]

    for model in embedding_models:
        kb_data = {
            "name": f"Test KB {model.split('/')[-1]}",
            "description": f"Testing {model} embedding model",
            "embedding_model": model,
            "sync_enabled": True,
        }

        response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
        assert response.status_code == 201

        response_data = response.json()
        data = response_data["data"]
        assert data["embedding_model"] == model


async def test_personal_kb_owner_regular_user_can_delete_own_document(client, db, auth_headers):
    """SHU-817 S1: a regular-user KB owner can delete their own document (previously 403)."""
    reg = await create_active_user_headers(client, auth_headers, role="regular_user")
    ensure = await client.post("/api/v1/knowledge-bases/personal", headers=reg)
    assert ensure.status_code in (200, 201), ensure.text
    kb_id = extract_data(ensure)["id"]

    up = await _kb_upload(client, reg, kb_id)
    assert up.status_code == 200, up.text
    doc_id = extract_data(up)["results"][0]["document_id"]

    d = await client.delete(f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}", headers=reg)
    assert d.status_code == 204, f"owner delete should succeed without 403: {d.status_code} {d.text}"
    assert await _kb_doc_total(client, reg, kb_id) == 0
    logger.info("Test passed: regular-user owner deleted their own personal-KB doc (no 403)")


async def test_kb_delete_denied_for_power_user_without_grant(client, db, auth_headers):
    """SHU-817 S1: power_user without kb.delete is denied (404) on a KB they don't own."""
    logger.info(
        "=== EXPECTED TEST OUTPUT: a 404 is expected when a power_user without kb.delete "
        "deletes another user's document ==="
    )
    kb_id = await _kb_create(client, auth_headers, f"Test KBDelete Authz KB {uuid.uuid4().hex[:8]}")
    up = await _kb_upload(client, auth_headers, kb_id)
    assert up.status_code == 200, up.text
    doc_id = extract_data(up)["results"][0]["document_id"]

    pu = await create_active_user_headers(client, auth_headers, role="power_user")
    d = await client.delete(f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}", headers=pu)
    assert d.status_code == 404, f"power_user without kb.delete must be denied (404): {d.status_code} {d.text}"
    assert await _kb_doc_total(client, auth_headers, kb_id) == 1, "document must survive the denied delete"
    logger.info("=== EXPECTED TEST OUTPUT: power_user delete correctly denied with 404 ===")


async def test_personal_kb_delete_denied_for_owner(client, db, auth_headers):
    """SHU-817: a personal KB is an owner-scoped singleton; its owner (a non-admin) cannot
    delete it (403). Only admins can — see test_personal_kb_deletable_by_admin."""
    logger.info("=== EXPECTED TEST OUTPUT: a 403 PERSONAL_KB_DELETE_NOT_ALLOWED is expected ===")
    reg = await create_active_user_headers(client, auth_headers, role="regular_user")
    ensure = await client.post("/api/v1/knowledge-bases/personal", headers=reg)
    kb_id = extract_data(ensure)["id"]

    d = await client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=reg)
    assert d.status_code == 403, f"owner delete of personal KB should be 403: {d.status_code} {d.text}"
    assert d.json().get("error", {}).get("code") == "PERSONAL_KB_DELETE_NOT_ALLOWED", d.text
    logger.info("=== EXPECTED TEST OUTPUT: owner delete of personal KB correctly rejected with 403 ===")


async def test_personal_kb_deletable_by_admin(client, db, auth_headers):
    """SHU-817: an admin CAN delete a personal KB (offboarding / cleanup). Without this
    path the KB is un-deletable and orphans on owner deletion."""
    reg = await create_active_user_headers(client, auth_headers, role="regular_user")
    ensure = await client.post("/api/v1/knowledge-bases/personal", headers=reg)
    assert ensure.status_code in (200, 201), ensure.text
    kb_id = extract_data(ensure)["id"]

    # Admin (auth_headers) deletes another user's personal KB.
    d = await client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers)
    assert d.status_code == 204, f"admin should be able to delete a personal KB (204): {d.status_code} {d.text}"

    # It's gone — the owner's GET /personal returns null again.
    g = await client.get("/api/v1/knowledge-bases/personal", headers=reg)
    assert g.status_code == 200 and g.json().get("data") is None, f"personal KB should be gone: {g.text}"
    logger.info("Test passed: admin deleted a user's personal KB")


async def test_deleting_user_cascades_personal_kb(client, db, auth_headers):
    """SHU-817: deleting a user removes their personal KB instead of orphaning it.

    owner_id is ON DELETE SET NULL (org KBs intentionally outlive their owner), so without
    the explicit cascade in user_service.delete_user the personal KB would be left behind
    with a NULL owner and could only be removed by an admin.
    """
    headers, user_id = await create_active_user_with_id(client, auth_headers, role="regular_user")
    ensure = await client.post("/api/v1/knowledge-bases/personal", headers=headers)
    assert ensure.status_code in (200, 201), ensure.text
    kb_id = extract_data(ensure)["id"]

    d = await client.delete(f"/api/v1/auth/users/{user_id}", headers=auth_headers)
    assert d.status_code in (200, 204), f"admin user delete should succeed: {d.status_code} {d.text}"

    # Fresh snapshot, then confirm the personal KB was deleted with its owner, not orphaned.
    await db.rollback()
    row = await db.execute(text("SELECT id FROM knowledge_bases WHERE id = :id"), {"id": kb_id})
    assert row.scalar_one_or_none() is None, "personal KB must be deleted with its owner, not orphaned"
    logger.info("Test passed: deleting a user cascades their personal KB")


async def test_reingest_missing_document_returns_404(client, db, auth_headers):
    """SHU-817 R3: re-ingesting a non-existent document is a clean 404."""
    logger.info("=== EXPECTED TEST OUTPUT: a 404 is expected for re-ingesting a missing document ===")
    kb_id = await _kb_create(client, auth_headers, f"Test Reingest 404 KB {uuid.uuid4().hex[:8]}")
    r = await client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/{uuid.uuid4().hex}/reingest", headers=auth_headers
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    logger.info("=== EXPECTED TEST OUTPUT: reingest of missing document correctly returned 404 ===")


async def test_reingest_busy_document_returns_409(client, db, auth_headers):
    """SHU-817 R3 / Scenario A: a still-processing document cannot be re-ingested (409)."""
    logger.info("=== EXPECTED TEST OUTPUT: a 409 DOCUMENT_BUSY is expected for a processing document ===")
    kb_id = await _kb_create(client, auth_headers, f"Test Reingest Busy KB {uuid.uuid4().hex[:8]}")
    up = await _kb_upload(client, auth_headers, kb_id)
    doc_id = extract_data(up)["results"][0]["document_id"]

    # Force a clearly non-terminal state so the result is independent of any worker.
    await db.execute(text("UPDATE documents SET processing_status='extracting' WHERE id=:id"), {"id": doc_id})
    await db.commit()

    r = await client.post(f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/reingest", headers=auth_headers)
    assert r.status_code == 409, f"expected 409 busy, got {r.status_code}: {r.text}"
    assert r.json().get("error", {}).get("code") == "DOCUMENT_BUSY", r.text
    logger.info("=== EXPECTED TEST OUTPUT: reingest of busy document correctly returned 409 ===")


async def test_get_personal_knowledge_base_returns_null_then_kb(client, db, auth_headers):
    """SHU-817 R5: GET /personal returns null before provisioning, then the ensured KB."""
    reg = await create_active_user_headers(client, auth_headers, role="regular_user")

    g0 = await client.get("/api/v1/knowledge-bases/personal", headers=reg)
    assert g0.status_code == 200, g0.text
    assert g0.json().get("data") is None, f"fresh user should have no personal KB yet: {g0.text}"

    ensure = await client.post("/api/v1/knowledge-bases/personal", headers=reg)
    assert ensure.status_code in (200, 201), ensure.text
    kb_id = extract_data(ensure)["id"]

    g1 = await client.get("/api/v1/knowledge-bases/personal", headers=reg)
    assert g1.status_code == 200, g1.text
    data = extract_data(g1)
    assert data and data["id"] == kb_id, f"GET /personal should return the ensured KB: {data}"
    assert data["is_personal"] is True, data
    logger.info("Test passed: GET /personal returns null then the ensured personal KB")


async def test_document_preview_exposes_synopsis_and_type(client, db, auth_headers):
    """SHU-817 F2: GET /preview exposes synopsis + document_type for the preview slide-over."""
    kb_id = await _kb_create(client, auth_headers, f"Test Preview KB {uuid.uuid4().hex[:8]}")
    up = await _kb_upload(client, auth_headers, kb_id)
    assert up.status_code == 200, up.text
    doc_id = extract_data(up)["results"][0]["document_id"]

    r = await client.get(
        f"/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}/preview", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    data = extract_data(r)
    # Keys are present even before profiling completes (values may be null).
    assert "synopsis" in data, data
    assert "document_type" in data, data
    assert "preview" in data and "processing_info" in data, data
    logger.info("Test passed: /preview exposes synopsis + document_type")


class KnowledgeBaseIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for knowledge base functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all knowledge base integration test functions."""
        return [
            test_health_endpoint,
            test_list_knowledge_bases_structure,
            test_create_knowledge_base_success,
            test_get_knowledge_base_by_id,
            test_update_knowledge_base,
            test_delete_knowledge_base,
            test_create_knowledge_base_duplicate_name,
            test_create_knowledge_base_invalid_data,
            test_unauthorized_access,
            test_knowledge_base_embedding_models,
            test_personal_kb_owner_regular_user_can_delete_own_document,
            test_kb_delete_denied_for_power_user_without_grant,
            test_personal_kb_delete_denied_for_owner,
            test_personal_kb_deletable_by_admin,
            test_deleting_user_cascades_personal_kb,
            test_reingest_missing_document_returns_404,
            test_reingest_busy_document_returns_409,
            test_get_personal_knowledge_base_returns_null_then_kb,
            test_document_preview_exposes_synopsis_and_type,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Knowledge Base Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for knowledge base CRUD operations and access control"

    def get_cli_examples(self) -> str:
        """Return knowledge base-specific CLI examples."""
        return """
Examples:
  python tests/test_knowledge_base_integration.py                    # Run all KB tests
  python tests/test_knowledge_base_integration.py --list            # List available tests
  python tests/test_knowledge_base_integration.py --test test_create_knowledge_base_success
  python tests/test_knowledge_base_integration.py --pattern "create" # Run creation tests
  python tests/test_knowledge_base_integration.py --pattern "access" # Run access tests
        """


if __name__ == "__main__":
    suite = KnowledgeBaseIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
