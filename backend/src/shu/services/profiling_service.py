"""Document and Chunk Profiling Service.

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate chunk profiles (summary, keywords, topics) for batch processing
- Generate document metadata in final batch (incremental profiling)
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
)
from ..utils.tokenization import estimate_tokens
from .profile_parser import ProfileParser
from .side_call_service import SideCallResult, SideCallService

logger = structlog.get_logger(__name__)


# Prompt for chunk-only profiling (used in batch processing)
CHUNK_PROFILE_SYSTEM_PROMPT = """You are profiling document chunks for an AI retrieval system.

PURPOSE: An AI agent scans these profiles to decide which chunks to retrieve. Generic descriptions are useless. Extract SPECIFIC, DISTINGUISHING details.

For each chunk, generate:
{
    "summary": "One-line summary with SPECIFIC content (names, figures, dates). Start with action verb.",
    "keywords": ["extract", "every", "proper noun", "date", "version", "amount"],
    "topics": ["specific categories", "not just 'database' but 'PostgreSQL indexing'"]
}

Examples:
BAD summary: "Discusses security configuration"
GOOD summary: "Configures OAuth2 scopes for admin API endpoints with JWT expiry settings"

BAD keywords: ["security", "configuration", "settings"]
GOOD keywords: ["OAuth2", "admin API", "read:users scope", "JWT expiry"]

Guidelines:
- summary: One line only. Start with action verb ("Explains...", "Details...", "Lists..."). Include the SPECIFIC subject.
- keywords: Extract EVERY proper noun, date, version number, monetary amount, and technical term.
- topics: Specific enough to be useful (e.g., "PostgreSQL indexing" not just "databases").
Limit to 5-10 keywords and 3-5 topics. Prioritize specificity over completeness."""

# Final batch prompt template with conditional query synthesis sections
# Same placeholder pattern as unified prompt
FINAL_BATCH_PROMPT_TEMPLATE = """You are profiling documents for an AI-powered retrieval system. You are processing the FINAL batch of chunks for a large document.

PURPOSE: An AI agent will use your output to decide whether to retrieve this document. Generic descriptions are USELESS. Extract SPECIFIC, DISTINGUISHING details.

You have two tasks:
1. Profile the chunks in this batch (same as previous batches)
2. Generate document-level metadata using the accumulated summaries from ALL previous chunks

Generate a JSON response with this exact structure:
{{
    "chunks": [
        {{
            "index": 0,
            "summary": "One-line summary with SPECIFIC content (names, figures, dates). Start with action verb.",
            "keywords": ["Acme Corp", "Q3 2024", "$4.2M", "John Smith"],
            "topics": ["quarterly earnings", "revenue growth"]
        }}
    ],
    "synopsis": "2-4 sentences with SPECIFIC details synthesized from ALL chunks. Include names, dates, figures, decisions.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {{
        "answers_questions_about": [
            "SPECIFIC topics consolidated from all chunks with named entities and dates",
            "Example: 'Acme Corp Q3 2024 revenue and profit margins'"
        ],
        "provides_information_type": ["facts", "opinions", "decisions", "instructions"],
        "authority_level": "primary, secondary, or commentary",
        "completeness": "complete, partial, or reference",
        "question_domains": ["who", "what", "when", "where", "why", "how"]
    }}{queries_json}
}}

CRITICAL - answers_questions_about:
BAD (too generic): "security measures", "strategic vision", "project updates"
GOOD (specific): "OAuth2 vulnerability in auth-service v2.3", "Q3 2024 board decision to acquire TechStart Inc"

Guidelines:
- summary: One line only. Start with action verb ("Explains...", "Details...", "Lists..."). Include the SPECIFIC subject.
- synopsis: Lead with the most important SPECIFIC facts from across the document.{queries_guidelines}
- keywords: Extract EVERY proper noun, date, version number, monetary amount, and technical term.
- topics: Specific enough to be useful (e.g., "PostgreSQL indexing" not just "databases").

Examples:
BAD summary: "Discusses security configuration"
GOOD summary: "Configures OAuth2 scopes for admin API endpoints with JWT expiry settings\""""

