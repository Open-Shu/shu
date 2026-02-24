"""Document and Chunk Profiling Service.

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate unified profiles (synopsis, one-liners, chunk profiles, queries) for small docs
- Generate chunk profiles (one-liner, summary, keywords, topics) for batch processing
- Generate document metadata in final batch for large documents (incremental profiling)
- Enforce profiling_max_input_tokens limit on all LLM calls
"""

import json

import structlog

from ..core.config import Settings
from ..schemas.profiling import (
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentProfile,
    DocumentType,
    UnifiedProfilingResponse,
)
from ..utils.tokenization import estimate_tokens
from .profile_parser import ProfileParser
from .side_call_service import SideCallResult, SideCallService

logger = structlog.get_logger(__name__)


# Unified profiling prompt template with conditional query synthesis sections
# Placeholders: {query_intro}, {queries_json}, {queries_guidelines}
# When queries disabled: all placeholders are empty strings
# When queries enabled: placeholders contain the query-specific content
UNIFIED_PROFILING_PROMPT_TEMPLATE = """You are a document profiling assistant. Analyze the document and generate a complete profile including document-level metadata and per-chunk summaries{query_intro}.

Generate a JSON response with this exact structure:
{{
    "synopsis": "A 2-4 sentence summary capturing the document's essence, main topics, and purpose.",
    "chunks": [
        {{
            "index": 0,
            "one_liner": "Condensed summary (~50-80 chars) for quick scanning",
            "summary": "Longer description of what this chunk contains",
            "keywords": ["specific", "extractable", "terms"],
            "topics": ["conceptual", "categories"]
        }}
    ],
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {{
        "answers_questions_about": ["list of specific topics the document addresses"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions"],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    }}{queries_json}
}}

Guidelines:
- one_liner: Start with action verb when possible ("Explains...", "Covers...", "Lists..."). Capture specific information, not just topic.
- synopsis: Capture strategic narrative and cross-chunk themes.{queries_guidelines}
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

# Final batch prompt template with conditional query synthesis sections
# Same placeholder pattern as unified prompt
FINAL_BATCH_PROMPT_TEMPLATE = """You are a document profiling assistant. You are processing the FINAL batch of chunks for a large document.

You have two tasks:
1. Profile the chunks in this batch (same as previous batches)
2. Generate document-level metadata using the accumulated one-liners from ALL previous chunks

Generate a JSON response with this exact structure:
{{
    "chunks": [
        {{
            "index": 0,
            "one_liner": "Condensed summary (~50-80 chars) for quick scanning",
            "summary": "Longer description of what this chunk contains",
            "keywords": ["specific", "extractable", "terms"],
            "topics": ["conceptual", "categories"]
        }}
    ],
    "synopsis": "A 2-4 sentence summary synthesizing ALL chunk one-liners into a cohesive document overview.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {{
        "answers_questions_about": ["consolidated list of topics from all chunks"],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions"],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    }}{queries_json}
}}

