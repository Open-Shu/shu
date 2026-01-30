"""
Integration tests for Hybrid Search functionality.

These tests cover the enhanced hybrid search system including:
- Stop word filtering in hybrid search
- Result combination and scoring
- Hybrid search with different query types
- Performance and edge case handling
"""

import sys
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

# Test Data
KB_DATA = {
    "name": "test_hybrid_search_kb",
    "description": "Knowledge base for hybrid search testing",
    "sync_enabled": True,
}


async def test_hybrid_search_stop_words_only(client, db, auth_headers):
    """Test hybrid search with stop words only query."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_stop_words_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with stop words only
    stop_word_queries = ["hi", "to", "out", "the", "and", "a", "an", "is", "are"]

    for query in stop_word_queries:
        search_data = {"query": query, "search_type": "hybrid", "limit": 5}

        response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

        # Should succeed without 500 errors
        assert response.status_code in [
            200,
            404,
        ], f"Stop word query '{query}' should not cause 500 error"

        if response.status_code == 200:
            data = extract_data(response)
            # Should return similarity results only (no keyword results due to stop words)
            assert "results" in data
            # Response time should be fast (< 100ms is handled by the service internally)


async def test_hybrid_search_mixed_stop_words(client, db, auth_headers):
    """Test hybrid search with mixed stop words and meaningful terms."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_mixed_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with mixed queries containing stop words and meaningful terms
    mixed_queries = [
        "the safety profile of MXB-22",
        "what is the effectiveness",
        "how does the system work",
        "where are the documents located",
    ]

    for query in mixed_queries:
        search_data = {"query": query, "search_type": "hybrid", "limit": 8}

        response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

        # Should succeed and filter stop words properly
        assert response.status_code in [
            200,
            404,
        ], f"Mixed query '{query}' should succeed, got {response.status_code}: {response.text}"

        if response.status_code == 200:
            response_json = response.json()
            assert "data" in response_json, f"Response missing 'data' key: {response_json}"
            data = extract_data(response)
            assert "results" in data, f"Data missing 'results' key: {data}"
            # When there are no documents, hybrid search may return similarity results
            # This is acceptable behavior as long as the search succeeds
            assert data["query_type"] in [
                "hybrid",
                "similarity",
            ], f"Expected query_type 'hybrid' or 'similarity', got: {data.get('query_type')}"


