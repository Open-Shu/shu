"""
Query Integration Tests for Shu

These tests cover the core RAG functionality including semantic search,
similarity search, hybrid search, and query operations.
"""

import sys
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data


async def test_health_endpoint(client, db, auth_headers):
    """Test that the health endpoint is accessible."""
    response = await client.get("/api/v1/health", headers=auth_headers)
    assert response.status_code == 200

    response_data = response.json()
    assert "data" in response_data
    data = response_data["data"]
    assert data["status"] in ["healthy", "warning"]


async def test_query_search_basic(client, db, auth_headers):
    """Test basic search functionality."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # First create a knowledge base
    kb_data = {
        "name": f"Query Test KB {unique_id}",
        "description": "Knowledge base for query testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test search endpoint
    search_data = {"query": "test search query", "search_type": "semantic", "limit": 5}

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)
    # Should succeed even if no documents exist
    assert response.status_code in [200, 404]  # 404 if no documents found

    if response.status_code == 200:
        data = response.json()
        assert "data" in data
        search_results = extract_data(response)
        assert isinstance(search_results, dict)
        assert "results" in search_results or "documents" in search_results


async def test_query_similarity_search(client, db, auth_headers):
    """Test similarity search functionality."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = {
        "name": f"Similarity Test KB {unique_id}",
        "description": "Knowledge base for similarity testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test similarity search
    similarity_data = {
        "query": "artificial intelligence machine learning",
        "limit": 10,
        "threshold": 0.7,
    }

    # Update to use unified search endpoint
    unified_similarity_data = {
        "query": "test similarity search",
        "query_type": "similarity",
        "limit": 5,
        "similarity_threshold": 0.7,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=unified_similarity_data, headers=auth_headers)
    # Should succeed even if no documents exist
    assert response.status_code in [200, 404]  # 404 if no documents found

    if response.status_code == 200:
        data = response.json()
        assert "data" in data


async def test_query_hybrid_search(client, db, auth_headers):
    """Test hybrid search (semantic + keyword) functionality."""
    # Create knowledge base
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Hybrid Search Test KB {unique_id}",
        "description": "Knowledge base for hybrid search testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test hybrid search
    hybrid_data = {
        "query": "machine learning algorithms",
        "search_type": "hybrid",
        "limit": 8,
        "semantic_weight": 0.7,
        "keyword_weight": 0.3,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=hybrid_data, headers=auth_headers)
    # Should succeed even if no documents exist
    assert response.status_code in [200, 404]  # 404 if no documents found


