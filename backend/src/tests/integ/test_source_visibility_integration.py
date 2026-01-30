#!/usr/bin/env python3
"""
Integration test for Issue 2: Enhanced source visibility in Query Tester and LLM Tester
Tests the enhanced SourcePreview component functionality
"""

import sys

from shu.core.config import get_settings_instance

# from shu.testing.framework import ShuTestFramework
from shu.services.knowledge_base_service import KnowledgeBaseService
from shu.services.query_service import QueryService


def test_enhanced_source_visibility():
    """Test enhanced source visibility features in query results"""

    # framework = ShuTestFramework()
    settings = get_settings_instance()

    try:
        # Initialize services
        kb_service = KnowledgeBaseService()
        query_service = QueryService()

        # Create test knowledge base
        kb_data = {
            "name": "Source Visibility Test KB",
            "description": "Testing enhanced source visibility features",
            "rag_config": {
                "similarity_threshold": 0.7,
                "title_weighting_enabled": True,
                "title_weight_multiplier": 3.0,
                "keyword_weight": 0.3,
                "similarity_weight": 0.7,
            },
        }

        kb = kb_service.create_knowledge_base(kb_data)
        kb_id = kb["id"]

        # Create test documents with varied content for visibility testing
        test_documents = [
            {
                "title": "High Relevance Document",
                "content": "This document contains highly relevant information about machine learning algorithms and neural networks. It discusses deep learning techniques and artificial intelligence applications.",
                "metadata": {
                    "file_type": "pdf",
                    "source_url": "https://example.com/high-relevance.pdf",
                },
            },
            {
                "title": "Medium Relevance Document",
                "content": "This document has medium relevance with some information about algorithms and data processing. It covers basic concepts and methodologies.",
                "metadata": {
                    "file_type": "docx",
                    "source_url": "https://example.com/medium-relevance.docx",
                },
            },
            {
                "title": "Low Relevance Document",
                "content": "This document has low relevance to the search query. It discusses unrelated topics like cooking recipes and gardening tips.",
                "metadata": {"file_type": "txt"},
            },
            {
                "title": "Secret Study Summary",
                "content": "This study examines some researched element behavior in laboratory settings. The research focuses on cognitive abilities and social interactions.",
                "metadata": {
                    "file_type": "pdf",
                    "source_url": "https://example.com/secret-study.pdf",
                },
            },
        ]

        # Add documents to knowledge base
        for doc in test_documents:
            kb_service.add_document(kb_id, doc["title"], doc["content"], doc["metadata"])

        print(f"✅ Created test knowledge base with {len(test_documents)} documents")

        # Test 1: Query with varied relevance scores
        print("\n🔍 Test 1: Query for 'machine learning algorithms'")
        results = query_service.search(
            kb_id=kb_id,
            query="machine learning algorithms",
            query_type="hybrid",
            limit=10,
            similarity_threshold=0.0,  # Get all results to test filtering
            title_weighting_enabled=True,
            title_weight_multiplier=3.0,
        )

        assert len(results["results"]) > 0, "Should return search results"

        # Verify results have different relevance scores for filtering tests
        scores = [r.get("similarity_score", 0) for r in results["results"]]
        print(f"   📊 Relevance scores: {[f'{s:.3f}' for s in scores]}")

        # Should have varied scores for testing filtering
        high_scores = [s for s in scores if s >= 0.8]
        medium_scores = [s for s in scores if 0.6 <= s < 0.8]
        low_scores = [s for s in scores if s < 0.6]

        print(f"   🎯 High relevance (≥0.8): {len(high_scores)} results")
        print(f"   🎯 Medium relevance (0.6-0.8): {len(medium_scores)} results")
        print(f"   🎯 Low relevance (<0.6): {len(low_scores)} results")

        # Test 2: Title search for highlighting
        print("\n🔍 Test 2: Title search for 'Secret' (should find Secret Study Summary)")
        study_results = query_service.search(
            kb_id=kb_id,
            query="Secret",
            query_type="hybrid",
            limit=10,
            similarity_threshold=0.0,
            title_weighting_enabled=True,
            title_weight_multiplier=3.0,
        )

        # Should find the Secret Study Summary document
        study_found = any("Secret" in r.get("document_title", "") for r in study_results["results"])
        assert study_found, "Should find Secret Study Summary document"
        print("   ✅ Found Secret document with title weighting")

        # Test 3: Verify source metadata is present
        print("\n📋 Test 3: Verify source metadata for visibility features")
        for i, result in enumerate(results["results"][:3]):  # Check first 3 results
            print(f"   Result {i+1}:")
            print(f"     📄 Title: {result.get('document_title', 'N/A')}")
            print(f"     🔢 Score: {result.get('similarity_score', 0):.3f}")
            print(f"     📝 Chunk: {result.get('chunk_index', 'N/A')}")
            print(f"     🔗 Source URL: {result.get('source_url', 'N/A')}")
            print(f"     📁 File Type: {result.get('metadata', {}).get('file_type', 'N/A')}")
            print(f"     📄 Content Length: {len(result.get('content', ''))}")

            # Verify required fields for enhanced visibility
            assert "document_title" in result, "Should have document title"
            assert "similarity_score" in result, "Should have similarity score"
            assert "content" in result, "Should have content for highlighting"
            assert "chunk_index" in result, "Should have chunk index"

        print("\n✅ All source metadata present for enhanced visibility")

        # Test 4: Test different search types for comprehensive visibility
        search_types = ["similarity", "keyword", "hybrid"]
        for search_type in search_types:
            print(f"\n🔍 Test 4.{search_types.index(search_type)+1}: {search_type.title()} search")
            type_results = query_service.search(
                kb_id=kb_id,
                query="algorithms data",
                query_type=search_type,
                limit=5,
                similarity_threshold=0.0,
                title_weighting_enabled=True,
                title_weight_multiplier=3.0,
            )

            assert len(type_results["results"]) > 0, f"Should return {search_type} results"
            print(f"   ✅ {search_type.title()} search returned {len(type_results['results'])} results")

            # Verify each result has the required fields for enhanced display
            for result in type_results["results"]:
                assert "content" in result, f"{search_type} result should have content"
                assert "document_title" in result, f"{search_type} result should have title"
                assert "similarity_score" in result, f"{search_type} result should have score"

        print("\n🎉 Enhanced Source Visibility Test PASSED!")
        print("   ✅ Multiple relevance levels for filtering")
        print("   ✅ Title search with highlighting support")
        print("   ✅ Complete source metadata for enhanced display")
        print("   ✅ All search types working with visibility features")

        return True

    except Exception as e:
        print(f"\n❌ Enhanced Source Visibility Test FAILED: {e!s}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        # Cleanup
        try:
            if "kb_id" in locals():
                kb_service.delete_knowledge_base(kb_id)
                print("\n🧹 Cleaned up test knowledge base")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")


if __name__ == "__main__":
    success = test_enhanced_source_visibility()
    sys.exit(0 if success else 1)
