"""
Unit tests for ProfilingService (SHU-343, SHU-581, SHU-582).

Tests the pure LLM logic for document and chunk profiling, mocking the SideCallService.
SHU-581 added unified profiling for small documents.
SHU-582 added incremental profiling for large documents.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.schemas.profiling import (
    ChunkData,
    DocumentType,
)
from shu.services.profiling_service import (
    CHUNK_PROFILE_SYSTEM_PROMPT,
    ProfilingService,
)
from shu.services.side_call_service import SideCallResult


@pytest.fixture
def mock_settings():
    """Mock settings with profiling configuration."""
    settings = MagicMock()
    settings.profiling_timeout_seconds = 60
    settings.query_synthesis_timeout_seconds = 90
    settings.chunk_profiling_batch_size = 5
    settings.profiling_full_doc_max_tokens = 4000
    settings.profiling_max_input_tokens = 8000
    settings.query_synthesis_min_queries = 3
    settings.query_synthesis_max_queries = 20
    settings.enable_query_synthesis = True  # Explicit default for tests
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
        # Verify prompt uses configured query limits
        assert "3-20 queries" in call_kwargs["system_prompt"]
        assert "document profiling assistant" in call_kwargs["system_prompt"]

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
            chunks=chunks,
        )

        assert unified is not None
        assert unified.synthesized_queries == ["What is X?", "How does Y work?"]


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


class TestInputValidation:
    """Tests for input token validation."""

    @pytest.mark.asyncio
    async def test_unified_profiling_rejects_oversized_input(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that oversized input is rejected for unified profiling."""
        mock_settings.profiling_max_input_tokens = 100

        # Create chunks with content that exceeds token limit
        large_content = "word " * 500
        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content=large_content)]
        unified, result = await profiling_service.profile_document_unified(
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


class TestIncrementalProfiling:
    """Tests for profile_chunks_incremental method (SHU-582).

    This method eliminates the separate aggregation LLM call by having
    the final batch generate document-level metadata from accumulated one-liners.
    """

    @pytest.mark.asyncio
    async def test_incremental_profiling_single_batch(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test incremental profiling with a single batch (becomes final batch)."""
        mock_settings.chunk_profiling_batch_size = 10  # All chunks fit in one batch

        llm_response = json.dumps({
            "chunks": [
                {
                    "index": 0,
                    "one_liner": "Explains OAuth2 flow",
                    "summary": "OAuth2 authorization code flow details",
                    "keywords": ["OAuth2", "PKCE"],
                    "topics": ["authentication"],
                },
                {
                    "index": 1,
                    "one_liner": "Covers token refresh",
                    "summary": "Token refresh and rotation patterns",
                    "keywords": ["refresh_token"],
                    "topics": ["token_management"],
                },
            ],
            "synopsis": "Technical guide covering OAuth2 authentication.",
            "document_type": "technical",
            "capability_manifest": {
                "answers_questions_about": ["OAuth2", "authentication"],
                "provides_information_type": ["instructions"],
                "authority_level": "primary",
                "completeness": "complete",
                "question_domains": ["how", "what"],
            },
            "synthesized_queries": [
                "How does OAuth2 PKCE flow work?",
                "What is token refresh rotation?",
            ],
        })
        mock_side_call_service.call.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=300
        )

        chunks = [
            ChunkData(chunk_id="c1", chunk_index=0, content="OAuth2 content"),
            ChunkData(chunk_id="c2", chunk_index=1, content="Token content"),
        ]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(
                chunks=chunks,
                document_metadata={"title": "Auth Guide"},
            )
        )

        assert len(chunk_results) == 2
        assert chunk_results[0].success is True
        assert chunk_results[0].profile.one_liner == "Explains OAuth2 flow"
        assert chunk_results[1].profile.one_liner == "Covers token refresh"

        assert doc_profile is not None
        assert doc_profile.synopsis == "Technical guide covering OAuth2 authentication."
        assert doc_profile.document_type == DocumentType.TECHNICAL

        assert len(queries) == 2
        assert "OAuth2 PKCE" in queries[0]

        assert tokens == 300
        # Only one LLM call (final batch)
        assert mock_side_call_service.call.call_count == 1

    @pytest.mark.asyncio
    async def test_incremental_profiling_multiple_batches(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test incremental profiling with multiple batches."""
        mock_settings.chunk_profiling_batch_size = 2

        # First batch response (regular batch)
        batch1_response = json.dumps([
            {
                "one_liner": "Batch 1 chunk 0",
                "summary": "Summary 0",
                "keywords": ["k0"],
                "topics": ["t0"],
            },
            {
                "one_liner": "Batch 1 chunk 1",
                "summary": "Summary 1",
                "keywords": ["k1"],
                "topics": ["t1"],
            },
        ])

        # Final batch response (includes doc metadata)
        final_batch_response = json.dumps({
            "chunks": [
                {
                    "index": 2,
                    "one_liner": "Final batch chunk",
                    "summary": "Summary 2",
                    "keywords": ["k2"],
                    "topics": ["t2"],
                },
            ],
            "synopsis": "Synopsis from accumulated one-liners.",
            "document_type": "narrative",
            "capability_manifest": {
                "answers_questions_about": ["topic1", "topic2"],
            },
            "synthesized_queries": ["Query 1", "Query 2"],
        })

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content=final_batch_response, success=True, tokens_used=200),
        ]

        chunks = [
            ChunkData(chunk_id="c0", chunk_index=0, content="Content 0"),
            ChunkData(chunk_id="c1", chunk_index=1, content="Content 1"),
            ChunkData(chunk_id="c2", chunk_index=2, content="Content 2"),
        ]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=chunks)
        )

        assert len(chunk_results) == 3
        assert chunk_results[0].profile.one_liner == "Batch 1 chunk 0"
        assert chunk_results[1].profile.one_liner == "Batch 1 chunk 1"
        assert chunk_results[2].profile.one_liner == "Final batch chunk"

        assert doc_profile is not None
        assert doc_profile.synopsis == "Synopsis from accumulated one-liners."

        assert len(queries) == 2
        assert tokens == 300  # 100 + 200
        assert mock_side_call_service.call.call_count == 2

    @pytest.mark.asyncio
    async def test_incremental_profiling_accumulates_one_liners(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that one-liners are accumulated and passed to final batch."""
        mock_settings.chunk_profiling_batch_size = 2

        batch1_response = json.dumps([
            {"one_liner": "First one-liner", "summary": "S1", "keywords": [], "topics": []},
            {"one_liner": "Second one-liner", "summary": "S2", "keywords": [], "topics": []},
        ])

        final_batch_response = json.dumps({
            "chunks": [
                {"index": 2, "one_liner": "Third", "summary": "S3", "keywords": [], "topics": []},
            ],
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": [],
        })

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content=final_batch_response, success=True, tokens_used=100),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(3)
        ]

        await profiling_service.profile_chunks_incremental(chunks=chunks)

        # Check that final batch call includes accumulated one-liners
        final_call = mock_side_call_service.call.call_args_list[1]
        user_content = final_call[1]["message_sequence"][0]["content"]
        assert "First one-liner" in user_content
        assert "Second one-liner" in user_content
        assert "Chunk 0:" in user_content
        assert "Chunk 1:" in user_content

    @pytest.mark.asyncio
    async def test_incremental_profiling_empty_chunks(
        self, profiling_service, mock_side_call_service
    ):
        """Test incremental profiling with empty chunk list."""
        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=[])
        )

        assert chunk_results == []
        assert doc_profile is None
        assert queries == []
        assert tokens == 0
        mock_side_call_service.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_profiling_final_batch_failure(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test handling when final batch LLM call fails."""
        mock_settings.chunk_profiling_batch_size = 2

        batch1_response = json.dumps([
            {"one_liner": "One-liner 0", "summary": "S0", "keywords": [], "topics": []},
            {"one_liner": "One-liner 1", "summary": "S1", "keywords": [], "topics": []},
        ])

        mock_side_call_service.call.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content="", success=False, error_message="Rate limited"),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(3)
        ]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=chunks)
        )

        # First batch succeeded
        assert chunk_results[0].success is True
        assert chunk_results[1].success is True
        # Final batch failed
        assert chunk_results[2].success is False
        assert "Rate limited" in chunk_results[2].error

        # No document profile when final batch fails
        assert doc_profile is None
        assert queries == []

    @pytest.mark.asyncio
    async def test_incremental_profiling_parse_failure(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test handling when final batch response cannot be parsed."""
        mock_settings.chunk_profiling_batch_size = 10

        # Invalid JSON response
        mock_side_call_service.call.return_value = SideCallResult(
            content="not valid json", success=True, tokens_used=100
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=chunks)
        )

        assert len(chunk_results) == 1
        assert chunk_results[0].success is False
        assert doc_profile is None
        assert queries == []

    @pytest.mark.asyncio
    async def test_incremental_profiling_regular_batch_failure(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test handling when a regular (non-final) batch fails.

        Failed batches should not contribute one-liners to the accumulated context,
        but processing should continue to the final batch.
        """
        mock_settings.chunk_profiling_batch_size = 2

        # First batch fails
        # Final batch succeeds
        final_batch_response = json.dumps({
            "chunks": [
                {"index": 2, "one_liner": "Final chunk", "summary": "S2", "keywords": [], "topics": []},
            ],
            "synopsis": "Synopsis from limited context.",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": ["Query 1"],
        })

        mock_side_call_service.call.side_effect = [
            SideCallResult(content="", success=False, error_message="Timeout"),
            SideCallResult(content=final_batch_response, success=True, tokens_used=200),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(3)
        ]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=chunks)
        )

        # First batch failed
        assert chunk_results[0].success is False
        assert chunk_results[1].success is False
        assert "Timeout" in chunk_results[0].error

        # Final batch succeeded
        assert chunk_results[2].success is True
        assert chunk_results[2].profile.one_liner == "Final chunk"

        # Document profile should still be generated (from limited context)
        assert doc_profile is not None
        assert doc_profile.synopsis == "Synopsis from limited context."
        assert len(queries) == 1


