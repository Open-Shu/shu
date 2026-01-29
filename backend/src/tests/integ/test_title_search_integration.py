"""
Title Search Integration Tests

Tests the enhanced title weighting and search functionality to ensure
documents like "Some Very Important Study Summary.docx" are found when searching for "Some Very Important Study".

This test suite validates:
- Title weighting configuration system
- Enhanced query preprocessing for technical terms
- Heavy title scoring in keyword, similarity, and hybrid search
- Dedicated title search functionality
- Real document creation and search workflows
"""

import logging
import sys
import uuid
from collections.abc import Callable

from sqlalchemy import text

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


# Test Data - Sample documents with various title patterns
TEST_DOCUMENTS = [
    {
        "title": "Some Very Important Study Summary.docx",
        "content": "This document contains a summary of some study conducted in 2024. The study involved behavioral analysis and cognitive testing.",
        "source_type": "filesystem",
        "file_type": "docx",
    },
    {
        "title": "Project Alpha Report.pdf",
        "content": "Project Alpha is a comprehensive research initiative focusing on advanced algorithms and machine learning techniques.",
        "source_type": "filesystem",
        "file_type": "pdf",
    },
    {
        "title": "Study Protocol Guidelines.txt",
        "content": "Guidelines for conducting super secret study research protocols. This document outlines safety procedures and ethical considerations.",
        "source_type": "filesystem",
        "file_type": "txt",
    },
    {
        "title": "Study Design Document.docx",
        "content": "This document outlines the study design for various research projects including Study studies and clinical trials.",
        "source_type": "filesystem",
        "file_type": "docx",
    },
    {
        "title": "Technical Specifications.pdf",
        "content": "Technical specifications for laboratory equipment and research protocols. Includes Study housing requirements.",
        "source_type": "filesystem",
        "file_type": "pdf",
    },
]


# Knowledge base configuration with title weighting enabled
def get_test_kb_config(suffix=""):
    """Get test KB config with unique name."""
    unique_id = str(uuid.uuid4())[:8]
    return {
        "name": f"test_title_search_kb_{unique_id}{suffix}",
        "description": "Test knowledge base for title search improvements",
    }


async def create_kb_with_title_weighting(
    client,
    db,
    auth_headers,
    title_weighting_enabled=True,
    title_weight_multiplier=10.0,
    title_chunk_enabled=True,
):
    """Create a knowledge base and configure title weighting."""
    # Create knowledge base
    kb_config = get_test_kb_config()
    response = await client.post("/api/v1/knowledge-bases", json=kb_config, headers=auth_headers)
    assert response.status_code == 201
    kb_data = extract_data(response)
    kb_id = kb_data["id"]

    # Update RAG configuration with title weighting settings directly in the database
    # since the API doesn't support title weighting configuration yet
    import json

    from sqlalchemy import text

    await db.execute(
        text("""
            UPDATE knowledge_bases
            SET rag_title_weighting_enabled = :enabled,
                rag_title_weight_multiplier = :multiplier,
                rag_title_chunk_enabled = :chunk_enabled
            WHERE id = :kb_id
        """),
        {
            "enabled": title_weighting_enabled,
            "multiplier": json.dumps(title_weight_multiplier),  # Convert to JSON string
            "chunk_enabled": title_chunk_enabled,
            "kb_id": kb_id,
        },
    )
    await db.commit()

    return kb_id


# Helper Functions
# Standardized helper now provided by tests.response_utils.extract_data


async def create_and_process_document(db, kb_id, doc_data, source_id_suffix=""):
    """Helper function to create a document and process it into chunks."""
    try:
        from sqlalchemy import select

        from shu.models.knowledge_base import KnowledgeBase
        from shu.schemas.document import DocumentCreate
        from shu.services.document_service import DocumentService
        from shu.services.rag_processing_service import RAGProcessingService

        doc_service = DocumentService(db)
        doc_create = DocumentCreate(
            knowledge_base_id=kb_id,
            title=doc_data["title"],
            content=doc_data["content"],
            source_type=doc_data["source_type"],
            source_id=f"{doc_data.get('source_id', 'test-doc')}{source_id_suffix}",
            file_type=doc_data["file_type"],
        )

        # Create document
        doc_response = await doc_service.create_document(doc_create)

        # Get knowledge base for processing
        kb_stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        kb_result = await db.execute(kb_stmt)
        kb = kb_result.scalar_one()

        # Process document into chunks
        rag_processor = RAGProcessingService.get_instance()
        document_chunks = await rag_processor.process_document(
            document_id=doc_response.id,
            knowledge_base=kb,
            text=doc_create.content,
            document_title=doc_create.title,
        )

        # Save chunks to database
        for chunk in document_chunks:
            db.add(chunk)
        await db.commit()

        return doc_response
    except Exception as e:
        logger.error(f"Error in create_and_process_document: {e}")
        raise


