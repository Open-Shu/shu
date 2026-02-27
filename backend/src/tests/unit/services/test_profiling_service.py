"""
Unit tests for ProfilingService (SHU-343, SHU-582, SHU-589).

Tests the pure LLM logic for document and chunk profiling, mocking the SideCallService.
SHU-582 added incremental profiling for large documents.
SHU-589 removed unified profiling, consolidating on incremental profiling only.
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
    settings.profiling_timeout_seconds = 180
    settings.query_synthesis_timeout_seconds = 240
    settings.chunk_profiling_batch_size = 5
    settings.profiling_max_input_tokens = 8000
    settings.query_synthesis_min_queries = 3
    settings.query_synthesis_max_queries = 20
    settings.enable_query_synthesis = True  # Explicit default for tests
    return settings


@pytest.fixture
def mock_side_call_service():
    """Mock SideCallService for unit testing.

    Mocks call_for_profiling which is the dedicated profiling method.
    """
    mock = AsyncMock()
    # Ensure call_for_profiling is properly mocked
    mock.call_for_profiling = AsyncMock()
    return mock


@pytest.fixture
def profiling_service(mock_side_call_service, mock_settings):
    """Create a ProfilingService with mocked dependencies."""
    return ProfilingService(mock_side_call_service, mock_settings)


class TestChunkProfiling:
    """Tests for profile_chunks method."""

    @pytest.mark.asyncio
    async def test_profile_chunks_success(self, profiling_service, mock_side_call_service):
        """Test successful chunk profiling with summary."""
        llm_response = json.dumps([
            {
                "summary": "Covers user authentication",
                "keywords": ["user", "auth"],
                "topics": ["security"],
            },
            {
                "summary": "Explains API endpoints",
                "keywords": ["REST", "HTTP"],
                "topics": ["integration"],
            },
        ])
        mock_side_call_service.call_for_profiling.return_value = SideCallResult(
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
        assert results[0].profile.summary == "Covers user authentication"
        assert "user" in results[0].profile.keywords
        assert results[1].success is True
        assert results[1].profile.summary == "Explains API endpoints"
        mock_side_call_service.call_for_profiling.assert_called_once()
        call_kwargs = mock_side_call_service.call_for_profiling.call_args[1]
        assert call_kwargs["system_prompt"] == CHUNK_PROFILE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_profile_chunks_empty_list(self, profiling_service, mock_side_call_service):
        """Test profiling empty chunk list."""
        results, tokens_used = await profiling_service.profile_chunks([])
        assert results == []
        assert tokens_used == 0
        mock_side_call_service.call_for_profiling.assert_not_called()

    @pytest.mark.asyncio
    async def test_profile_chunks_batching(self, profiling_service, mock_side_call_service, mock_settings):
        """Test that chunks are processed in batches."""
        mock_settings.chunk_profiling_batch_size = 2

        def make_response(call_count):
            profiles = [
                {"summary": f"Profile {i}", "keywords": [], "topics": []}
                for i in range(2)
            ]
            return SideCallResult(content=json.dumps(profiles), success=True, tokens_used=100)

        mock_side_call_service.call_for_profiling.side_effect = [
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
        assert mock_side_call_service.call_for_profiling.call_count == 3
        assert len(results) == 5
        assert tokens_used == 300  # 100 per batch * 3 batches

    @pytest.mark.asyncio
    async def test_profile_chunks_llm_failure(self, profiling_service, mock_side_call_service):
        """Test handling of LLM failure during chunk profiling."""
        mock_side_call_service.call_for_profiling.return_value = SideCallResult(
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
    async def test_profile_chunks_truncates_long_lists(self, profiling_service, mock_side_call_service):
        """Test that excessively long keywords/topics lists are truncated."""
        long_summary = "y" * 1000
        long_keywords = [f"kw{i}" for i in range(50)]
        llm_response = json.dumps([{
            "summary": long_summary,
            "keywords": long_keywords,
            "topics": [f"t{i}" for i in range(20)],
        }])
        mock_side_call_service.call_for_profiling.return_value = SideCallResult(
            content=llm_response, success=True, tokens_used=500
        )

        chunks = [ChunkData(chunk_id="c1", chunk_index=0, content="Content")]
        results, _ = await profiling_service.profile_chunks(chunks)

        assert len(results[0].profile.summary) <= 500
        assert len(results[0].profile.keywords) <= 15
        assert len(results[0].profile.topics) <= 10


class TestInputValidation:
    """Tests for input token validation."""

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
    """Tests for profile_chunks_incremental method (SHU-582, SHU-594).

    SHU-594 separated chunk profiling from document metadata generation:
    - All chunks are profiled uniformly using _profile_chunk_batch()
    - Document metadata is generated in a separate LLM call using _generate_document_metadata()
    """

    @pytest.mark.asyncio
    async def test_incremental_profiling_single_batch(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test incremental profiling with a single batch."""
        mock_settings.chunk_profiling_batch_size = 10  # All chunks fit in one batch

        # First call: chunk profiling
        chunk_response = json.dumps([
            {
                "summary": "Explains OAuth2 flow",
                "keywords": ["OAuth2", "PKCE"],
                "topics": ["authentication"],
            },
            {
                "summary": "Covers token refresh",
                "keywords": ["refresh_token"],
                "topics": ["token_management"],
            },
        ])

        # Second call: document metadata generation
        metadata_response = json.dumps({
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

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=chunk_response, success=True, tokens_used=200),
            SideCallResult(content=metadata_response, success=True, tokens_used=100),
        ]

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
        assert chunk_results[0].profile.summary == "Explains OAuth2 flow"
        assert chunk_results[1].profile.summary == "Covers token refresh"

        assert doc_profile is not None
        assert doc_profile.synopsis == "Technical guide covering OAuth2 authentication."
        assert doc_profile.document_type == DocumentType.TECHNICAL

        assert len(queries) == 2
        assert "OAuth2 PKCE" in queries[0]

        assert tokens == 300  # 200 for chunks + 100 for metadata
        # Two LLM calls: one for chunks, one for metadata
        assert mock_side_call_service.call_for_profiling.call_count == 2

    @pytest.mark.asyncio
    async def test_incremental_profiling_multiple_batches(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test incremental profiling with multiple batches."""
        mock_settings.chunk_profiling_batch_size = 2

        # First batch response (chunks 0-1)
        batch1_response = json.dumps([
            {
                "summary": "Batch 1 chunk 0",
                "keywords": ["k0"],
                "topics": ["t0"],
            },
            {
                "summary": "Batch 1 chunk 1",
                "keywords": ["k1"],
                "topics": ["t1"],
            },
        ])

        # Second batch response (chunk 2)
        batch2_response = json.dumps([
            {
                "summary": "Batch 2 chunk",
                "keywords": ["k2"],
                "topics": ["t2"],
            },
        ])

        # Third call: document metadata generation
        metadata_response = json.dumps({
            "synopsis": "Synopsis from accumulated summaries.",
            "document_type": "narrative",
            "capability_manifest": {
                "answers_questions_about": ["topic1", "topic2"],
            },
            "synthesized_queries": ["Query 1", "Query 2"],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content=batch2_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=150),
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
        assert chunk_results[0].profile.summary == "Batch 1 chunk 0"
        assert chunk_results[1].profile.summary == "Batch 1 chunk 1"
        assert chunk_results[2].profile.summary == "Batch 2 chunk"

        assert doc_profile is not None
        assert doc_profile.synopsis == "Synopsis from accumulated summaries."

        assert len(queries) == 2
        assert tokens == 300  # 100 + 50 + 150
        # Three LLM calls: two chunk batches + metadata
        assert mock_side_call_service.call_for_profiling.call_count == 3

    @pytest.mark.asyncio
    async def test_incremental_profiling_accumulates_summaries(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that summaries are accumulated and passed to document metadata generation."""
        mock_settings.chunk_profiling_batch_size = 2

        batch1_response = json.dumps([
            {"summary": "First summary", "keywords": [], "topics": []},
            {"summary": "Second summary", "keywords": [], "topics": []},
        ])

        batch2_response = json.dumps([
            {"summary": "Third summary", "keywords": [], "topics": []},
        ])

        metadata_response = json.dumps({
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": [],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content=batch2_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=50),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(3)
        ]

        await profiling_service.profile_chunks_incremental(chunks=chunks)

        # Check that metadata generation call includes all accumulated summaries
        metadata_call = mock_side_call_service.call_for_profiling.call_args_list[2]
        user_content = metadata_call[1]["message_sequence"][0]["content"]
        assert "First summary" in user_content
        assert "Second summary" in user_content
        assert "Third summary" in user_content
        assert "Chunk 0:" in user_content
        assert "Chunk 1:" in user_content
        assert "Chunk 2:" in user_content

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
        mock_side_call_service.call_for_profiling.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_profiling_metadata_generation_failure(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test handling when document metadata generation fails.

        All chunk profiling succeeds, but the separate metadata generation call fails.
        Chunks should still have their profiles, but document profile should be None.
        """
        mock_settings.chunk_profiling_batch_size = 2

        batch1_response = json.dumps([
            {"summary": "Summary 0", "keywords": [], "topics": []},
            {"summary": "Summary 1", "keywords": [], "topics": []},
        ])

        batch2_response = json.dumps([
            {"summary": "Summary 2", "keywords": [], "topics": []},
        ])

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=batch1_response, success=True, tokens_used=100),
            SideCallResult(content=batch2_response, success=True, tokens_used=50),
            SideCallResult(content="", success=False, error_message="Rate limited"),
        ]

        chunks = [
            ChunkData(chunk_id=f"c{i}", chunk_index=i, content=f"Content {i}")
            for i in range(3)
        ]

        chunk_results, doc_profile, queries, tokens = (
            await profiling_service.profile_chunks_incremental(chunks=chunks)
        )

        # All chunk profiling succeeded
        assert chunk_results[0].success is True
        assert chunk_results[1].success is True
        assert chunk_results[2].success is True
        assert chunk_results[0].profile.summary == "Summary 0"
        assert chunk_results[2].profile.summary == "Summary 2"

        # Document metadata generation failed
        assert doc_profile is None
        assert queries == []
        # Tokens from successful chunk batches still counted
        assert tokens == 150  # 100 + 50, metadata call returned 0

    @pytest.mark.asyncio
    async def test_incremental_profiling_parse_failure(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test handling when final batch response cannot be parsed."""
        mock_settings.chunk_profiling_batch_size = 10

        # Invalid JSON response
        mock_side_call_service.call_for_profiling.return_value = SideCallResult(
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
        """Test handling when a chunk batch fails.

        Failed batches should not contribute summaries to the accumulated context,
        but processing should continue. Document metadata generation should still
        proceed with whatever summaries were accumulated.
        """
        mock_settings.chunk_profiling_batch_size = 2

        # First batch fails
        # Second batch succeeds
        batch2_response = json.dumps([
            {"summary": "Second batch chunk", "keywords": [], "topics": []},
        ])

        # Metadata generation succeeds with limited context
        metadata_response = json.dumps({
            "synopsis": "Synopsis from limited context.",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": ["Query 1"],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content="", success=False, error_message="Timeout"),
            SideCallResult(content=batch2_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=100),
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

        # Second batch succeeded
        assert chunk_results[2].success is True
        assert chunk_results[2].profile.summary == "Second batch chunk"

        # Document profile should still be generated (from limited context)
        assert doc_profile is not None
        assert doc_profile.synopsis == "Synopsis from limited context."
        assert len(queries) == 1


class TestDocumentMetadataPrompt:
    """Tests for document metadata prompt construction (SHU-594)."""

    @pytest.mark.asyncio
    async def test_metadata_generation_uses_correct_prompt(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that metadata generation uses dynamically built prompt with configured query limits."""
        mock_settings.chunk_profiling_batch_size = 10

        chunk_response = json.dumps([
            {"summary": "Test chunk", "keywords": [], "topics": []},
        ])

        metadata_response = json.dumps({
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": [],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=chunk_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=50),
        ]

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await profiling_service.profile_chunks_incremental(chunks=chunks)

        # Check the second call (metadata generation)
        metadata_call_kwargs = mock_side_call_service.call_for_profiling.call_args_list[1][1]
        # Verify prompt uses configured query limits (min=3, max=20 from mock_settings)
        assert "3-20 queries" in metadata_call_kwargs["system_prompt"]
        # Should be focused on synthesis, not FINAL batch
        assert "synthesizing" in metadata_call_kwargs["system_prompt"].lower()

    @pytest.mark.asyncio
    async def test_metadata_generation_includes_document_metadata(
        self, profiling_service, mock_side_call_service, mock_settings
    ):
        """Test that document metadata is included in metadata generation prompt."""
        mock_settings.chunk_profiling_batch_size = 10

        chunk_response = json.dumps([
            {"summary": "Test chunk", "keywords": [], "topics": []},
        ])

        metadata_response = json.dumps({
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": [],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=chunk_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=50),
        ]

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await profiling_service.profile_chunks_incremental(
            chunks=chunks,
            document_metadata={"title": "My Document", "source": "email"},
        )

        # Check the second call (metadata generation)
        metadata_call = mock_side_call_service.call_for_profiling.call_args_list[1]
        user_content = metadata_call[1]["message_sequence"][0]["content"]
        assert "My Document" in user_content
        assert "email" in user_content


class TestQuerySynthesisToggle:
    """Tests for enable_query_synthesis controlling prompt content."""

    @pytest.mark.asyncio
    async def test_metadata_prompt_excludes_queries_when_disabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=False, metadata prompt should NOT ask for queries."""
        mock_settings.enable_query_synthesis = False
        mock_settings.chunk_profiling_batch_size = 10
        service = ProfilingService(mock_side_call_service, mock_settings)

        chunk_response = json.dumps([
            {"summary": "Test chunk", "keywords": [], "topics": []},
        ])

        metadata_response = json.dumps({
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=chunk_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=50),
        ]

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_chunks_incremental(chunks=chunks)

        # Check the metadata generation call (second call)
        metadata_call_kwargs = mock_side_call_service.call_for_profiling.call_args_list[1][1]
        system_prompt = metadata_call_kwargs["system_prompt"]
        user_content = metadata_call_kwargs["message_sequence"][0]["content"]

        # System prompt should not mention queries
        assert "synthesized_queries" not in system_prompt
        # User content should not ask for queries
        assert "synthesized_queries" not in user_content

    @pytest.mark.asyncio
    async def test_metadata_prompt_includes_queries_when_enabled(
        self, mock_side_call_service, mock_settings
    ):
        """When enable_query_synthesis=True, metadata prompt SHOULD ask for queries."""
        mock_settings.enable_query_synthesis = True
        mock_settings.chunk_profiling_batch_size = 10
        service = ProfilingService(mock_side_call_service, mock_settings)

        chunk_response = json.dumps([
            {"summary": "Test chunk", "keywords": [], "topics": []},
        ])

        metadata_response = json.dumps({
            "synopsis": "Test",
            "document_type": "narrative",
            "capability_manifest": {},
            "synthesized_queries": ["Query 1"],
        })

        mock_side_call_service.call_for_profiling.side_effect = [
            SideCallResult(content=chunk_response, success=True, tokens_used=50),
            SideCallResult(content=metadata_response, success=True, tokens_used=50),
        ]

        chunks = [ChunkData(chunk_id="c0", chunk_index=0, content="Content")]
        await service.profile_chunks_incremental(chunks=chunks)

        # Check the metadata generation call (second call)
        metadata_call_kwargs = mock_side_call_service.call_for_profiling.call_args_list[1][1]
        system_prompt = metadata_call_kwargs["system_prompt"]
        user_content = metadata_call_kwargs["message_sequence"][0]["content"]

        # System prompt should mention queries
        assert "synthesized_queries" in system_prompt
        assert "3-20 queries" in system_prompt
        # User content should ask for queries
        assert "synthesized_queries" in user_content


class TestProfileParserNullHandling:
    """Tests for ProfileParser handling of null values from LLM responses (SHU-586)."""

    @pytest.mark.asyncio
    async def test_chunk_profiles_handles_null_values(self, profiling_service, mock_side_call_service):
        """Test that chunk profiling handles null values in LLM response gracefully."""
        # LLM returns explicit null values instead of missing keys
        llm_response = json.dumps([
            {
                "index": 0,
                "summary": None,
                "keywords": None,
                "topics": None,
            },
            {
                "index": 1,
                "summary": "Valid summary",
                "keywords": ["keyword1"],
                "topics": ["topic1"],
            },
        ])

        mock_side_call_service.call_for_profiling.return_value = SideCallResult(
            content=llm_response,
            success=True,
            tokens_used=100,
        )

        chunks = [
            ChunkData(chunk_id="c0", chunk_index=0, content="Content 0"),
            ChunkData(chunk_id="c1", chunk_index=1, content="Content 1"),
        ]

        results, _ = await profiling_service.profile_chunks(chunks=chunks)

        # Both chunks should succeed - null values coerced to empty strings/lists
        assert len(results) == 2
        assert all(r.success for r in results)

        # First chunk should have empty values
        assert results[0].profile.summary == ""
        assert results[0].profile.keywords == []
        assert results[0].profile.topics == []

        # Second chunk should have actual values
        assert results[1].profile.summary == "Valid summary"
        assert results[1].profile.keywords == ["keyword1"]
        assert results[1].profile.topics == ["topic1"]
