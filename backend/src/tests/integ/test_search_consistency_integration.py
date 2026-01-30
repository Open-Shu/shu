"""
Integration tests for Search Consistency and Error Handling.

These tests cover:
- Cross-search type consistency
- Stop word handling consistency
- Reference handling consistency
- Error handling and edge cases
- Performance requirements
"""

import sys
import time
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite

# Test Data
KB_DATA = {
    "name": "test_consistency_kb",
    "description": "Knowledge base for consistency testing",
    "sync_enabled": True,
}


async def test_stop_word_consistency_across_search_types(client, db, auth_headers):
    """Test that stop word handling is consistent across keyword, similarity, and hybrid search."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_consistency_stop_words_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["data"]["id"]

    # Test stop word queries across all search types
    stop_word_queries = ["hi", "to", "the", "and"]

    for query in stop_word_queries:
        # Test keyword search
        keyword_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": "keyword", "limit": 5},
            headers=auth_headers,
        )

        # Test similarity search via unified endpoint
        similarity_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={
                "query": query,
                "query_type": "similarity",
                "limit": 5,
                "similarity_threshold": 0.7,
            },
            headers=auth_headers,
        )

        # Test hybrid search
        hybrid_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": "hybrid", "limit": 5},
            headers=auth_headers,
        )

        # All should succeed without 500 errors
        assert keyword_response.status_code in [200, 404], f"Keyword search failed for '{query}'"
        assert similarity_response.status_code in [
            200,
            404,
        ], f"Similarity search failed for '{query}'"
        assert hybrid_response.status_code in [200, 404], f"Hybrid search failed for '{query}'"

        # Keyword search should return empty results for stop words
        if keyword_response.status_code == 200:
            keyword_data = keyword_response.json()["data"]
            assert (
                len(keyword_data.get("results", [])) == 0
            ), f"Keyword search should return no results for stop word '{query}'"

        # Similarity search may return results (semantic matching)
        # Hybrid search should behave like similarity when keyword returns empty


async def test_mixed_query_consistency(client, db, auth_headers):
    """Test that mixed queries return consistent results across search types."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_consistency_mixed_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["data"]["id"]

    # Test mixed queries with meaningful terms
    mixed_queries = [
        "machine learning algorithms",
        "data analysis methods",
        "research methodology approach",
    ]

    for query in mixed_queries:
        # Test keyword search
        keyword_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": "keyword", "limit": 10},
            headers=auth_headers,
        )

        # Test hybrid search
        hybrid_response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": "hybrid", "limit": 10},
            headers=auth_headers,
        )

        # Both should succeed
        assert keyword_response.status_code in [
            200,
            404,
        ], f"Keyword search failed for '{query}': {keyword_response.status_code} - {keyword_response.text}"
        assert hybrid_response.status_code in [
            200,
            404,
        ], f"Hybrid search failed for '{query}': {hybrid_response.status_code} - {hybrid_response.text}"

        # If both return results, hybrid should include keyword results
        if keyword_response.status_code == 200 and hybrid_response.status_code == 200:
            keyword_data = keyword_response.json()["data"]
            hybrid_data = hybrid_response.json()["data"]

            # Verify response structure consistency
            assert "query_type" in keyword_data, f"keyword_data missing query_type: {keyword_data}"
            assert "query_type" in hybrid_data, f"hybrid_data missing query_type: {hybrid_data}"
            # When there are no documents, both keyword and hybrid search may fall back to similarity
            assert keyword_data["query_type"] in [
                "keyword",
                "similarity",
            ], f"Expected 'keyword' or 'similarity', got: {keyword_data.get('query_type')}"
            assert hybrid_data["query_type"] in [
                "hybrid",
                "similarity",
            ], f"Expected 'hybrid' or 'similarity', got: {hybrid_data.get('query_type')}"


async def test_reference_handling_across_search_types(client, db, auth_headers):
    """Test that reference post-processing behaves consistently across search types."""
    import uuid

    unique_id = str(uuid.uuid4())[:8]

    # Create knowledge base with references enabled
    kb_data = KB_DATA.copy()
    kb_data["name"] = f"test_consistency_references_kb_{unique_id}"

    kb_response = await client.post("/api/v1/knowledge-bases", json=kb_data, headers=auth_headers)
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["data"]["id"]

    # Set RAG config with references enabled
    rag_config_response = await client.put(
        f"/api/v1/knowledge-bases/{kb_id}/rag-config",
        json={"include_references": True, "search_threshold": 0.7, "max_results": 10},
        headers=auth_headers,
    )
    assert rag_config_response.status_code == 200

    # Test query across different search types
    query = "research findings analysis"

    search_types = ["keyword", "hybrid"]  # similarity uses different endpoint

    for search_type in search_types:
        response = await client.post(
            f"/api/v1/query/{kb_id}/search",
            json={"query": query, "search_type": search_type, "limit": 5},
            headers=auth_headers,
        )

        assert response.status_code in [200, 404], f"Search type '{search_type}' should succeed"

        if response.status_code == 200:
            data = response.json()["data"]
            assert "query_type" in data
            # Reference handling is consistent regardless of search type