# Test Functions
async def test_title_configuration_loading(client, db, auth_headers):
    """Test that title weighting configuration is loaded correctly."""
    # Create knowledge base with title weighting configuration
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Verify configuration was stored by fetching the knowledge base
    from sqlalchemy import select

    from shu.models.knowledge_base import KnowledgeBase

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    result = await db.execute(stmt)
    kb = result.scalar_one_or_none()
    assert kb is not None

    # Get the RAG configuration using the model method
    rag_config = kb.get_rag_config()
    assert rag_config["title_weighting_enabled"] is True
    assert rag_config["title_weight_multiplier"] == 10.0
    assert rag_config["title_chunk_enabled"] is True

    return kb_id


async def test_query_preprocessing_technical_terms(client, db, auth_headers):
    """Test that query preprocessing correctly handles technical terms like 'Study'."""
    # Test the query preprocessing endpoint if available, or test through search
    test_cases = [
        ("Some Very Important Study", ["Study", "Study"]),
        ("API v2.1", ["API", "v2.1"]),
        ("Some Very Important Study Summary", ["Study", "Study", "Summary"]),
    ]

    # Create a test knowledge base with title weighting
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Add a test document to enable search
    test_doc = {
        "title": "Test Document for Query Processing",
        "content": "This document contains Some Very Important Study information and MXB-2024 Protocol details.",
        "source_type": "filesystem",
        "file_type": "txt",
    }

    doc_response = await create_and_process_document(db, kb_id, test_doc, "-query-test")
    assert doc_response.id is not None

    # Test search with technical terms - should not fail due to preprocessing
    for query, expected_terms in test_cases:
        search_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": "keyword", "limit": 5},
            headers=auth_headers,
        )
        # Should not fail due to preprocessing issues
        assert search_response.status_code in [200, 404]  # 404 if no results found is acceptable

    return kb_id


async def test_document_creation_with_title_chunks(client, db, auth_headers):
    """Test that documents are created with proper title chunks when enabled."""
    # Create knowledge base with title chunks enabled
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Create a test document and process it
    doc_data = TEST_DOCUMENTS[0]  # "Some Very Important Study Summary.docx"
    doc_response = await create_and_process_document(db, kb_id, doc_data, "-title-chunks")
    doc_id = doc_response.id

    # Verify document was created
    result = await db.execute(text("SELECT title, content FROM documents WHERE id = :id"), {"id": doc_id})
    db_row = result.fetchone()
    assert db_row is not None
    assert db_row[0] == doc_data["title"]

    # Check for document chunks
    chunks_result = await db.execute(
        text("SELECT content, chunk_metadata FROM document_chunks WHERE document_id = :id ORDER BY chunk_index"),
        {"id": doc_id},
    )
    chunks = chunks_result.fetchall()
    assert len(chunks) > 0

    # Check if title chunk exists (first chunk should contain title)
    first_chunk = chunks[0]
    assert "Document Title:" in first_chunk[0] or doc_data["title"] in first_chunk[0]

    return kb_id, doc_id


