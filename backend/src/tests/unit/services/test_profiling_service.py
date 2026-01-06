"""
Unit tests for ProfilingService (SHU-343).

Tests the pure LLM logic for document and chunk profiling, mocking the SideCallService.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from shu.services.profiling_service import (
    ProfilingService,
    DOCUMENT_PROFILE_SYSTEM_PROMPT,
    CHUNK_PROFILE_SYSTEM_PROMPT,
    AGGREGATE_PROFILE_SYSTEM_PROMPT,
)
from shu.services.side_call_service import SideCallResult
from shu.schemas.profiling import (
    ChunkData,
    ChunkProfile,
    DocumentType,
)


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


class TestDocumentProfiling:
    """Tests for profile_document method."""

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
            }
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
        call_kwargs = mock_side_call_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == DOCUMENT_PROFILE_SYSTEM_PROMPT

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
        """Test successful chunk profiling."""
        llm_response = json.dumps([
            {"summary": "First chunk about users", "keywords": ["user", "auth"], "topics": ["security"]},
            {"summary": "Second chunk about APIs", "keywords": ["REST", "HTTP"], "topics": ["integration"]},
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
        assert results[0].profile.summary == "First chunk about users"
        assert "user" in results[0].profile.keywords
        assert results[1].success is True
        assert results[1].chunk_id == "c2"

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

        # Mock returns profiles for batch
        def make_response(call_count):
            profiles = [{"summary": f"Profile {i}", "keywords": [], "topics": []}
                       for i in range(2)]
            return SideCallResult(content=json.dumps(profiles), success=True, tokens_used=100)

        mock_side_call_service.call.side_effect = [
            make_response(0), make_response(1), make_response(2)
        ]

        chunks = [ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
                  for i in range(5)]
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
        assert tokens_used == 0  # No tokens used on failure

    @pytest.mark.asyncio
    async def test_profile_chunks_truncates_long_data(self, profiling_service, mock_side_call_service):
        """Test that excessively long profile data is truncated."""
        long_summary = "x" * 1000
        long_keywords = [f"kw{i}" for i in range(50)]
        llm_response = json.dumps([{
            "summary": long_summary,
            "keywords": long_keywords,
            "topics": [f"t{i}" for i in range(20)],
        }])
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=500
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        results, tokens_used = await profiling_service.profile_chunks(chunks)

        assert len(results[0].profile.summary) <= 500
        assert len(results[0].profile.keywords) <= 15
        assert len(results[0].profile.topics) <= 10
        assert tokens_used == 500


class TestAggregateProfiles:
    """Tests for aggregate_chunk_profiles method."""

    @pytest.mark.asyncio
    async def test_aggregate_profiles_success(self, profiling_service, mock_side_call_service):
        """Test successful aggregation of chunk profiles."""
        from shu.schemas.profiling import ChunkProfileResult

        llm_response = json.dumps({
            "synopsis": "Aggregated document about APIs and security.",
            "document_type": "technical",
            "capability_manifest": {
                "answers_questions_about": ["APIs", "security"],
                "provides_information_type": ["instructions"],
                "authority_level": "primary",
                "completeness": "complete",
                "question_domains": ["how"],
            }
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=300
        )

        chunk_profiles = [
            ChunkProfileResult(
                chunk_id="c1", chunk_index=0,
                profile=ChunkProfile(summary="About APIs", keywords=["REST"], topics=["integration"]),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2", chunk_index=1,
                profile=ChunkProfile(summary="About security", keywords=["auth"], topics=["security"]),
                success=True,
            ),
        ]

        profile, result = await profiling_service.aggregate_chunk_profiles(chunk_profiles)

        assert result.success is True
        assert profile is not None
        assert "APIs" in profile.synopsis or "Aggregated" in profile.synopsis
        call_kwargs = mock_side_call_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == AGGREGATE_PROFILE_SYSTEM_PROMPT
        # Check that chunk summaries are included in the request
        user_content = call_kwargs["message_sequence"][0]["content"]
        assert "About APIs" in user_content
        assert "About security" in user_content

    @pytest.mark.asyncio
    async def test_aggregate_ignores_failed_chunks(self, profiling_service, mock_side_call_service):
        """Test that failed chunk profiles are excluded from aggregation."""
        from shu.schemas.profiling import ChunkProfileResult

        mock_side_call_service.call.return_value = SideCallResult(
            content='{"synopsis":"Test","document_type":"narrative","capability_manifest":{}}',
            success=True,
            tokens_used=100,
        )

        chunk_profiles = [
            ChunkProfileResult(
                chunk_id="c1", chunk_index=0,
                profile=ChunkProfile(summary="Good profile", keywords=["key"], topics=["topic"]),
                success=True,
            ),
            ChunkProfileResult(
                chunk_id="c2", chunk_index=1,
                profile=ChunkProfile(summary="", keywords=[], topics=[]),
                success=False,
                error="Failed to profile",
            ),
        ]

        await profiling_service.aggregate_chunk_profiles(chunk_profiles)

        user_content = mock_side_call_service.call.call_args[1]["message_sequence"][0]["content"]
        assert "Good profile" in user_content
        # Failed chunk summary should not appear (empty string)
        assert "Chunk 1:" not in user_content  # Only successful chunks included

