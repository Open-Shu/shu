"""
Unit tests for QuerySynthesisService (SHU-353).

Tests the pure LLM logic for query synthesis, mocking the SideCallService.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.schemas.query_synthesis import MainIdea, SynthesizedQuery
from shu.services.query_synthesis_service import (
    MAIN_IDEAS_SYSTEM_PROMPT,
    QUERY_GENERATION_SYSTEM_PROMPT,
    QuerySynthesisService,
)
from shu.services.side_call_service import SideCallResult


@pytest.fixture
def mock_settings():
    """Mock settings with query synthesis configuration."""
    settings = MagicMock()
    settings.query_synthesis_timeout_seconds = 90
    settings.query_synthesis_max_queries = 20
    settings.query_synthesis_min_queries = 3
    settings.profiling_max_input_tokens = 8000
    return settings


@pytest.fixture
def mock_side_call_service():
    """Mock SideCallService for unit testing."""
    return AsyncMock()


@pytest.fixture
def query_synthesis_service(mock_side_call_service, mock_settings):
    """Create a QuerySynthesisService with mocked dependencies."""
    return QuerySynthesisService(mock_side_call_service, mock_settings)


class TestSynthesizeQueries:
    """Tests for synthesize_queries method."""

    @pytest.mark.asyncio
    async def test_synthesize_queries_success(self, query_synthesis_service, mock_side_call_service):
        """Test successful query synthesis with two-stage approach."""
        # Stage 1: Main ideas extraction
        main_ideas_response = json.dumps([
            {"topic": "API authentication", "description": "How to authenticate API requests"},
            {"topic": "Rate limiting", "description": "Request rate limits and quotas"},
        ])
        # Stage 2: Query generation
        queries_response = json.dumps([
            {"query_text": "How do I authenticate API requests?", "query_type": "interrogative", "topic_covered": "API authentication"},
            {"query_text": "What are the rate limits?", "query_type": "interrogative", "topic_covered": "Rate limiting"},
            {"query_text": "API authentication methods", "query_type": "declarative", "topic_covered": "API authentication"},
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=100),
            SideCallResult(content=queries_response, success=True, tokens_used=150),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Sample API documentation about authentication and rate limiting."
        )

        assert result.success is True
        assert len(result.queries) == 3
        assert len(result.main_ideas) == 2
        assert result.tokens_used == 250
        assert result.error is None
        assert mock_side_call_service.call.call_count == 2

    @pytest.mark.asyncio
    async def test_synthesize_queries_with_synopsis(self, query_synthesis_service, mock_side_call_service):
        """Test that synopsis is used for main idea extraction when provided."""
        main_ideas_response = json.dumps([{"topic": "Budget", "description": "Q3 budget details"}])
        queries_response = json.dumps([
            {"query_text": "What is the Q3 budget?", "query_type": "interrogative", "topic_covered": "Budget"},
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=75),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Full document text here...",
            synopsis="This document covers Q3 budget allocations.",
        )

        assert result.success is True
        # Verify synopsis was used (first call should contain synopsis, not full doc)
        first_call = mock_side_call_service.call.call_args_list[0]
        user_content = first_call[1]["message_sequence"][0]["content"]
        assert "Q3 budget allocations" in user_content

    @pytest.mark.asyncio
    async def test_synthesize_queries_reuses_capability_manifest(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test that capability manifest topics skip main idea extraction."""
        queries_response = json.dumps([
            {"query_text": "How to deploy?", "query_type": "interrogative", "topic_covered": "deployment"},
            {"query_text": "Scaling configuration", "query_type": "declarative", "topic_covered": "scaling"},
        ])

        mock_side_call_service.call.return_value = SideCallResult(
            content=queries_response, success=True, tokens_used=100
        )

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document about deployment and scaling.",
            capability_manifest={"answers_questions_about": ["deployment", "scaling", "monitoring"]},
        )

        assert result.success is True
        # Should only call once (query generation), skipping main idea extraction
        assert mock_side_call_service.call.call_count == 1
        # Main ideas should be populated from capability manifest
        assert len(result.main_ideas) == 3
        assert result.main_ideas[0].topic == "deployment"

    @pytest.mark.asyncio
    async def test_synthesize_queries_main_idea_extraction_failure(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling of LLM failure during main idea extraction."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="", success=False, error_message="Rate limited", tokens_used=0
        )

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is False
        assert "Failed to extract main ideas" in result.error
        assert result.queries == []

    @pytest.mark.asyncio
    async def test_synthesize_queries_query_generation_failure(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling of LLM failure during query generation."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content="", success=False, error_message="Timeout", tokens_used=0),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is False
        assert "Failed to generate queries" in result.error
        # Main ideas should still be populated
        assert len(result.main_ideas) == 1

    @pytest.mark.asyncio
    async def test_synthesize_queries_no_main_ideas_found(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling when no main ideas can be identified."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="[]", success=True, tokens_used=50
        )

        result = await query_synthesis_service.synthesize_queries(
            document_text="Very short or empty document"
        )

        assert result.success is False
        assert "No main ideas" in result.error

    @pytest.mark.asyncio
    async def test_synthesize_queries_enforces_max_limit(
        self, query_synthesis_service, mock_side_call_service, mock_settings
    ):
        """Test that query count is capped at max_queries."""
        mock_settings.query_synthesis_max_queries = 5

        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        # LLM returns more queries than allowed
        queries_response = json.dumps([
            {"query_text": f"Query {i}", "query_type": "interrogative", "topic_covered": "Topic"}
            for i in range(10)
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=200),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is True
        assert len(result.queries) == 5  # Capped at max

    @pytest.mark.asyncio
    async def test_synthesize_queries_custom_max_override(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test that max_queries parameter overrides settings."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        queries_response = json.dumps([
            {"query_text": f"Query {i}", "query_type": "interrogative", "topic_covered": "Topic"}
            for i in range(10)
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=200),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text",
            max_queries=3,
        )

        assert result.success is True
        assert len(result.queries) == 3


class TestJsonParsing:
    """Tests for JSON parsing edge cases."""

    @pytest.mark.asyncio
    async def test_parse_main_ideas_markdown_code_block(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test parsing main ideas wrapped in markdown code blocks."""
        main_ideas_response = """```json
[{"topic": "Wrapped topic", "description": "In code block"}]
```"""
        queries_response = json.dumps([
            {"query_text": "Query", "query_type": "interrogative", "topic_covered": "Wrapped topic"}
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=75),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is True
        assert result.main_ideas[0].topic == "Wrapped topic"

    @pytest.mark.asyncio
    async def test_parse_queries_markdown_code_block(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test parsing queries wrapped in markdown code blocks."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        queries_response = """```
[{"query_text": "Wrapped query", "query_type": "declarative", "topic_covered": "Topic"}]
```"""

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=75),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is True
        assert result.queries[0].query_text == "Wrapped query"

    @pytest.mark.asyncio
    async def test_parse_main_ideas_invalid_json(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling of invalid JSON in main ideas response."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="This is not valid JSON at all",
            success=True,
            tokens_used=50,
        )

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is False
        assert "No main ideas" in result.error

    @pytest.mark.asyncio
    async def test_parse_queries_invalid_json(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling of invalid JSON in queries response."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content="Not valid JSON", success=True, tokens_used=75),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        # Query generation succeeded (LLM call worked) but parsing failed
        # Result should still be success=True but with empty queries
        assert result.success is True
        assert result.queries == []

    @pytest.mark.asyncio
    async def test_parse_main_ideas_not_a_list(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test handling when main ideas response is not a list."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"topic": "Single object, not array"}',
            success=True,
            tokens_used=50,
        )

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is False
        assert "No main ideas" in result.error


class TestInputValidation:
    """Tests for input validation and token limits."""

    @pytest.mark.asyncio
    async def test_input_exceeds_token_limit(
        self, query_synthesis_service, mock_side_call_service, mock_settings
    ):
        """Test that oversized input is rejected."""
        mock_settings.profiling_max_input_tokens = 100

        # Create input that exceeds token limit (rough estimate: 1 token ≈ 4 chars)
        large_text = "word " * 500  # ~500 tokens

        result = await query_synthesis_service.synthesize_queries(
            document_text=large_text
        )

        assert result.success is False
        assert "exceeds max tokens" in result.error
        mock_side_call_service.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_correct_system_prompts(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test that correct system prompts are used for each stage."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        queries_response = json.dumps([
            {"query_text": "Query", "query_type": "interrogative", "topic_covered": "Topic"}
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=75),
        ]

        await query_synthesis_service.synthesize_queries(document_text="Document text")

        # First call should use main ideas prompt
        first_call = mock_side_call_service.call.call_args_list[0]
        assert first_call[1]["system_prompt"] == MAIN_IDEAS_SYSTEM_PROMPT

        # Second call should use query generation prompt
        second_call = mock_side_call_service.call.call_args_list[1]
        assert second_call[1]["system_prompt"] == QUERY_GENERATION_SYSTEM_PROMPT


class TestQueryTypes:
    """Tests for query type handling."""

    @pytest.mark.asyncio
    async def test_preserves_query_types(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test that query types are preserved from LLM response."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        queries_response = json.dumps([
            {"query_text": "What is X?", "query_type": "interrogative", "topic_covered": "Topic"},
            {"query_text": "Show me X", "query_type": "imperative", "topic_covered": "Topic"},
            {"query_text": "X configuration", "query_type": "declarative", "topic_covered": "Topic"},
            {"query_text": "Why use X?", "query_type": "interpretive", "topic_covered": "Topic"},
            {"query_text": "When was X updated?", "query_type": "temporal", "topic_covered": "Topic"},
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=150),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is True
        query_types = [q.query_type for q in result.queries]
        assert "interrogative" in query_types
        assert "imperative" in query_types
        assert "declarative" in query_types
        assert "interpretive" in query_types
        assert "temporal" in query_types

    @pytest.mark.asyncio
    async def test_defaults_missing_query_type(
        self, query_synthesis_service, mock_side_call_service
    ):
        """Test that missing query_type defaults to interrogative."""
        main_ideas_response = json.dumps([{"topic": "Topic", "description": "Desc"}])
        queries_response = json.dumps([
            {"query_text": "Query without type", "topic_covered": "Topic"},
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=main_ideas_response, success=True, tokens_used=50),
            SideCallResult(content=queries_response, success=True, tokens_used=75),
        ]

        result = await query_synthesis_service.synthesize_queries(
            document_text="Document text"
        )

        assert result.success is True
        assert result.queries[0].query_type == "interrogative"