# Query synthesis additions - injected into templates when enable_query_synthesis=True
QUERY_INTRO_ADDITION = ", and hypothetical queries"

QUERIES_JSON_ADDITION = """,
    "synthesized_queries": [
        "What was Acme Corp's Q3 2024 revenue?",
        "Who approved the TechStart acquisition?",
        "How do I configure OAuth2 scopes for the admin API?"
    ]"""

QUERIES_GUIDELINES_TEMPLATE = """
- synthesized_queries: Generate {min_queries}-{max_queries} queries this document can answer.
  PURPOSE: These queries will be embedded and matched against user searches.

  CRITICAL: Include SPECIFIC details from the document:
  BAD: "What were the results?" (matches any document with results)
  GOOD: "What were the Q3 2024 sales results for the EMEA region?"

  BAD: "How does authentication work?" (matches any auth doc)
  GOOD: "How does OAuth2 token refresh work in auth-service?"

  Include in your queries:
  - Named entities (people, companies, projects, products)
  - Dates and time periods
  - Version numbers, amounts, metrics
  - Specific technical terms unique to this document

  Query types to include:
  - Factual: "What is X's Y?" "How much did Z cost?"
  - Procedural: "How do I configure X?" "What are the steps for Y?"
  - Comparative: "What changed between v1 and v2?"
  - Entity-focused: "What did [Person] say about [Topic]?"

  Scale: {min_queries} for simple docs, {max_queries} for complex multi-topic docs."""


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

    async def profile_chunks(
        self,
        chunks: list[ChunkData],
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], int]:
        """Generate profiles for multiple chunks.

        Processes chunks in batches for efficiency. Each chunk gets:
        - summary: One-line description for agent scanning and retrieval
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
            + f"\n\nIMPORTANT: Return a JSON array with EXACTLY {len(chunks)} profiles, one per chunk, in the same order. "
            + "Do NOT skip, merge, or add extra profiles. Each profile must include: summary, keywords, topics."
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

        result = await self.side_call.call_for_profiling(
            message_sequence=message_sequence,
            system_prompt=CHUNK_PROFILE_SYSTEM_PROMPT,
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
        from accumulated summaries.

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
        accumulated_summaries: list[str] = []
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
                    accumulated_summaries,
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

            # Accumulate summaries for final batch
            for result in batch_results:
                if result.success and result.profile.summary:
                    accumulated_summaries.append(f"Chunk {result.chunk_index}: {result.profile.summary}")

        # Should not reach here, but handle edge case
        return all_results, None, [], total_tokens

    async def _profile_final_batch(
        self,
        chunks: list[ChunkData],
        accumulated_summaries: list[str],
        document_metadata: dict | None,
        timeout_ms: int,
    ) -> tuple[list[ChunkProfileResult], DocumentProfile | None, list[str], int]:
        """Profile the final batch and generate document-level metadata.

        Args:
            chunks: Chunks in this final batch
            accumulated_summaries: Summaries from all previous batches
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

        if accumulated_summaries:
            user_content += "Summaries from previous chunks:\n"
            user_content += "\n".join(accumulated_summaries)
            user_content += "\n\n"

        user_content += f"Profile these final {len(chunks)} chunks:\n\n"
        user_content += "\n\n".join(chunks_text)

        # Build response instructions - only request queries if enabled
        user_content += (
            "\n\nRespond with a JSON object containing:\n"
            "1. 'chunks': array of profiles for these chunks (index, summary, keywords, topics)\n"
            "2. 'synopsis': 2-4 sentence summary of the ENTIRE document based on all summaries\n"
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
            user_content, f"final batch ({len(chunks)} chunks, {len(accumulated_summaries)} accumulated)"
        )
        if error_result:
            failed_results = [
                self.parser.create_failed_chunk_result(c, error_result.error_message or "Input too large")
                for c in chunks
            ]
            return failed_results, None, [], 0

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call_for_profiling(
            message_sequence=message_sequence,
            system_prompt=self._build_final_batch_prompt(),
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
