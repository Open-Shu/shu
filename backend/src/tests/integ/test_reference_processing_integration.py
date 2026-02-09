"""
Integration tests for Reference Post-Processing functionality.

These tests cover the enhanced reference processing system including:
- Reference detection in LLM responses
- System reference addition logic
- Citation pattern recognition
- Reference format handling
"""

import sys
from collections.abc import Callable

from integ.base_integration_test import BaseIntegrationTestSuite

# Test Data
PROVIDER_DATA = {
    "name": "test_reference_provider",
    "provider_type": "openai",
    "api_endpoint": "https://api.openai.com/v1",
    "api_key": "test-api-key-12345",
    "is_active": True,
}

MODEL_DATA = {
    "model_name": "gpt-4",
    "display_name": "GPT-4 Reference Test Model",
    "description": "Test model for reference processing",
    "context_window": 8192,
    "max_tokens": 4096,
    "supports_streaming": True,
    "supports_functions": False,
    "supports_vision": False,
}

MODEL_CONFIG_DATA = {
    "name": "test_reference_assistant",
    "description": "Test model configuration for reference testing",
    "is_active": True,
    "created_by": "test-user",
    "knowledge_base_ids": [],
}

KB_DATA = {
    "name": "test_reference_kb",
    "description": "Knowledge base for reference testing",
    "sync_enabled": True,
}


async def test_detect_references_section_variations(client, db, auth_headers):
    """Test detection of various reference section headers."""
    # This test validates the reference detection logic by testing the prompt utils directly
    from shu.utils.prompt_utils import analyze_response_references

    # Test various reference section headers
    test_responses = [
        "Here is my analysis.\n\nReferences:\n- Document 1\n- Document 2",
        "Analysis complete.\n\nSources:\n- Source A\n- Source B",
        "Research findings.\n\nResources:\n- Resource X\n- Resource Y",
        "Study results.\n\nBibliography:\n- Paper 1\n- Paper 2",
        "Conclusion.\n\nFurther Reading:\n- Article A\n- Article B",
    ]

    for response in test_responses:
        analysis = analyze_response_references(response)
        assert (
            len(analysis["reference_section_indicators"]) > 0
        ), f"Should detect reference section in: {response[:50]}..."
        assert analysis["citation_patterns"], "Should detect citation patterns"


async def test_detect_inline_source_mentions(client, db, auth_headers):
    """Test detection of inline source mentions without formal reference sections."""
    from shu.utils.prompt_utils import analyze_response_references

    # Mock source metadata
    mock_sources = [
        {"document_title": "ML Paper 1", "source_url": "http://example.com/ml1"},
        {"document_title": "Document 2", "source_url": "http://example.com/doc2"},
    ]

    # Test responses with inline mentions
    test_responses = [
        "Based on ML Paper 1 and Document 2, we can conclude...",
        "According to the research in ML Paper 1, the results show...",
        "As stated in Document 2, the methodology involves...",
        "From ML Paper 1, we learn that the approach...",
    ]

    for response in test_responses:
        analysis = analyze_response_references(response, mock_sources)
        assert analysis["has_source_citations"], f"Should detect source citations in: {response[:50]}..."
        assert len(analysis["cited_sources"]) > 0, "Should identify cited sources"


async def test_detect_partial_title_matches(client, db, auth_headers):
    """Test detection of partial title matches and abbreviations."""
    from shu.utils.prompt_utils import analyze_response_references

    # Mock source with long title
    mock_sources = [
        {
            "document_title": "Machine Learning Applications Research Document.pdf",
            "source_url": "http://example.com/long",
        }
    ]

    # Test responses with partial matches that meet the 60% threshold
    test_responses = [
        "According to the Machine Learning Applications Research, we see...",  # 4/4 words = 100%
        "The Machine Learning Applications Document indicates...",  # 3/4 words = 75%
        "Research from Machine Learning Applications shows...",  # 3/4 words = 75%
    ]

    for response in test_responses:
        analysis = analyze_response_references(response, mock_sources)
        # Should recognize partial matches when enough words match (60%+ threshold)
        assert (
            analysis["has_source_citations"] or len(analysis["cited_sources"]) > 0
        ), f"Should detect partial match in: {response[:50]}..."