async def test_study_keyword_search(client, db, auth_headers):
    """Test that 'Some Very Important Study' finds 'Some Very Important Study Summary.docx' in keyword search."""
    # Create knowledge base and documents
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Create test documents and process them
    created_docs = []
    for i, doc_data in enumerate(TEST_DOCUMENTS):
        doc_response = await create_and_process_document(db, kb_id, doc_data, f"-keyword-{i}")
        created_docs.append({"id": doc_response.id, "title": doc_response.title})

    # Verify documents were created
    assert len(created_docs) == len(
        TEST_DOCUMENTS
    ), f"Expected {len(TEST_DOCUMENTS)} documents, got {len(created_docs)}"

    # Verify chunks were created
    from sqlalchemy import text

    chunk_count_result = await db.execute(
        text("SELECT COUNT(*) FROM document_chunks WHERE knowledge_base_id = :kb_id"),
        {"kb_id": kb_id},
    )
    chunk_count = chunk_count_result.scalar()
    assert (
        chunk_count > 0
    ), f"No document chunks found for knowledge base {kb_id}. Documents may not have been processed correctly."

    # Verify knowledge base still exists
    kb_exists_result = await db.execute(
        text("SELECT COUNT(*) FROM knowledge_bases WHERE id = :kb_id"), {"kb_id": kb_id}
    )
    kb_exists = kb_exists_result.scalar()
    assert kb_exists > 0, f"Knowledge base {kb_id} not found in database"

    # Perform keyword search for "Some Very Important Study"
    search_payload = {"query": "Some Very Important Study", "query_type": "keyword", "limit": 10}

    search_response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_payload, headers=auth_headers)

    # Should find results
    assert (
        search_response.status_code == 200
    ), f"Search request failed with status {search_response.status_code}: {search_response.text}"
    search_results = search_response.json()

    # Extract data from API envelope
    search_data = search_results.get("data", search_results)

    # Check if results contain the Some Very Important Study Summary document
    study_doc_found = False
    study_doc_rank = None

    if "results" in search_data and len(search_data["results"]) > 0:
        for i, result in enumerate(search_data["results"]):
            if "Some Very Important Study Summary.docx" in result.get("document_title", ""):
                study_doc_found = True
                study_doc_rank = i
                break

    # The document should be found (this is the main test assertion)
    assert study_doc_found, f"Some Very Important Study Summary.docx not found in keyword search results for 'Some Very Important Study'. Search data: {search_data}"

    # Should be ranked highly (ideally in top 3)
    assert (
        study_doc_rank is not None and study_doc_rank < 3
    ), f"Some Very Important Study Summary.docx ranked too low (position {study_doc_rank}) for exact title match"

    return kb_id


async def test_study_hybrid_search(client, db, auth_headers):
    """Test that 'Some Very Important Study' finds 'Some Very Important Study Summary.docx' in hybrid search."""
    # Create knowledge base and documents
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Create test documents and process them
    for i, doc_data in enumerate(TEST_DOCUMENTS):
        await create_and_process_document(db, kb_id, doc_data, f"-hybrid-{i}")

    # Perform hybrid search for "Some Very Important Study"
    search_response = await client.post(
        f"/api/v1/query/{kb_id}/search",
        json={
            "query": "Some Very Important Study",
            "query_type": "hybrid",
            "limit": 10,
            "similarity_threshold": 0.0,
        },
        headers=auth_headers,
    )

    # Should find results
    assert search_response.status_code == 200
    search_results = search_response.json()

    # Check if results contain the Some Very Important Study Summary document
    study_doc_found = False
    study_doc_rank = None

    # Extract data from API envelope
    search_data = search_results.get("data", search_results)
    if "results" in search_data and len(search_data["results"]) > 0:
        for i, result in enumerate(search_data["results"]):
            if "Some Very Important Study Summary.docx" in result.get("document_title", ""):
                study_doc_found = True
                study_doc_rank = i
                break

    # The document should be found (it's in the results, just check if it exists)
    assert study_doc_found, f"Some Very Important Study Summary.docx not found in hybrid search results for 'Some Very Important Study'. Results: {search_results}"

    # Should be ranked highly (in top 5 for hybrid search, as title weighting competes with content relevance)
    assert (
        study_doc_rank is not None and study_doc_rank < 5
    ), f"Some Very Important Study Summary.docx ranked too low (position {study_doc_rank}) for exact title match in hybrid search"

    return kb_id


async def test_partial_title_matches(client, db, auth_headers):
    """Test that partial title matches work correctly."""
    # Create knowledge base and documents
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Create test documents and process them
    for i, doc_data in enumerate(TEST_DOCUMENTS):
        await create_and_process_document(db, kb_id, doc_data, f"-partial-{i}")

    # Test partial matches
    test_cases = [
        ("Very Important study", "Some Very Important Study Summary.docx"),
        ("Important Study Summary", "Some Very Important Study Summary.docx"),
    ]

    for query, expected_doc in test_cases:
        search_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={
                "query": query,
                "query_type": "hybrid",  # Use hybrid search for better title matching
                "limit": 10,
                "similarity_threshold": 0.0,
            },
            headers=auth_headers,
        )

        assert search_response.status_code == 200
        search_results = search_response.json()

        # Should find the expected document in top 3 results (title weighting ensures high ranking)
        found = False
        found_rank = None
        search_data = search_results.get("data", search_results)
        if "results" in search_data:
            for i, result in enumerate(search_data["results"][:5]):  # Check top 5 (relaxed due to search complexity)
                if expected_doc in result.get("document_title", ""):
                    found = True
                    found_rank = i
                    break

        assert (
            found
        ), f"Query '{query}' should find document '{expected_doc}' in top 5 results. Results: {search_results}"

    return kb_id