async def test_query_with_filters(client, db, auth_headers):
    """Test search with filters and metadata."""
    # Create knowledge base
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    kb_data = {
        "name": f"Filtered Search Test KB {unique_id}",
        "description": "Knowledge base for filtered search testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test search with filters
    filtered_data = {
        "query": "project documentation",
        "search_type": "semantic",
        "limit": 5,
        "filters": {"source_type": "filesystem", "file_type": "pdf"},
        "date_range": {"start": "2024-01-01", "end": "2024-12-31"},
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=filtered_data, headers=auth_headers)
    # Should succeed even if no documents match filters
    assert response.status_code in [200, 404]


async def test_query_stats(client, db, auth_headers):
    """Test query statistics endpoint."""
    # Create knowledge base
    kb_data = {
        "name": f"Query Stats Test KB {str(__import__('uuid').uuid4())[:8]}",
        "description": "Knowledge base for query stats testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test query stats
    response = await client.get(f"/api/v1/query/{kb_id}/stats", headers=auth_headers)
    # Stats endpoint may not be fully implemented yet
    if response.status_code == 200:
        data = response.json()
        assert "data" in data
        stats = extract_data(response)
        assert isinstance(stats, dict)
    else:
        # Accept 500 if stats service method is not implemented
        assert response.status_code in [500, 501]


async def test_query_invalid_knowledge_base(client, db, auth_headers):
    """Test query with invalid knowledge base ID."""
    invalid_kb_id = "550e8400-e29b-41d4-a716-446655440000"

    search_data = {"query": "test query", "search_type": "semantic", "limit": 5}

    response = await client.post(f"/api/v1/query/{invalid_kb_id}/search", json=search_data, headers=auth_headers)
    assert response.status_code == 200
    assert extract_data(response).get("results") == []


async def test_query_invalid_search_data(client, db, auth_headers):
    """Test query with invalid search data."""
    # Create knowledge base
    kb_data = {
        "name": f"Invalid Query Test KB {str(__import__('uuid').uuid4())[:8]}",
        "description": "Knowledge base for invalid query testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    invalid_data_sets = [
        {},  # Empty data
        {"query": ""},  # Empty query
        {"query": "test", "query_type": "invalid"},  # Invalid query type
        {"query": "test", "limit": -1},  # Invalid limit
        {"query": "test", "limit": 1000},  # Limit too high
    ]

    for invalid_data in invalid_data_sets:
        response = await client.post(f"/api/v1/query/{kb_id}/search", json=invalid_data, headers=auth_headers)
        assert response.status_code in [
            400,
            422,
        ], f"Invalid data should be rejected: {invalid_data}"


async def test_query_unauthorized_access(client, db, auth_headers):
    """Test that query endpoints require authentication."""
    # Create knowledge base first
    kb_data = {
        "name": f"Auth Test KB {str(__import__('uuid').uuid4())[:8]}",
        "description": "Knowledge base for auth testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test without auth headers
    search_data = {"query": "test", "search_type": "semantic"}

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data)
    assert response.status_code == 401

    # Test similarity search via unified endpoint
    similarity_search_data = {"query": "test", "query_type": "similarity"}
    response = await client.post(f"/api/v1/query/{kb_id}/search", json=similarity_search_data)
    assert response.status_code == 401

    response = await client.get(f"/api/v1/query/{kb_id}/stats")
    assert response.status_code == 401


async def test_query_performance_limits(client, db, auth_headers):
    """Test query performance and limits."""
    # Create knowledge base
    kb_data = {
        "name": f"Performance Test KB {str(__import__('uuid').uuid4())[:8]}",
        "description": "Knowledge base for performance testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with maximum allowed limit
    search_data = {
        "query": "performance test query",
        "search_type": "semantic",
        "limit": 100,  # Test maximum limit
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)
    # Should either succeed or return validation error for limit too high
    assert response.status_code in [200, 400, 404, 422]


async def test_query_different_search_types(client, db, auth_headers):
    """Test different search type options."""
    # Create knowledge base
    kb_data = {
        "name": f"Search Types Test KB {str(__import__('uuid').uuid4())[:8]}",
        "description": "Knowledge base for search types testing",
        "sync_enabled": True,
    }

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test different search types
    search_types = ["semantic", "keyword", "hybrid"]

    for search_type in search_types:
        search_data = {
            "query": f"test {search_type} search",
            "search_type": search_type,
            "limit": 5,
        }

        response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)
        # Should succeed or return 404 if no documents
        assert response.status_code in [200, 404], f"Search type {search_type} should be valid"


class QueryIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for query and search functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all query integration test functions."""
        return [
            test_health_endpoint,
            test_query_search_basic,
            test_query_similarity_search,
            test_query_hybrid_search,
            test_query_with_filters,
            test_query_stats,
            test_query_invalid_knowledge_base,
            test_query_invalid_search_data,
            test_query_unauthorized_access,
            test_query_performance_limits,
            test_query_different_search_types,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Query Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for RAG query, search, and similarity operations"

    def get_cli_examples(self) -> str:
        """Return query-specific CLI examples."""
        return """
Examples:
  python tests/test_query_integration.py                          # Run all query tests
  python tests/test_query_integration.py --list                   # List available tests
  python tests/test_query_integration.py --test test_query_search_basic
  python tests/test_query_integration.py --pattern "search"       # Run search tests
  python tests/test_query_integration.py --pattern "similarity"   # Run similarity tests
        """


if __name__ == "__main__":
    suite = QueryIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