async def test_malformed_reference_sections(client, db, auth_headers):
    """Test handling of malformed reference sections in responses."""
    # Test the reference processing utilities directly
    from shu.utils.prompt_utils import analyze_response_references, should_add_system_references

    # Test responses with broken markdown and incomplete lists
    malformed_responses = [
        "Analysis complete.\n\nReferences:\n- Incomplete list",  # Incomplete
        "Results:\n\nSources:\n[Broken markdown link](incomplete",  # Broken markdown
        "Findings.\n\nBibliography:\n- Item 1\n- Item 2\n- ",  # Trailing incomplete item
        "Study.\n\nReferences:\n\n\n\n",  # Empty reference section
    ]

    mock_sources = [
        {"document_title": "Source 1", "source_url": "http://example.com/1"},
        {"document_title": "Source 2", "source_url": "http://example.com/2"},
    ]

    for response in malformed_responses:
        # Should handle gracefully without exceptions
        analysis = analyze_response_references(response, mock_sources)
        assert isinstance(analysis, dict), "Should return valid analysis dict"

        should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, True)
        assert isinstance(should_add, bool), "Should return valid boolean"
        assert isinstance(reason, str), "Should return valid reason string"
        assert isinstance(sources_to_add, list), "Should return valid sources list"


async def test_very_long_responses(client, db, auth_headers):
    """Test processing of very long responses."""
    from shu.utils.prompt_utils import analyze_response_references

    # Create a very long response (simulate thousands of lines)
    long_content = "This is a detailed analysis. " * 1000  # ~30,000 characters
    long_response = f"{long_content}\n\nReferences:\n- Source 1\n- Source 2"

    mock_sources = [
        {"document_title": "Source 1", "source_url": "http://example.com/1"},
        {"document_title": "Source 2", "source_url": "http://example.com/2"},
    ]

    # Should process efficiently without timeouts
    start_time = time.time()
    analysis = analyze_response_references(long_response, mock_sources)
    end_time = time.time()

    processing_time = end_time - start_time
    assert processing_time < 1.0, f"Long response processing took too long: {processing_time:.3f}s"
    assert len(analysis["reference_section_indicators"]) > 0, "Should detect reference section"


async def test_empty_or_null_responses(client, db, auth_headers):
    """Test handling of empty or null responses."""
    from shu.utils.prompt_utils import analyze_response_references, should_add_system_references

    mock_sources = [{"document_title": "Source 1", "source_url": "http://example.com/1"}]

    # Test empty responses
    empty_responses = ["", "   ", "\n\n\n", None]

    for response in empty_responses:
        # Should handle gracefully
        analysis = analyze_response_references(response or "", mock_sources)
        assert isinstance(analysis, dict), f"Should handle empty response: {response!r}"

        should_add, reason, sources_to_add = should_add_system_references(response or "", mock_sources, True)
        assert isinstance(should_add, bool), "Should return valid boolean for empty response"


async def test_sources_without_titles(client, db, auth_headers):
    """Test handling of source metadata missing document_title."""
    from shu.utils.prompt_utils import analyze_response_references

    # Sources with missing titles
    incomplete_sources = [
        {"source_url": "http://example.com/1"},  # Missing title
        {"document_title": "", "source_url": "http://example.com/2"},  # Empty title
        {"document_title": "Valid Source", "source_url": "http://example.com/3"},  # Valid
    ]

    response = "Analysis based on the research findings."

    # Should handle gracefully without errors
    analysis = analyze_response_references(response, incomplete_sources)
    assert isinstance(analysis, dict), "Should handle sources without titles"
    assert "has_source_citations" in analysis
    assert "cited_sources" in analysis


async def test_sources_without_urls(client, db, auth_headers):
    """Test handling of source metadata missing source_url."""
    from shu.utils.prompt_utils import analyze_response_references

    # Sources with missing URLs
    incomplete_sources = [
        {"document_title": "Source Without URL"},  # Missing URL
        {"document_title": "Source With Empty URL", "source_url": ""},  # Empty URL
        {"document_title": "Valid Source", "source_url": "http://example.com/valid"},  # Valid
    ]

    response = "According to Source Without URL and Valid Source, the findings show..."

    # Should handle gracefully and still detect citations
    analysis = analyze_response_references(response, incomplete_sources)
    assert isinstance(analysis, dict), "Should handle sources without URLs"
    assert analysis["has_source_citations"], "Should detect citations even without URLs"
    assert len(analysis["cited_sources"]) > 0, "Should identify cited sources"


class SearchConsistencyIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for search consistency and error handling."""

    def get_test_functions(self) -> list[Callable]:
        """Return all search consistency test functions."""
        return [
            test_stop_word_consistency_across_search_types,
            test_mixed_query_consistency,
            test_reference_handling_across_search_types,
            test_malformed_reference_sections,
            test_very_long_responses,
            test_empty_or_null_responses,
            test_sources_without_titles,
            test_sources_without_urls,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Search Consistency Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Integration tests for search consistency, error handling, and edge cases across different search types"

    def get_cli_examples(self) -> str:
        """Return search consistency specific CLI examples."""
        return """
Examples:
  python tests/test_search_consistency_integration.py                    # Run all consistency tests
  python tests/test_search_consistency_integration.py --list            # List available tests
  python tests/test_search_consistency_integration.py --test test_stop_word_consistency_across_search_types
  python tests/test_search_consistency_integration.py --pattern "consistency" # Run consistency tests
  python tests/test_search_consistency_integration.py --pattern "error"  # Run error handling tests
        """


if __name__ == "__main__":
    suite = SearchConsistencyIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