class TestFinalBatchPrompt:
    """Tests for final batch prompt construction."""

    @pytest.mark.asyncio
    async def test_final_batch_uses_correct_prompt(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that final batch uses dynamically built prompt with configured query limits."""
        mock_settings.chunk_profiling_batch_size = 10

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "synopsis": "Test",
                "document_type": "narrative",
                "capability_manifest": {},
                "synthesized_queries": [],
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await profiling_service.profile_chunks_incremental(chunks=chunks)

        call_kwargs = mock_side_call_service.call.call_args[1]
        # Verify prompt uses configured query limits (min=3, max=20 from mock_settings)
        assert "3-20 queries" in call_kwargs["system_prompt"]
        assert "FINAL batch" in call_kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_final_batch_includes_document_metadata(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that document metadata is included in final batch prompt."""
        mock_settings.chunk_profiling_batch_size = 10

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "synopsis": "Test",
                "document_type": "narrative",
                "capability_manifest": {},
                "synthesized_queries": [],
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await profiling_service.profile_chunks_incremental(
            chunks=chunks,
            document_metadata={"title": "My Document", "source": "email"},
        )

        user_content = mock_side_call_service.call.call_args[1]["message_sequence"][0]["content"]
        assert "My Document" in user_content
        assert "email" in user_content


class TestQuerySynthesisToggle:
    """Tests for enable_query_synthesis controlling prompt content."""

    @pytest.mark.asyncio
    async def test_unified_prompt_excludes_queries_when_disabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=False, unified prompt should NOT ask for queries."""
        mock_settings.enable_query_synthesis = False
        service = ProfilingService(mock_side_call_service, mock_settings)

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "synopsis": "Test",
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "document_type": "narrative",
                "capability_manifest": {},
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_document_unified(chunks=chunks)

        system_prompt = mock_side_call_service.call.call_args[1]["system_prompt"]
        assert "synthesized_queries" not in system_prompt
        assert "PURPOSE: These queries" not in system_prompt

    @pytest.mark.asyncio
    async def test_unified_prompt_includes_queries_when_enabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=True, unified prompt SHOULD ask for queries."""
        mock_settings.enable_query_synthesis = True
        service = ProfilingService(mock_side_call_service, mock_settings)

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "synopsis": "Test",
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "document_type": "narrative",
                "capability_manifest": {},
                "synthesized_queries": ["Query 1"],
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_document_unified(chunks=chunks)

        system_prompt = mock_side_call_service.call.call_args[1]["system_prompt"]
        assert "synthesized_queries" in system_prompt
        assert "3-20 queries" in system_prompt

    @pytest.mark.asyncio
    async def test_final_batch_prompt_excludes_queries_when_disabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=False, final batch prompt should NOT ask for queries."""
        mock_settings.enable_query_synthesis = False
        mock_settings.chunk_profiling_batch_size = 10
        service = ProfilingService(mock_side_call_service, mock_settings)

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "synopsis": "Test",
                "document_type": "narrative",
                "capability_manifest": {},
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_chunks_incremental(chunks=chunks)

        call_kwargs = mock_side_call_service.call.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        user_content = call_kwargs["message_sequence"][0]["content"]

        # System prompt should not mention queries
        assert "synthesized_queries" not in system_prompt
        # User content should not ask for queries
        assert "synthesized_queries" not in user_content

    @pytest.mark.asyncio
    async def test_final_batch_prompt_includes_queries_when_enabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=True, final batch prompt SHOULD ask for queries."""
        mock_settings.enable_query_synthesis = True
        mock_settings.chunk_profiling_batch_size = 10
        service = ProfilingService(mock_side_call_service, mock_settings)

        mock_side_call_service.call.return_value = SideCallResult(
            content=json.dumps({
                "chunks": [{"index": 0, "one_liner": "Test", "summary": "S", "keywords": [], "topics": []}],
                "synopsis": "Test",
                "document_type": "narrative",
                "capability_manifest": {},
                "synthesized_queries": ["Query 1"],
            }),
            success=True,
            tokens_used=100,
        )

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_chunks_incremental(chunks=chunks)

        call_kwargs = mock_side_call_service.call.call_args[1]
        system_prompt = call_kwargs["system_prompt"]
        user_content = call_kwargs["message_sequence"][0]["content"]

        # System prompt should mention queries
        assert "synthesized_queries" in system_prompt
        assert "3-20 queries" in system_prompt
        # User content should ask for queries
        assert "synthesized_queries" in user_content
