"""
Unit tests for ProfilingService (SHU-343, SHU-581).

Tests the pure LLM logic for document and chunk profiling, mocking the SideCallService.
SHU-581 added unified profiling for small documents.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.schemas.profiling import (
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentType,
)
from shu.services.profiling_service import (
    AGGREGATE_PROFILE_SYSTEM_PROMPT,
    CHUNK_PROFILE_SYSTEM_PROMPT,
    UNIFIED_PROFILING_SYSTEM_PROMPT,
    ProfilingService,
)
from shu.services.side_call_service import SideCallResult


@pytest.fixture
def mock_settings():
    """Mock settings with profiling configuration."""
    settings = MagicMock()
    settings.profiling_timeout_seconds = 60
    settings.chunk_profiling_batch_size = 5
    settings.profiling_full_doc_max_tokens = 4000
    settings.profiling_max_input_tokens = 8000
    return settings


@pytest.fixture
def mock_side_call_service():
    """Mock SideCallService for unit testing."""
    return AsyncMock()


@pytest.fixture
def profiling_service(mock_side_call_service, mock_settings):
    """Create a ProfilingService with mocked dependencies."""
    return ProfilingService(mock_side_call_service, mock_settings)


class TestUnifiedProfiling:
    """Tests for profile_document_unified method (SHU-581)."""

    @pytest.mark.asyncio
    async def test_unified_profiling_success(self, profiling_service, mock_side_call_service):
        """Test successful unified profiling for small documents."""
        llm_response = json.dumps({
            "synopsis": "A technical document about API design patterns.",
            "chunks": [
                {
                    "index": 0,
                    "one_liner": "Explains REST API basics",
                    "summary": "Introduction to REST API design",
                    "keywords": ["REST", "API"],
                    "topics": ["web development"],
                },
                {
                    "index": 1,
                    "one_liner": "Covers authentication methods",
                    "summary": "Authentication and authorization patterns",
                    "keywords": ["OAuth", "JWT"],
                    "topics": ["security"],
                },
            ],
            "document_type": "technical",
            "capability_manifest": {
                "answers_questions_about": ["API design", "authentication"],
                "provides_information_type": ["instructions", "facts"],
                "authority_level": "primary",
                "completeness": "complete",
                "question_domains": ["what", "how"],
            },
            "synthesized_queries": [
                "How do I design a REST API?",
                "What authentication methods are available?",
            ],
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=250
        )

        chunks = [
            ChunkData(chunk_id="c1", chunk_index=0, content="REST API content"),
            ChunkData(chunk_id="c2", chunk_index=1, content="Auth content"),
        ]
        unified, result = await profiling_service.profile_document_unified(
            document_text="Full document text",
            chunks=chunks,
        )

        assert result.success is True
        assert unified is not None
        assert unified.synopsis == "A technical document about API design patterns."
        assert len(unified.chunks) == 2
        assert unified.chunks[0].one_liner == "Explains REST API basics"
        assert unified.chunks[1].one_liner == "Covers authentication methods"
        assert len(unified.synthesized_queries) == 2
        assert "API design" in unified.capability_manifest.answers_questions_about
        mock_side_call_service.call.assert_called_once()
        call_kwargs = mock_side_call_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == UNIFIED_PROFILING_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_unified_profiling_with_metadata(self, profiling_service, mock_side_call_service):
        """Test unified profiling includes document metadata."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","chunks":[],"document_type":"narrative","capability_manifest":{},"synthesized_queries":[]}',
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        await profiling_service.profile_document_unified(
            document_text="Document text",
            chunks=chunks,
            document_metadata={"title": "My Doc", "source": "email"},
        )

        call_kwargs = mock_side_call_service.call.call_args[1]
        user_message = call_kwargs["message_sequence"][0]["content"]
        assert "My Doc" in user_message
        assert "email" in user_message

    @pytest.mark.asyncio
    async def test_unified_profiling_llm_failure(self, profiling_service, mock_side_call_service):
        """Test handling of LLM call failure."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="", success=False, error_message="Rate limited"
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        unified, result = await profiling_service.profile_document_unified(
            document_text="Document text",
            chunks=chunks,
        )

        assert result.success is False
        assert unified is None
        assert result.error_message == "Rate limited"

    @pytest.mark.asyncio
    async def test_unified_profiling_handles_query_objects(self, profiling_service, mock_side_call_service):
        """Test that queries returned as objects are converted to strings."""
        llm_response = json.dumps({
            "synopsis": "Test",
            "chunks": [],
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": [
                {"query_text": "What is X?"},
                {"query_text": "How does Y work?"},
            ],
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=100
        )

        chunks = []
        unified, result = await profiling_service.profile_document_unified(
            document_text="Document text",
            chunks=chunks,
        )

        assert unified is not None
        assert unified.synthesized_queries == ["What is X?", "How does Y work?"]


class TestDocumentProfiling:
    """Tests for profile_document method (legacy, for backward compatibility)."""

    @pytest.mark.asyncio
    async def test_profile_document_success(self, profiling_service, mock_side_call_service):
        """Test successful document profiling."""
        llm_response = json.dumps({
            "synopsis": "A technical document about API design patterns.",
            "document_type": "technical",
            "capability_manifest": {
                "answers_questions_about": ["API design", "REST conventions"],
                "provides_information_type": ["instructions", "facts"],
                "authority_level": "primary",
                "completeness": "complete",
                "question_domains": ["what", "how"],
            },
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=150
        )

        profile, result = await profiling_service.profile_document("Sample document text")

        assert result.success is True
        assert profile is not None
        assert profile.synopsis == "A technical document about API design patterns."
        assert profile.document_type == DocumentType.TECHNICAL
        assert "API design" in profile.capability_manifest.answers_questions_about
        mock_side_call_service.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_profile_document_with_metadata(self, profiling_service, mock_side_call_service):
        """Test profiling includes document metadata."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","document_type":"narrative","capability_manifest":{}}',
            success=True,
            tokens_used=100,
        )

        await profiling_service.profile_document(
            "Document text",
            document_metadata={"title": "My Doc", "source": "email"},
        )

        call_kwargs = mock_side_call_service.call.call_args[1]
        user_message = call_kwargs["message_sequence"][0]["content"]
        assert "My Doc" in user_message
        assert "email" in user_message

    @pytest.mark.asyncio
    async def test_profile_document_llm_failure(self, profiling_service, mock_side_call_service):
        """Test handling of LLM call failure."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="", success=False, error_message="Rate limited"
        )

        profile, result = await profiling_service.profile_document("Document text")

        assert result.success is False
        assert profile is None
        assert result.error_message == "Rate limited"

    @pytest.mark.asyncio
    async def test_profile_document_invalid_json(self, profiling_service, mock_side_call_service):
        """Test handling of invalid JSON response."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="This is not valid JSON", success=True, tokens_used=50
        )

        profile, result = await profiling_service.profile_document("Document text")

        assert result.success is True  # LLM call succeeded
        assert profile is None  # But parsing failed

    @pytest.mark.asyncio
    async def test_profile_document_markdown_json(self, profiling_service, mock_side_call_service):
        """Test parsing JSON wrapped in markdown code blocks."""
        llm_response = """```json
{"synopsis":"Wrapped in code blocks","document_type":"narrative","capability_manifest":{}}
```"""
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=100
        )

        profile, result = await profiling_service.profile_document("Document text")

        assert profile is not None
        assert profile.synopsis == "Wrapped in code blocks"

    @pytest.mark.asyncio
    async def test_profile_document_unknown_type_defaults(self, profiling_service, mock_side_call_service):
        """Test fallback to NARRATIVE for unknown document types."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","document_type":"unknown_type","capability_manifest":{}}',
            success=True,
            tokens_used=50,
        )

        profile, _ = await profiling_service.profile_document("Document text")

        assert profile.document_type == DocumentType.NARRATIVE


class TestChunkProfiling:
    """Tests for profile_chunks method."""

    @pytest.mark.asyncio
    async def test_profile_chunks_success(self, profiling_service, mock_side_call_service):
        """Test successful chunk profiling with one_liner."""
        llm_response = json.dumps([
            {
                "one_liner": "Covers user authentication",
                "summary": "First chunk about users",
                "keywords": ["user", "auth"],
                "topics": ["security"],
            },
            {
                "one_liner": "Explains API endpoints",
                "summary": "Second chunk about APIs",
                "keywords": ["REST", "HTTP"],
                "topics": ["integration"],
            },
        ])
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=200
        )

        chunks = [
            ChunkData(chunk_id="c1", chunk_index=0, content="User auth content"),
            ChunkData(chunk_id="c2", chunk_index=1, content="API content"),
        ]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        assert tokens_used == 200
        assert len(results) == 2
        assert results[0].success is True
        assert results[0].chunk_id == "c1"
        assert results[0].profile.one_liner == "Covers user authentication"
        assert results[0].profile.summary == "First chunk about users"
        assert "user" in results[0].profile.keywords
        assert results[1].success is True
        assert results[1].profile.one_liner == "Explains API endpoints"
        mock_side_call_service.call.assert_called_once()
        call_kwargs = mock_side_call_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == CHUNK_PROFILE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_profile_chunks_empty_list(self, profiling_service, mock_side_call_service):
        """Test profiling empty chunk list."""
        results, tokens_used = await profiling_service.profile_chunks([])
        assert results == []
        assert tokens_used == 0
        mock_side_call_service.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_profile_chunks_batching(self, profiling_service, mock_side_call_service, mock_settings):
        """Test that chunks are processed in batches."""
        mock_settings.chunk_profiling_batch_size = 2

        def make_response(call_count):
            profiles = [
                {"one_liner": f"One-liner {i}", "summary": f"Profile {i}", "keywords": [], "topics": []}
                for i in range(2)
            ]
            return SideCallResult(content=json.dumps(profiles), success=True, tokens_used=100)

        mock_side_call_service.call.side_effect = [
            make_response(0),
            make_response(1),
            make_response(2),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(5)
        ]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        # Should have 3 calls: 2+2+1 chunks
        assert mock_side_call_service.call.call_count == 3
        assert len(results) == 5
        assert tokens_used == 300  # 100 per batch * 3 batches

    @pytest.mark.asyncio
    async def test_profile_chunks_llm_failure(self, profiling_service, mock_side_call_service):
        """Test handling of LLM failure during chunk profiling."""
        mock_side_call_service.call.return_value = SideCallResult(
            content="", success=False, error_message="Timeout"
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Timeout"
        assert results[0].profile.summary == ""
        assert tokens_used == 0

    @pytest.mark.asyncio
    async def test_profile_chunks_truncates_long_data(self, profiling_service, mock_side_call_service):
        """Test that excessively long profile data is truncated."""
        long_one_liner = "x" * 200
        long_summary = "y" * 1000
        long_keywords = [f"kw{i}" for i in range(50)]
        llm_response = json.dumps([{
            "one_liner": long_one_liner,
            "summary": long_summary,
            "keywords": long_keywords,
            "topics": [f"t{i}" for i in range(20)],
        }])
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=500
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        assert len(results[0].profile.one_liner) <= 100
        assert len(results[0].profile.summary) <= 500
        assert len(results[0].profile.keywords) <= 15
        assert len(results[0].profile.topics) <= 10
        assert tokens_used == 500


class TestAggregateProfiles:
    """Tests for aggregate_chunk_profiles method."""

    @pytest.mark.asyncio
    async def test_aggregate_profiles_success(self, profiling_service, mock_side_call_service):
        """Test successful aggregation of chunk profiles with queries."""
        llm_response = json.dumps({
            "synopsis": "Aggregated document about APIs and security.",
            "document_type": "technical",
            "capability_manifest": {
                "answers_questions_about": ["APIs", "security"],
                "provides_information_type": ["instructions"],
                "authority_level": "primary",
                "completeness": "complete",
                "question_domains": ["how"],
            },
            "synthesized_queries": [
                "How do I secure my API?",
                "What authentication methods are available?",
            ],
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=300
        )

        chunk_profiles = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(
                    one_liner="Covers API basics",
                    summary="About APIs",
                    keywords=["REST"],
                    topics=["integration"],
                ),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2",
                chunk_index=1,
                profile=ChunkProfile(
                    one_liner="Explains security",
                    summary="About security",
                    keywords=["auth"],
                    topics=["security"],
                ),
                success=True,
            ),
        ]

        aggregate_result, result = await profiling_service.aggregate_chunk_profiles(chunk_profiles)

        assert result.success is True
        assert aggregate_result is not None
        profile, queries = aggregate_result
        assert "APIs" in profile.synopsis or "Aggregated" in profile.synopsis
        assert len(queries) == 2
        assert "How do I secure my API?" in queries
        call_kwargs = mock_side_call_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == AGGREGATE_PROFILE_SYSTEM_PROMPT
        # Check that one-liners are used in the request
        user_content = call_kwargs["message_sequence"][0]["content"]
        assert "Covers API basics" in user_content
        assert "Explains security" in user_content

    @pytest.mark.asyncio
    async def test_aggregate_uses_one_liners(self, profiling_service, mock_side_call_service):
        """Test that aggregation prefers one_liner over summary."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","document_type":"narrative","capability_manifest":{},"synthesized_queries":[]}',
            success=True,
            tokens_used=100,
        )

        chunk_profiles = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(
                    one_liner="Short one-liner",
                    summary="Much longer summary that should not be used",
                    keywords=[],
                    topics=[],
                ),
                success=True,
            ),
        ]

        await profiling_service.aggregate_chunk_profiles(chunk_profiles)

        user_content = mock_side_call_service.call.call_args[1]["message_sequence"][0]["content"]
        assert "Short one-liner" in user_content
        # Summary should not appear since one_liner is available
        assert "Much longer summary" not in user_content

    @pytest.mark.asyncio
    async def test_aggregate_ignores_failed_chunks(self, profiling_service, mock_side_call_service):
        """Test that failed chunk profiles are excluded from aggregation."""
        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","document_type":"narrative","capability_manifest":{},"synthesized_queries":[]}',
            success=True,
            tokens_used=100,
        )

        chunk_profiles = [
            ChunkProfileResult(
                chunk_id="c1",
                chunk_index=0,
                profile=ChunkProfile(
                    one_liner="Good profile",
                    summary="Good summary",
                    keywords=["key"],
                    topics=["topic"],
                ),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2",
                chunk_index=1,
                profile=ChunkProfile(one_liner="", summary="", keywords=[], topics=[]),
                success=False,
                error="Failed to profile",
            ),
        ]

        await profiling_service.aggregate_chunk_profiles(chunk_profiles)

        user_content = mock_side_call_service.call.call_args[1]["message_sequence"][0]["content"]
        assert "Good profile" in user_content
        # Failed chunk should not appear
        assert "Chunk 1:" not in user_content


class TestInputValidation:
    """Tests for input token validation."""

    @pytest.mark.asyncio
    async def test_unified_profiling_rejects_oversized_input(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that oversized input is rejected for unified profiling."""
        mock_settings.profiling_max_input_tokens = 100

        # Create input that exceeds token limit
        large_text = "word " * 500

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        unified, result = await profiling_service.profile_document_unified(
            document_text=large_text,
            chunks=chunks,
        )

        assert result.success is False
        assert "exceeds" in result.error_message
        assert unified is None
        mock_side_call_service.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_chunk_profiling_rejects_oversized_batch(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that oversized chunk batch is rejected."""
        mock_settings.profiling_max_input_tokens = 100

        large_content = "word " * 500
        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content=large_content)]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        assert len(results) == 1
        assert results[0].success is False
        assert "too large" in results[0].error.lower() or "exceeds" in results[0].error.lower()
        assert tokens_used == 0
