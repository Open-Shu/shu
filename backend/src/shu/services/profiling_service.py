"""Document and Chunk Profiling Service (SHU-343).

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate document profiles (synopsis, type, capability manifest)
- Generate chunk profiles (summary, keywords, topics)
- Batch chunk profiling for efficiency
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
)
from ..utils.tokenization import estimate_tokens
from .profile_parser import ProfileParser
from .side_call_service import SideCallResult, SideCallService

logger = structlog.get_logger(__name__)


# Prompts for profiling
DOCUMENT_PROFILE_SYSTEM_PROMPT = """You are a document profiling assistant. Your task is to analyze a document and generate a structured profile that describes what the document contains and what questions it can answer.

Generate a JSON response with the following structure:
{
    "synopsis": "A one-paragraph summary (2-4 sentences) capturing the document's essence, main topics, and purpose.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {
        "answers_questions_about": ["list of specific topics/subjects the document addresses"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions", etc.],
        "authority_level": "primary (authoritative source), secondary (derived), or commentary",
        "completeness": "complete (standalone), partial (needs context), or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how" - which apply]
    }
}

Be specific and concise. Focus on what makes this document useful for answering questions."""

CHUNK_PROFILE_SYSTEM_PROMPT = """You are a document chunk profiling assistant. Your task is to analyze text chunks and generate lightweight metadata for retrieval indexing.

For each chunk, generate a JSON response with:
{
    "summary": "One-line description of what this chunk contains",
    "keywords": ["specific", "extractable", "terms", "names", "numbers", "dates"],
    "topics": ["conceptual", "categories", "themes"]
}

Keywords should be specific, extractable terms from the text (names, numbers, dates, technical terms).
Topics should be broader conceptual categories that help group related content.
Keep summaries under 100 characters. Limit to 5-10 keywords and 3-5 topics."""

AGGREGATE_PROFILE_SYSTEM_PROMPT = """You are a document summarization assistant. Given summaries of document chunks, generate an overall document profile.

Generate a JSON response with the following structure:
{
    "synopsis": "A one-paragraph summary (2-4 sentences) synthesizing the chunk summaries into a cohesive document overview.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {
        "answers_questions_about": ["consolidated list of topics from all chunks"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions", etc.],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    }
}

Synthesize the chunk information into a coherent whole, not just a list."""


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
                duration_ms=0,
                error_message=error_msg,
            )
        return None

    async def profile_document(
        self,
        document_text: str,
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[DocumentProfile | None, SideCallResult]:
        """Generate a document profile from full document text.

        Used for small documents that fit within PROFILING_FULL_DOC_MAX_TOKENS.

        Args:
            document_text: Full text of the document
            document_metadata: Optional metadata (title, source, etc.)
            timeout_ms: Optional timeout override

        Returns:
            Tuple of (DocumentProfile or None if failed, SideCallResult with details)

        """
        timeout = self._resolve_timeout_ms(timeout_ms)

        # Build the user message
        user_content = f"Document text:\n\n{document_text}"
        if document_metadata:
            meta_str = json.dumps(document_metadata, indent=2)
            user_content = f"Document metadata:\n{meta_str}\n\n{user_content}"

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(user_content, "document profiling")
        if error_result:
            return None, error_result

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=DOCUMENT_PROFILE_SYSTEM_PROMPT,
            user_id=None,  # System operation - no user attribution
            timeout_ms=timeout,
        )

        if not result.success:
            logger.warning(
                "document_profiling_failed",
                error=result.error_message,
            )
            return None, result

        # Parse the response using dedicated parser
        profile = self.parser.parse_document_profile(result.content)
        return profile, result

    async def profile_chunks(
        self,
        chunks: list[ChunkData],
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], int]:
        """Generate profiles for multiple chunks.

        Processes chunks in batches for efficiency.

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
            + "\n\nRespond with a JSON array of profiles, one per chunk, in order."
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
            user_id=None,  # System operation - no user attribution
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
    ) -> tuple[DocumentProfile | None, SideCallResult]:
        """Generate a document profile by aggregating chunk profiles.

        Used for large documents that exceed PROFILING_FULL_DOC_MAX_TOKENS.

        Args:
            chunk_profiles: Profiles from all document chunks
            document_metadata: Optional document metadata
            timeout_ms: Optional timeout override

        Returns:
            Tuple of (DocumentProfile or None if failed, SideCallResult)

        """
        timeout = self._resolve_timeout_ms(timeout_ms)

        # Build summary of chunk profiles
        chunk_summaries = []
        all_keywords = set()
        all_topics = set()

        for cp in chunk_profiles:
            if cp.success and cp.profile:
                chunk_summaries.append(f"Chunk {cp.chunk_index}: {cp.profile.summary}")
                all_keywords.update(cp.profile.keywords)
                all_topics.update(cp.profile.topics)

        user_content = (
            f"Document has {len(chunk_profiles)} chunks.\n\n"
            f"Chunk summaries:\n" + "\n".join(chunk_summaries) + "\n\n"
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
            user_id=None,  # System operation - no user attribution
            timeout_ms=timeout,
        )

        if not result.success:
            logger.warning(
                "aggregate_profiling_failed",
                error=result.error_message,
            )
            return None, result

        profile = self.parser.parse_document_profile(result.content)
        return profile, result
