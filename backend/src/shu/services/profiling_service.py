"""Document and Chunk Profiling Service.

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate unified profiles (synopsis, one-liners, chunk profiles, queries) for small docs
- Generate chunk profiles (one-liner, summary, keywords, topics) for batch processing
- Aggregate chunk profiles for large documents
- Enforce profiling_max_input_tokens limit on all LLM calls
"""

import json

import structlog

from ..core.config import Settings
from ..schemas.profiling import (
    ChunkData,
    ChunkProfileResult,
    DocumentProfile,
    UnifiedProfilingResponse,
)
from ..utils.tokenization import estimate_tokens
from .profile_parser import ProfileParser
from .side_call_service import SideCallResult, SideCallService

logger = structlog.get_logger(__name__)


# Unified profiling prompt for small documents - generates everything in one pass
UNIFIED_PROFILING_SYSTEM_PROMPT = """You are a document profiling assistant. Analyze the document and generate a complete profile including document-level metadata, per-chunk summaries, and hypothetical queries.

Generate a JSON response with this exact structure:
{
    "synopsis": "A 2-4 sentence summary capturing the document's essence, main topics, and purpose.",
    "chunks": [
        {
            "index": 0,
            "one_liner": "Condensed summary (~50-80 chars) for quick scanning",
            "summary": "Longer description of what this chunk contains",
            "keywords": ["specific", "extractable", "terms"],
            "topics": ["conceptual", "categories"]
        }
    ],
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {
        "answers_questions_about": ["list of specific topics the document addresses"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions"],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    },
    "synthesized_queries": [
        "What is the main topic of this document?",
        "How does X work?",
        "Tell me about Y"
    ]
}

Guidelines:
- one_liner: Start with action verb when possible ("Explains...", "Covers...", "Lists..."). Capture specific information, not just topic.
- synopsis: Capture strategic narrative and cross-chunk themes.
- synthesized_queries: Generate 3-5 diverse queries (questions, commands, keyword searches) that this document can answer.
- keywords: Specific extractable terms (names, numbers, dates, technical terms).
- topics: Broader conceptual categories."""

# Legacy prompt for chunk-only profiling (used in batch processing for large docs)
CHUNK_PROFILE_SYSTEM_PROMPT = """You are a document chunk profiling assistant. Analyze text chunks and generate metadata for retrieval indexing.

For each chunk, generate a JSON response with:
{
    "one_liner": "Condensed summary (~50-80 chars) for quick scanning",
    "summary": "Longer description of what this chunk contains",
    "keywords": ["specific", "extractable", "terms", "names", "numbers", "dates"],
    "topics": ["conceptual", "categories", "themes"]
}

Guidelines:
- one_liner: Start with action verb ("Explains...", "Covers...", "Lists..."). Capture specific information.
- summary: More detailed description for retrieval ranking.
- keywords: Specific extractable terms from the text.
- topics: Broader conceptual categories.
Limit to 5-10 keywords and 3-5 topics."""

# Aggregate profiling prompt for large documents
AGGREGATE_PROFILE_SYSTEM_PROMPT = """You are a document summarization assistant. Given one-liner summaries of document chunks, generate an overall document profile.

Generate a JSON response with:
{
    "synopsis": "A 2-4 sentence summary synthesizing the chunk summaries into a cohesive document overview.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {
        "answers_questions_about": ["consolidated list of topics from all chunks"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions"],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    },
    "synthesized_queries": [
        "What is the main topic?",
        "How does X work?"
    ]
}

Synthesize the chunk information into a coherent whole. Generate 3-5 queries this document can answer."""