Guidelines:
- one_liner: Start with action verb ("Explains...", "Covers...", "Lists..."). Capture specific information.
- synopsis: Synthesize the accumulated one-liners into a strategic narrative. Capture cross-chunk themes.{queries_guidelines}
- keywords: Specific extractable terms from the text.
- topics: Broader conceptual categories."""

# Query synthesis additions - injected into templates when enable_query_synthesis=True
QUERY_INTRO_ADDITION = ", and hypothetical queries"

QUERIES_JSON_ADDITION = """,
    "synthesized_queries": [
        "What is the main topic of this document?",
        "How does X work?",
        "Tell me about Y"
    ]"""

QUERIES_GUIDELINES_TEMPLATE = """
- synthesized_queries: Generate {min_queries}-{max_queries} queries that this document can answer.
  PURPOSE: These queries will be embedded and matched against user searches to help find this document.
  Include specific details (names, dates, topics, unique terms) that distinguish this document from others.
  AVOID generic queries like "What were the results?" - instead use "What were the results of the Q3 2024 marketing analysis?"
  Balance specificity with discoverability - queries should be specific enough to identify this document but general enough to match natural user questions.
  Scaling rules:
  - Generate at least one query per distinct topic in answers_questions_about
  - Include a mix of interrogative ("What is...?"), imperative ("Tell me about..."), and keyword searches
  - Simple single-topic documents: closer to {min_queries}
  - Complex multi-topic documents with many domains: closer to {max_queries}"""


class ProfilingService:
    """Service for generating document and chunk profiles via LLM inference."""

    def __init__(
        self,
        side_call_service: SideCallService,
        settings: Settings,
    ) -> None:
        self.side_call = side_call_service
        self.settings = settings
        self.parser = ProfileParser(max_queries=settings.query_synthesis_max_queries)

    def _resolve_timeout_ms(self, timeout_ms: int | None, *, for_query_synthesis: bool = False) -> int:
        """Resolve timeout, using settings default if not provided.

        Args:
            timeout_ms: Explicit timeout override (takes priority)
            for_query_synthesis: If True, use query_synthesis_timeout_seconds as default
                instead of profiling_timeout_seconds (for calls that generate queries)

        """
        if timeout_ms is not None:
            return timeout_ms
        if for_query_synthesis:
            return self.settings.query_synthesis_timeout_seconds * 1000
        return self.settings.profiling_timeout_seconds * 1000

    def _build_unified_profiling_prompt(self) -> str:
        """Build unified profiling system prompt.

        Injects query synthesis sections when enable_query_synthesis is True.
        """
        if self.settings.enable_query_synthesis:
            queries_guidelines = QUERIES_GUIDELINES_TEMPLATE.format(
                min_queries=self.settings.query_synthesis_min_queries,
                max_queries=self.settings.query_synthesis_max_queries,
            )
            return UNIFIED_PROFILING_PROMPT_TEMPLATE.format(
                query_intro=QUERY_INTRO_ADDITION,
                queries_json=QUERIES_JSON_ADDITION,
                queries_guidelines=queries_guidelines,
            )
        return UNIFIED_PROFILING_PROMPT_TEMPLATE.format(
            query_intro="",
            queries_json="",
            queries_guidelines="",
        )

    def _build_final_batch_prompt(self) -> str:
        """Build final batch system prompt.

        Injects query synthesis sections when enable_query_synthesis is True.
        """
        if self.settings.enable_query_synthesis:
            queries_guidelines = QUERIES_GUIDELINES_TEMPLATE.format(
                min_queries=self.settings.query_synthesis_min_queries,
                max_queries=self.settings.query_synthesis_max_queries,
            )
            return FINAL_BATCH_PROMPT_TEMPLATE.format(
                queries_json=QUERIES_JSON_ADDITION,
                queries_guidelines=queries_guidelines,
            )
        return FINAL_BATCH_PROMPT_TEMPLATE.format(
            queries_json="",
            queries_guidelines="",
        )

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
        timeout = self._resolve_timeout_ms(timeout_ms, for_query_synthesis=self.settings.enable_query_synthesis)

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
            system_prompt=self._build_unified_profiling_prompt(),
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
        if not unified_response:
            logger.warning("failed_to_parse_unified_profiling_response")
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

    async def profile_chunks_incremental(
        self,
        chunks: list[ChunkData],
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], DocumentProfile | None, list[str], int]:
        """Profile chunks incrementally, with final batch generating document metadata.

        This method eliminates the separate aggregation LLM call by having the final
        batch generate document-level metadata (synopsis, capability_manifest, queries)
        from accumulated one-liners.

        Args:
            chunks: List of chunk data to profile
            document_metadata: Optional document metadata (title, source, etc.)
            timeout_ms: Optional timeout override per batch

        Returns:
            Tuple of (chunk_results, document_profile, synthesized_queries, total_tokens)

        """
        if not chunks:
            return [], None, [], 0

        timeout = self._resolve_timeout_ms(timeout_ms)
        final_batch_timeout = self._resolve_timeout_ms(
            timeout_ms, for_query_synthesis=self.settings.enable_query_synthesis
        )
        batch_size = self.settings.chunk_profiling_batch_size
        all_results: list[ChunkProfileResult] = []
        accumulated_one_liners: list[str] = []
        total_tokens = 0

        # Calculate batch boundaries
        num_batches = (len(chunks) + batch_size - 1) // batch_size

        for batch_idx in range(num_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(chunks))
            batch = chunks[start_idx:end_idx]
            is_final_batch = batch_idx == num_batches - 1

            if is_final_batch:
                # Final batch: generate chunk profiles AND document metadata
                batch_results, doc_profile, queries, tokens = await self._profile_final_batch(
                    batch,
                    accumulated_one_liners,
                    document_metadata,
                    final_batch_timeout,
                )
                all_results.extend(batch_results)
                total_tokens += tokens
                return all_results, doc_profile, queries, total_tokens
            # Regular batch: just chunk profiles
            batch_results, tokens = await self._profile_chunk_batch(batch, timeout)
            all_results.extend(batch_results)
            total_tokens += tokens

            # Accumulate one-liners for final batch
            for result in batch_results:
                if result.success and result.profile.one_liner:
                    accumulated_one_liners.append(f"Chunk {result.chunk_index}: {result.profile.one_liner}")

        # Should not reach here, but handle edge case
        return all_results, None, [], total_tokens

    async def _profile_final_batch(
        self,
        chunks: list[ChunkData],
        accumulated_one_liners: list[str],
        document_metadata: dict | None,
        timeout_ms: int,
    ) -> tuple[list[ChunkProfileResult], DocumentProfile | None, list[str], int]:
        """Profile the final batch and generate document-level metadata.

        Args:
            chunks: Chunks in this final batch
            accumulated_one_liners: One-liners from all previous batches
            document_metadata: Optional document metadata
            timeout_ms: Timeout for LLM call

        Returns:
            Tuple of (chunk_results, document_profile, synthesized_queries, tokens_used)

        """
        # Build user message with chunks and accumulated context
        chunks_text = []
        for chunk in chunks:
            chunks_text.append(f"[CHUNK {chunk.chunk_index}]\n{chunk.content}\n[/CHUNK]")

        user_content = "This is the FINAL batch of chunks for this document.\n\n"

        if accumulated_one_liners:
            user_content += "One-liners from previous chunks:\n"
            user_content += "\n".join(accumulated_one_liners)
            user_content += "\n\n"

        user_content += f"Profile these final {len(chunks)} chunks:\n\n"
        user_content += "\n\n".join(chunks_text)

        # Build response instructions - only request queries if enabled
        user_content += (
            "\n\nRespond with a JSON object containing:\n"
            "1. 'chunks': array of profiles for these chunks (index, one_liner, summary, keywords, topics)\n"
            "2. 'synopsis': 2-4 sentence summary of the ENTIRE document based on all one-liners\n"
            "3. 'document_type': narrative, transactional, technical, or conversational\n"
            "4. 'capability_manifest': what questions this document can answer"
        )
        if self.settings.enable_query_synthesis:
            min_q = self.settings.query_synthesis_min_queries
            max_q = self.settings.query_synthesis_max_queries
            user_content += f"\n5. 'synthesized_queries': {min_q}-{max_q} queries this document can answer"

        if document_metadata:
            meta_str = json.dumps(document_metadata, indent=2)
            user_content = f"Document metadata:\n{meta_str}\n\n{user_content}"

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(
            user_content, f"final batch ({len(chunks)} chunks, {len(accumulated_one_liners)} accumulated)"
        )
        if error_result:
            failed_results = [
                self.parser.create_failed_chunk_result(c, error_result.error_message or "Input too large")
                for c in chunks
            ]
            return failed_results, None, [], 0

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=self._build_final_batch_prompt(),
            user_id=None,
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning("final_batch_profiling_failed", error=result.error_message)
            failed_results = [
                self.parser.create_failed_chunk_result(c, result.error_message or "LLM call failed") for c in chunks
            ]
            return failed_results, None, [], 0

        # Parse the final batch response
        final_response = self.parser.parse_final_batch_response(result.content)
        if not final_response:
            logger.warning("failed_to_parse_final_batch_response")
            failed_results = [
                self.parser.create_failed_chunk_result(c, "Failed to parse final batch response") for c in chunks
            ]
            return failed_results, None, [], result.tokens_used

        # Convert FinalBatchResponse chunks to ChunkProfileResults
        chunk_results = []
        response_chunks_by_index = {rc.index: rc for rc in final_response.chunks}

        for i, chunk in enumerate(chunks):
            response_chunk = response_chunks_by_index.get(chunk.chunk_index)
            if not response_chunk and i < len(final_response.chunks):
                # Fallback: LLM may have returned batch-relative (0-based) indices
                logger.debug(
                    "using_positional_fallback_for_chunk",
                    expected_index=chunk.chunk_index,
                    position=i,
                )
                response_chunk = final_response.chunks[i]
            if response_chunk:
                profile = ChunkProfile(
                    one_liner=response_chunk.one_liner,
                    summary=response_chunk.summary,
                    keywords=response_chunk.keywords,
                    topics=response_chunk.topics,
                )
                chunk_results.append(
                    ChunkProfileResult(
                        chunk_id=chunk.chunk_id,
                        chunk_index=chunk.chunk_index,
                        profile=profile,
                        success=True,
                    )
                )
            else:
                chunk_results.append(
                    self.parser.create_failed_chunk_result(chunk, "No profile in final batch response")
                )

        # Build DocumentProfile from final response
        try:
            doc_type = DocumentType((final_response.document_type or "narrative").lower())
        except (ValueError, AttributeError):
            doc_type = DocumentType.NARRATIVE

        doc_profile = DocumentProfile(
            synopsis=final_response.synopsis,
            document_type=doc_type,
            capability_manifest=final_response.capability_manifest,
        )

        return chunk_results, doc_profile, final_response.synthesized_queries, result.tokens_used