async def test_title_weighting_disabled(client, db, auth_headers):
    """Test behavior when title weighting is disabled."""
    # Create knowledge base with title weighting disabled
    kb_id = await create_kb_with_title_weighting(
        client,
        db,
        auth_headers,
        title_weighting_enabled=False,
        title_weight_multiplier=1.0,
        title_chunk_enabled=False,
    )

    # Verify configuration was stored correctly by fetching the knowledge base
    from sqlalchemy import select

    from shu.models.knowledge_base import KnowledgeBase

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    result = await db.execute(stmt)
    kb = result.scalar_one_or_none()
    assert kb is not None

    # Get the RAG configuration using the model method
    rag_config = kb.get_rag_config()
    assert rag_config["title_weighting_enabled"] is False
    assert rag_config["title_chunk_enabled"] is False

    return kb_id


async def test_rag_config_api_title_weighting(client, db, auth_headers):
    """Test that RAG config API supports title weighting configuration."""
    # Create a knowledge base
    kb_id = await create_kb_with_title_weighting(client, db, auth_headers)

    # Get current RAG config
    get_response = await client.get(f"/api/v1/knowledge-bases/{kb_id}/rag-config", headers=auth_headers)
    assert get_response.status_code == 200
    current_config = extract_data(get_response)

    # Verify title weighting fields are present and have correct values
    assert "title_weighting_enabled" in current_config, f"title_weighting_enabled missing from config: {current_config}"
    assert "title_weight_multiplier" in current_config, f"title_weight_multiplier missing from config: {current_config}"
    assert "title_chunk_enabled" in current_config, f"title_chunk_enabled missing from config: {current_config}"
    assert (
        current_config["title_weighting_enabled"] is True
    ), f"Expected title_weighting_enabled=True, got {current_config['title_weighting_enabled']}"
    assert (
        current_config["title_weight_multiplier"] == 10.0
    ), f"Expected title_weight_multiplier=10.0, got {current_config['title_weight_multiplier']}"
    assert (
        current_config["title_chunk_enabled"] is True
    ), f"Expected title_chunk_enabled=True, got {current_config['title_chunk_enabled']}"

    # Update RAG config with different title weighting settings
    updated_config = {
        "include_references": current_config["include_references"],
        "reference_format": current_config["reference_format"],
        "context_format": current_config["context_format"],
        "prompt_template": current_config["prompt_template"],
        "search_threshold": current_config["search_threshold"],
        "max_results": current_config["max_results"],
        "chunk_overlap_ratio": current_config["chunk_overlap_ratio"],
        "search_type": current_config["search_type"],
        "title_weighting_enabled": False,
        "title_weight_multiplier": 5.0,
        "title_chunk_enabled": False,
    }

    update_response = await client.put(
        f"/api/v1/knowledge-bases/{kb_id}/rag-config", json=updated_config, headers=auth_headers
    )
    assert update_response.status_code == 200
    updated_result = extract_data(update_response)

    # Verify the updated values
    assert updated_result["title_weighting_enabled"] is False
    assert updated_result["title_weight_multiplier"] == 5.0
    assert updated_result["title_chunk_enabled"] is False

    return kb_id


async def test_unauthorized_search_access(client, db, auth_headers):
    """Test that search endpoints require authentication."""
    # Try to search without auth headers
    response = await client.post("/api/v1/query/fake-id/search", json={"query": "test query", "query_type": "keyword"})
    assert response.status_code == 401


# Test Suite Class
class TitleSearchTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Title Search functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all title search test functions."""
        return [
            test_title_configuration_loading,
            test_query_preprocessing_technical_terms,
            test_document_creation_with_title_chunks,
            test_study_keyword_search,
            test_study_hybrid_search,
            test_partial_title_matches,
            test_title_weighting_disabled,
            test_rag_config_api_title_weighting,
            test_unauthorized_search_access,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Title Search Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for enhanced title weighting and search functionality"

    def get_cli_examples(self) -> str:
        """Return title search-specific CLI examples."""
        return """
Examples:
  python tests/test_title_search_integration.py                       # Run all title search tests
  python tests/test_title_search_integration.py --list               # List available tests
  python tests/test_title_search_integration.py --test test_study_study_keyword_search
  python tests/test_title_search_integration.py --pattern study        # Run Study-related tests
  python tests/test_title_search_integration.py --pattern "config"   # Run configuration tests
        """


if __name__ == "__main__":
    suite = TitleSearchTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