async def test_detect_filename_mentions(client, db, auth_headers):
    """Test detection of filename mentions in responses."""
    from shu.utils.prompt_utils import analyze_response_references

    # Mock sources with filenames
    mock_sources = [
        {"document_title": "Research_Report_2023.pdf", "source_url": "http://example.com/report"},
        {"document_title": "Analysis Document.docx", "source_url": "http://example.com/analysis"},
    ]

    # Test responses mentioning exact filenames or titles
    test_responses = [
        "According to Research_Report_2023.pdf, the findings are...",  # Exact filename match
        "Based on Analysis Document.docx, we can see...",  # Exact filename match
        "The Research_Report_2023.pdf shows important data...",  # Exact filename match
        "From Analysis Document.docx, the results indicate...",  # Exact filename match
    ]

    for response in test_responses:
        analysis = analyze_response_references(response, mock_sources)
        assert (
            analysis["has_source_citations"] or len(analysis["cited_sources"]) > 0
        ), f"Should detect filename mention in: {response[:50]}..."


async def test_detect_url_mentions(client, db, auth_headers):
    """Test detection of URL mentions in responses."""
    from shu.utils.prompt_utils import analyze_response_references

    # Mock sources with URLs
    mock_sources = [
        {"document_title": "Research Paper", "source_url": "https://example.com/research-paper"},
        {"document_title": "Study Document", "source_url": "https://docs.example.com/study"},
    ]

    # Test responses mentioning URLs
    test_responses = [
        "According to https://example.com/research-paper, the methodology...",
        "The study at https://docs.example.com/study shows...",
        "Based on the document at https://example.com/research-paper...",
    ]

    for response in test_responses:
        analysis = analyze_response_references(response, mock_sources)
        assert analysis["has_source_citations"], f"Should detect URL mention in: {response[:50]}..."
        assert len(analysis["cited_sources"]) > 0, "Should identify sources by URL"


async def test_no_citations_adds_all_sources(client, db, auth_headers):
    """Test that responses with no citations get all sources added."""
    from shu.utils.prompt_utils import should_add_system_references

    # Mock sources
    mock_sources = [
        {"document_title": "Source 1", "source_url": "http://example.com/1"},
        {"document_title": "Source 2", "source_url": "http://example.com/2"},
        {"document_title": "Source 3", "source_url": "http://example.com/3"},
    ]

    # Response with no citations
    response = "This is a general response about the topic without any specific citations or references."

    should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, True)

    assert should_add, "Should add system references when no citations found"
    assert reason == "no_citations_found", f"Expected 'no_citations_found', got '{reason}'"
    assert len(sources_to_add) == 3, "Should add all available sources"


async def test_partial_citations_adds_missing(client, db, auth_headers):
    """Test that responses with partial citations get missing sources added."""
    from shu.utils.prompt_utils import should_add_system_references

    # Mock sources
    mock_sources = [
        {"document_title": "Cited Source 1", "source_url": "http://example.com/1"},
        {"document_title": "Cited Source 2", "source_url": "http://example.com/2"},
        {"document_title": "Missing Source 3", "source_url": "http://example.com/3"},
        {"document_title": "Missing Source 4", "source_url": "http://example.com/4"},
    ]

    # Response citing only 2 of 4 sources
    response = (
        "Based on Cited Source 1 and Cited Source 2, we can conclude that the analysis shows significant results."
    )

    should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, True)

    assert should_add, "Should add system references for missing sources"
    assert reason == "missing_sources", f"Expected 'missing_sources', got '{reason}'"
    assert len(sources_to_add) == 2, "Should add only the missing sources"

    # Verify the correct sources are identified as missing
    missing_titles = [source["document_title"] for source in sources_to_add]
    assert "Missing Source 3" in missing_titles
    assert "Missing Source 4" in missing_titles


async def test_complete_citations_adds_nothing(client, db, auth_headers):
    """Test that responses with complete citations get no additional sources."""
    from shu.utils.prompt_utils import should_add_system_references

    # Mock sources
    mock_sources = [
        {"document_title": "Source A", "source_url": "http://example.com/a"},
        {"document_title": "Source B", "source_url": "http://example.com/b"},
    ]

    # Response citing all available sources
    response = "According to Source A and Source B, the research demonstrates clear evidence of the hypothesis."

    should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, True)

    assert not should_add, "Should not add system references when all sources are cited"
    assert reason == "complete_citations", f"Expected 'complete_citations', got '{reason}'"
    assert len(sources_to_add) == 0, "Should not add any sources"


async def test_citation_patterns_without_sources(client, db, auth_headers):
    """Test responses with citation patterns but no actual source mentions."""
    from shu.utils.prompt_utils import should_add_system_references

    # Mock sources
    mock_sources = [
        {"document_title": "Research Paper", "source_url": "http://example.com/research"},
        {"document_title": "Study Document", "source_url": "http://example.com/study"},
    ]

    # Response with citation patterns but no actual sources
    response = "The research shows [1] that the methodology is effective [2]. According to the analysis [3], results are significant."

    should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, True)

    assert should_add, "Should add system references when citations exist but no sources mentioned"
    assert reason == "citations_without_sources", f"Expected 'citations_without_sources', got '{reason}'"
    assert len(sources_to_add) == 2, "Should add all available sources"