class ProfilingService:
    """Service for generating document and chunk profiles via LLM inference."""

    def __init__(
        self,
        side_call_service: SideCallService,
        settings: Settings,
    ) -> None:
        self.side_call = side_call_service
        self.settings = settings
        self.parser = ProfileParser()

    def _resolve_timeout_ms(self, timeout_ms: int | None) -> int:
        """Resolve timeout, using settings default if not provided."""
        return timeout_ms or (self.settings.profiling_timeout_seconds * 1000)

    def _validate_input_tokens(self, content: str, context: str) -> SideCallResult | None:
        """Validate that content does not exceed profiling_max_input_tokens.

        Args:
            content: The content to validate
            context: Description for logging (e.g., "document profiling")

        Returns:
            SideCallResult with error if limit exceeded, None if OK

        """
        max_tokens = self.settings.profiling_max_input_tokens
        token_count = estimate_tokens(content)
        if token_count > max_tokens:
            error_msg = f"Input exceeds profiling_max_input_tokens: {token_count} > {max_tokens}"
            logger.warning(
                "profiling_input_too_large",
                context=context,
                token_count=token_count,
                max_tokens=max_tokens,
            )
            return SideCallResult(
                success=False,
                content="",
                tokens_used=0,
                response_time_ms=0,
                error_message=error_msg,
            )
        return None

    async def profile_document_unified(
        self,
        chunks: list[ChunkData],
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[UnifiedProfilingResponse | None, SideCallResult]:
        """Generate a complete unified profile for a small document.

        This is the primary method for small documents. It generates synopsis,
        per-chunk one-liners and profiles, capability manifest, and synthesized
        queries in a single LLM call.

        Args:
            chunks: List of chunk data with content (contains full document)
            document_metadata: Optional metadata (title, source, etc.)
            timeout_ms: Optional timeout override

        Returns:
            Tuple of (UnifiedProfilingResponse or None if failed, SideCallResult)

        """
        timeout = self._resolve_timeout_ms(timeout_ms)

        # Build the user message with chunk structure only
        # The chunks contain the full document content - no need to duplicate
        chunks_text = []
        for chunk in chunks:
            chunks_text.append(f"[CHUNK {chunk.chunk_index}]\n{chunk.content}\n[/CHUNK]")

        user_content = f"Analyze this document with {len(chunks)} chunks:\n\n"
        user_content += "\n\n".join(chunks_text)

        if document_metadata:
            meta_str = json.dumps(document_metadata, indent=2)
            user_content = f"Document metadata:\n{meta_str}\n\n{user_content}"

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(user_content, "unified profiling")
        if error_result:
            return None, error_result

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=UNIFIED_PROFILING_SYSTEM_PROMPT,
            user_id=None,  # System operation
            timeout_ms=timeout,
        )

        if not result.success:
            logger.warning(
                "unified_profiling_failed",
                error=result.error_message,
            )
            return None, result

        # Parse the unified response
        unified_response = self.parser.parse_unified_response(result.content)
        return unified_response, result

    async def profile_chunks(
        self,
        chunks: list[ChunkData],
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], int]:
        """Generate profiles for multiple chunks.

        Processes chunks in batches for efficiency. Each chunk gets:
        - one_liner: Condensed summary for agent scanning
        - summary: Longer description for retrieval ranking
        - keywords: Specific extractable terms
        - topics: Conceptual categories

        Args:
            chunks: List of chunk data to profile
            timeout_ms: Optional timeout override per batch

        Returns:
            Tuple of (List of ChunkProfileResult, total tokens used)

        """
        if not chunks:
            return [], 0

        timeout = self._resolve_timeout_ms(timeout_ms)
        batch_size = self.settings.chunk_profiling_batch_size
        results: list[ChunkProfileResult] = []
        total_tokens = 0

        # Process in batches
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            batch_results, tokens = await self._profile_chunk_batch(batch, timeout)
            results.extend(batch_results)
            total_tokens += tokens

        return results, total_tokens

    async def _profile_chunk_batch(
        self,
        chunks: list[ChunkData],
        timeout_ms: int,
    ) -> tuple[list[ChunkProfileResult], int]:
        """Profile a batch of chunks in a single LLM call.

        Returns:
            Tuple of (chunk results, tokens used)

        """
        # Build user message with all chunks
        chunks_text = []
        for chunk in chunks:
            chunks_text.append(f"[CHUNK {chunk.chunk_index}]\n{chunk.content}\n[/CHUNK]")

        user_content = (
            f"Profile the following {len(chunks)} chunks:\n\n"
            + "\n\n".join(chunks_text)
            + "\n\nRespond with a JSON array of profiles, one per chunk, in order. "
            + "Each profile must include: one_liner, summary, keywords, topics."
        )

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(user_content, f"chunk batch ({len(chunks)} chunks)")
        if error_result:
            # Return failed results for all chunks
            failed_results = [
                self.parser.create_failed_chunk_result(c, error_result.error_message or "Input too large")
                for c in chunks
            ]
            return failed_results, 0

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=CHUNK_PROFILE_SYSTEM_PROMPT,
            user_id=None,  # System operation
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning(f"chunk_batch_profiling_failed: {result.error_message}")
            # Return failed results for all chunks
            failed_results = [
                self.parser.create_failed_chunk_result(c, result.error_message or "LLM call failed") for c in chunks
            ]
            return failed_results, 0

        # Parse the response using dedicated parser
        parsed_results = self.parser.parse_chunk_profiles(result.content, chunks)
        return parsed_results, result.tokens_used

    async def aggregate_chunk_profiles(
        self,
        chunk_profiles: list[ChunkProfileResult],
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[tuple[DocumentProfile, list[str]] | None, SideCallResult]:
        """Generate a document profile by aggregating chunk profiles.

        Used for large documents that exceed PROFILING_FULL_DOC_MAX_TOKENS.
        Uses one-liners from chunks to generate synopsis and queries.

        Args:
            chunk_profiles: Profiles from all document chunks
            document_metadata: Optional document metadata
            timeout_ms: Optional timeout override

        Returns:
            Tuple of ((DocumentProfile, synthesized_queries) or None if failed, SideCallResult)

        """
        timeout = self._resolve_timeout_ms(timeout_ms)

        # Build summary using one-liners for compact representation
        one_liner_summaries = []
        all_keywords = set()
        all_topics = set()

        for cp in chunk_profiles:
            if cp.success and cp.profile:
                # Prefer one_liner, fall back to summary
                summary_text = cp.profile.one_liner or cp.profile.summary
                one_liner_summaries.append(f"Chunk {cp.chunk_index}: {summary_text}")
                all_keywords.update(cp.profile.keywords)
                all_topics.update(cp.profile.topics)

        user_content = (
            f"Document has {len(chunk_profiles)} chunks.\n\n"
            f"Chunk one-liners:\n" + "\n".join(one_liner_summaries) + "\n\n"
            f"Keywords found: {', '.join(list(all_keywords)[:50])}\n"
            f"Topics found: {', '.join(list(all_topics)[:30])}"
        )

        if document_metadata:
            meta_str = json.dumps(document_metadata, indent=2)
            user_content = f"Document metadata:\n{meta_str}\n\n{user_content}"

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(user_content, f"aggregate profiling ({len(chunk_profiles)} chunks)")
        if error_result:
            return None, error_result

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=AGGREGATE_PROFILE_SYSTEM_PROMPT,
            user_id=None,
            timeout_ms=timeout,
        )

        if not result.success:
            logger.warning("aggregate_profiling_failed", error=result.error_message)
            return None, result

        # Parse the response - now includes synthesized_queries
        parsed = self.parser.parse_aggregate_response(result.content)
        return parsed, result