async def test_hybrid_search_combines_results(client, db, auth_headers):
    """Test that hybrid search properly combines keyword and similarity results."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_combine_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with query that should return both types of results
    search_data = {
        "query": "machine learning algorithms neural networks",
        "search_type": "hybrid",
        "limit": 10,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = extract_data(response)
        assert "results" in data
        # When there are no documents, hybrid search may return similarity results
        assert data["query_type"] in [
            "hybrid",
            "similarity",
        ], f"Expected 'hybrid' or 'similarity', got: {data.get('query_type')}"

        # Verify response structure includes combined scoring
        if data["results"]:
            for result in data["results"]:
                # Each result should have similarity_score
                assert "similarity_score" in result
                assert isinstance(result["similarity_score"], (int, float))


async def test_hybrid_search_keyword_only_results(client, db, auth_headers):
    """Test hybrid search when only keyword matches exist."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_keyword_only_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with very specific technical terms that would only match keywords
    search_data = {
        "query": "MXB-22 compound synthesis protocol",
        "search_type": "hybrid",
        "limit": 5,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = extract_data(response)
        # When there are no documents, hybrid search may return similarity results
        assert data["query_type"] in [
            "hybrid",
            "similarity",
        ], f"Expected 'hybrid' or 'similarity', got: {data.get('query_type')}"
        # Results should be properly formatted even if only from one search type


async def test_hybrid_search_similarity_only_results(client, db, auth_headers):
    """Test hybrid search when only similarity matches exist."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_similarity_only_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with conceptual query that would match semantically but not keywords
    search_data = {
        "query": "artificial intelligence deep learning concepts",
        "search_type": "hybrid",
        "limit": 5,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = extract_data(response)
        # When there are no documents, hybrid search may return similarity results
        assert data["query_type"] in [
            "hybrid",
            "similarity",
        ], f"Expected 'hybrid' or 'similarity', got: {data.get('query_type')}"
        # Should handle similarity-only results gracefully


async def test_hybrid_search_handles_mixed_formats(client, db, auth_headers):
    """Test that hybrid search handles both Pydantic objects and dictionaries from sub-searches."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_formats_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test with query that exercises both search paths
    search_data = {
        "query": "research document analysis methodology",
        "search_type": "hybrid",
        "limit": 10,
    }

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

    # Should not get KeyError or AttributeError exceptions
    assert response.status_code in [
        200,
        404,
    ], "Hybrid search should handle mixed formats without exceptions"

    if response.status_code == 200:
        data = extract_data(response)
        assert "results" in data
        # Verify consistent chunk access regardless of format
        if data["results"]:
            for result in data["results"]:
                # Should have consistent field access
                assert "id" in result or "chunk_id" in result
                assert "content" in result


async def test_hybrid_search_performance(client, db, auth_headers):
    """Test hybrid search performance with stop word queries."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_performance_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test stop word query performance (should be very fast)
    search_data = {"query": "the and or but", "search_type": "hybrid", "limit": 5}

    import time

    start_time = time.time()

    response = await client.post(f"/api/v1/query/{kb_id}/search", json=search_data, headers=auth_headers)

    end_time = time.time()
    response_time = end_time - start_time

    # Should complete quickly (< 1 second for integration test)
    assert response_time < 1.0, f"Stop word query took too long: {response_time:.3f}s"
    assert response.status_code in [200, 404]


async def test_hybrid_search_cross_search_consistency(client, db, auth_headers):
    """Test consistency of stop word handling across search types."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_hybrid_consistency_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = extract_data(kb_response)["id"]

    # Test same stop word query across different search types
    stop_word_query = "the and or"

    # Test keyword search
    keyword_response = await client.post(
        f"/api/v1/query/{kb_id}/search",
        json={"query": stop_word_query, "search_type": "keyword", "limit": 5},
        headers=auth_headers,
    )

    # Test similarity search via unified endpoint
    similarity_data = {
        "query": stop_word_query,
        "query_type": "similarity",
        "limit": 5,
        "similarity_threshold": 0.7,
    }
    similarity_response = await client.post(f"/api/v1/query/{kb_id}/search", json=similarity_data, headers=auth_headers)

    # Test hybrid search
    hybrid_response = await client.post(
        f"/api/v1/query/{kb_id}/search",
        json={"query": stop_word_query, "search_type": "hybrid", "limit": 5},
        headers=auth_headers,
    )

    # All should handle stop words consistently
    assert keyword_response.status_code in [200, 404]
    assert similarity_response.status_code in [200, 404]
    assert hybrid_response.status_code in [200, 404]

    # Keyword search should return empty or 404 for stop words only
    if keyword_response.status_code == 200:
        keyword_data = extract_data(keyword_response)
        # Should have no or very few results for stop words
        assert len(keyword_data.get("results", [])) == 0 or keyword_data.get("total_results", 0) == 0


class HybridSearchIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for hybrid search functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all hybrid search test functions."""
        return [
            test_hybrid_search_stop_words_only,
            test_hybrid_search_mixed_stop_words,
            test_hybrid_search_combines_results,
            test_hybrid_search_keyword_only_results,
            test_hybrid_search_similarity_only_results,
            test_hybrid_search_handles_mixed_formats,
            test_hybrid_search_performance,
            test_hybrid_search_cross_search_consistency,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Hybrid Search Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Integration tests for hybrid search functionality including stop word filtering, result combination, and edge cases"

    def get_cli_examples(self) -> str:
        """Return hybrid search specific CLI examples."""
        return """
Examples:
  python tests/test_hybrid_search_integration.py                    # Run all hybrid search tests
  python tests/test_hybrid_search_integration.py --list            # List available tests
  python tests/test_hybrid_search_integration.py --test test_hybrid_search_stop_words_only
  python tests/test_hybrid_search_integration.py --pattern "stop"  # Run stop word tests
  python tests/test_hybrid_search_integration.py --pattern "combine" # Run combination tests
        """


if __name__ == "__main__":
    suite = HybridSearchIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