async def test_kb_disabled_references(client, db, auth_headers):
    """Test that KB configuration can disable reference addition."""
    from shu.utils.prompt_utils import should_add_system_references

    # Mock sources
    mock_sources = [
        {"document_title": "Source 1", "source_url": "http://example.com/1"},
        {"document_title": "Source 2", "source_url": "http://example.com/2"},
    ]

    # Response with no citations
    response = "This is a response without any citations or references."

    # Test with KB references disabled
    should_add, reason, sources_to_add = should_add_system_references(response, mock_sources, False)

    assert not should_add, "Should not add references when KB has references disabled"
    assert reason == "kb_disabled", f"Expected 'kb_disabled', got '{reason}'"
    assert len(sources_to_add) == 0, "Should not add any sources when disabled"


async def test_reference_appending_to_existing_section(client, db, auth_headers):
    """Test appending missing sources to existing reference sections."""
    # This test validates the reference formatting logic
    # Since we can't easily test the full chat flow, we test the utility functions
    from shu.utils.prompt_utils import analyze_response_references

    # Response with existing references section
    response_with_refs = """
Here is my analysis of the data.

The results show significant improvements in performance.

References:
- Existing Source 1
- Existing Source 2
"""

    analysis = analyze_response_references(response_with_refs)
    assert len(analysis["reference_section_indicators"]) > 0, "Should detect existing reference section"
    assert analysis["citation_patterns"], "Should detect citation patterns in list format"


async def test_reference_section_creation(client, db, auth_headers):
    """Test creation of new reference sections when none exist."""
    from shu.utils.prompt_utils import analyze_response_references

    # Response without references section
    response_no_refs = "Here is my analysis of the data. The results show significant improvements."

    analysis = analyze_response_references(response_no_refs)
    assert len(analysis["reference_section_indicators"]) == 0, "Should not detect reference section"
    # This validates that new sections would need to be created


async def test_markdown_link_formatting(client, db, auth_headers):
    """Test detection of markdown link formatting in responses."""
    from shu.utils.prompt_utils import analyze_response_references

    # Response with markdown links
    response_with_links = "According to [Research Paper](http://example.com/paper) and [Study Document](http://example.com/study), the results are significant."

    analysis = analyze_response_references(response_with_links)
    assert "markdown_links" in analysis["citation_patterns"], "Should detect markdown link patterns"


async def test_plain_text_formatting(client, db, auth_headers):
    """Test detection of plain text reference formatting."""
    from shu.utils.prompt_utils import analyze_response_references

    # Response with plain text references
    response_plain = """
Analysis complete.

References:
- Document Title One
- Document Title Two
- Document Title Three
"""

    analysis = analyze_response_references(response_plain)
    assert "list_format" in analysis["citation_patterns"], "Should detect list format citations"
    assert len(analysis["reference_section_indicators"]) > 0, "Should detect reference section"


class ReferenceProcessingIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for reference post-processing functionality."""

    def get_test_functions(self) -> list[Callable]:
        """Return all reference processing test functions."""
        return [
            test_detect_references_section_variations,
            test_detect_inline_source_mentions,
            test_detect_partial_title_matches,
            test_detect_filename_mentions,
            test_detect_url_mentions,
            test_no_citations_adds_all_sources,
            test_partial_citations_adds_missing,
            test_complete_citations_adds_nothing,
            test_citation_patterns_without_sources,
            test_kb_disabled_references,
            test_reference_appending_to_existing_section,
            test_reference_section_creation,
            test_markdown_link_formatting,
            test_plain_text_formatting,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Reference Processing Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "Integration tests for reference post-processing including citation detection, source analysis, and system reference addition logic"

    def get_cli_examples(self) -> str:
        """Return reference processing specific CLI examples."""
        return """
Examples:
  python tests/test_reference_processing_integration.py                    # Run all reference tests
  python tests/test_reference_processing_integration.py --list            # List available tests
  python tests/test_reference_processing_integration.py --test test_detect_references_section_variations
  python tests/test_reference_processing_integration.py --pattern "detect" # Run detection tests
  python tests/test_reference_processing_integration.py --pattern "citations" # Run citation tests
        """


if __name__ == "__main__":
    suite = ReferenceProcessingIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
