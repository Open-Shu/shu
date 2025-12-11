import sys, os

from integ.base_integration_test import BaseIntegrationTestSuite
from typing import List, Callable

from integ.response_utils import extract_data


async def test_rag_config_full_doc_fields_roundtrip(client, db, auth_headers):
    """Create KB, set full-doc fields, and verify get returns them."""
    # Create KB
    kb_payload = {
        "name": "Test KB FullDoc",
        "description": "Integration Test for full document escalation",
    }
    resp = await client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    kb_id = extract_data(resp)["id"]

    # Update RAG config
    rag_update = {
        "include_references": True,
        "reference_format": "markdown",
        "context_format": "detailed",
        "prompt_template": "custom",
        "search_threshold": 0.5,
        "max_results": 5,
        "chunk_overlap_ratio": 0.1,
        "search_type": "similarity",
        "title_weighting_enabled": True,
        "title_weight_multiplier": 2.0,
        "title_chunk_enabled": True,
        "max_chunks_per_document": 3,
        "minimum_query_words": 1,
        # New fields
        "fetch_full_documents": True,
        "full_doc_max_docs": 1,
        "full_doc_token_cap": 1000,
    }
    resp = await client.put(f"/api/v1/knowledge-bases/{kb_id}/rag-config", json=rag_update, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    # Get RAG config
    resp = await client.get(f"/api/v1/knowledge-bases/{kb_id}/rag-config", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = extract_data(resp)
    assert data["fetch_full_documents"] is True
    assert data["full_doc_max_docs"] == 1
    assert data["full_doc_token_cap"] == 1000


class KBFullDocumentEscalationTestSuite(BaseIntegrationTestSuite):
    def get_test_functions(self) -> List[Callable]:
        return [
            test_rag_config_full_doc_fields_roundtrip,
        ]

    def get_suite_name(self) -> str:
        return "KB Full Document Escalation Integration Tests"

    def get_suite_description(self) -> str:
        return "End-to-end tests for KB RAG full-document escalation behavior and config plumbing."


if __name__ == "__main__":
    KBFullDocumentEscalationTestSuite().run()
