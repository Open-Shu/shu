"""Document and Chunk Profiling Service.

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate chunk profiles (summary, topics) for batch processing
- Generate document metadata in a separate LLM call after all chunks are profiled
- Enforce profiling_max_input_tokens limit on all LLM calls
"""

import json

import structlog

from ..core.config import Settings
from ..schemas.profiling import (
    ChunkData,
    ChunkProfileResult,
    DocumentProfile,
    SynthesizedQuery,
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
    "topics": ["specific categories", "not just 'database' but 'PostgreSQL indexing'"]
}

Examples:
BAD summary: "Discusses security configuration"
GOOD summary: "Configures OAuth2 scopes for admin API endpoints with JWT expiry settings"

Guidelines:
- summary: One line only. Start with action verb ("Explains...", "Details...", "Lists..."). Include the SPECIFIC subject.
- topics: Specific enough to be useful (e.g., "PostgreSQL indexing" not just "databases").
Limit to 3-5 topics. Prioritize specificity over completeness."""

# Document metadata prompt - focused solely on synthesizing document-level metadata
# Used AFTER all chunks are profiled, receives accumulated summaries as input
DOCUMENT_METADATA_PROMPT_TEMPLATE = """You are synthesizing document-level metadata from chunk summaries for an AI retrieval system.

PURPOSE: An AI agent will use your output to decide whether to retrieve this document. Generic descriptions are USELESS. Extract SPECIFIC, DISTINGUISHING details.

You are given summaries from ALL chunks of a document. Generate metadata that synthesizes these into a cohesive document profile.

Generate a JSON response with this exact structure:
{{
    "synopsis": "2-4 sentences with SPECIFIC details synthesized from ALL chunk summaries. Include names, dates, figures, decisions.",
    "document_type": "One of: narrative, transactional, technical, conversational",
    "capability_manifest": {{
        "answers_questions_about": [
            "SPECIFIC topics consolidated from all chunks with topics, named entities and dates",
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
- synopsis: Lead with the most important SPECIFIC facts from across the document. Synthesize, don't just list.{queries_guidelines}
- capability_manifest: Consolidate themes from all chunks into specific, queryable topics."""

# Query synthesis additions - always injected into document metadata prompts
QUERIES_JSON_ADDITION = """,
    "chunk_queries": [
        {
            "chunk_index": 0,
            "queries": ["How did Acme Corp do in Q3 2024?", "Did Acme Corp acquire TechStart?"]
        },
        {
            "chunk_index": 1,
            "queries": ["Does auth-service support OAuth2?", "How do I set up auth for the admin API?"]
        }
    ]"""

QUERIES_GUIDELINES_TEMPLATE = """
- chunk_queries: For EACH chunk summary above, generate {queries_per_chunk} capability queries.

  PURPOSE: These queries are embedded and matched against user searches at retrieval time.
  Users are searching ACROSS ALL documents — they have not selected this document yet.
  Every query must be grounded in the document's subject so it is findable in a global search.

  Other retrieval surfaces already handle content-level matching: raw chunk embeddings match
  specific facts, chunk summary embeddings match topical descriptions, and BM25 full-text
  search matches named entities. Synthesized queries must cover what those surfaces CANNOT:
  interpretive, thematic, and capability-oriented questions about the document's subject.

  GROUNDING RULE (mandatory):
  Every query MUST name the document's primary subject — the product, company, system,
  study, or topic that distinguishes this document from all others in the knowledge base.
  Identify the subject from the document metadata and chunk topics. A query that
  could apply to any document on a similar topic is ungrounded and useless.

  CAPABILITY QUERIES ask what the document can ANSWER, not what it contains.
  - Ask about conclusions, implications, safety, efficacy, comparisons, decisions —
    things a user WANTS TO KNOW, not data points they could grep for
  - Phrase as a real person would ask — short, direct, natural language
  - Think: "What question does this chunk ANSWER?" not "What does this chunk SAY?"

  GOOD vs BAD examples:

  Document subject: Compound X-47 (preclinical drug candidate)
  Chunk summary: "Lists hematology and clinical chemistry panels used to assess Compound X-47 toxicity"
    BAD (restates content — redundant with chunk and summary embeddings):
    - "What hematology and clinical chemistry parameters were measured for Compound X-47?"
    BAD (ungrounded — could match any preclinical study):
    - "Did any extended chemistry panels reveal adverse effects?"
    - "What additional coagulation and biochemistry tests were performed?"
    GOOD (grounded + capability-level):
    - "Is Compound X-47 toxic?"
    - "What safety tests were run on Compound X-47?"

  Document subject: Acme Corp (quarterly earnings)
  Chunk summary: "Details Acme Corp EMEA revenue figures and profit margins for Q3 2024"
    BAD: "What was Acme Corp's EMEA revenue in Q3 2024?"
    BAD (ungrounded): "What were the quarterly profit margins?"
    GOOD: "How did Acme Corp do in Q3 2024?"
    GOOD: "Is Acme Corp profitable in EMEA?"

  Document subject: auth-service (internal platform)
  Chunk summary: "Configures OAuth2 scopes for admin API endpoints with JWT expiry settings"
    BAD: "How do I configure OAuth2 scopes for admin API endpoints with JWT expiry?"
    BAD (ungrounded): "What authentication scopes are available?"
    GOOD: "How do I set up auth for the admin API?"
    GOOD: "Does auth-service support OAuth2?"

  Heuristic: if the query would match well against the chunk's raw text via embedding
  similarity, it is too specific and redundant. Step up one level of abstraction.
  If the query could appear in a search against ANY similar document, it is ungrounded.

  Return chunk_queries as an array with one entry per chunk, in chunk_index order.
  Maximum {max_total_queries} queries total — if the document has many chunks, prioritize
  chunks with the most distinctive topics."""


class ProfilingService:
    """Service for generating document and chunk profiles via LLM inference."""

    def __init__(
        self,
        side_call_service: SideCallService,
        settings: Settings,
    ) -> None:
        self.side_call = side_call_service
        self.settings = settings
        self.parser = ProfileParser(max_total_queries=settings.query_synthesis_max_total_queries)

    def _resolve_timeout_ms(self, timeout_ms: int | None, *, for_metadata: bool = False) -> int:
        """Resolve timeout, using settings default if not provided.

        Args:
            timeout_ms: Explicit timeout override (takes priority)
            for_metadata: If True, use profiling_metadata_timeout_seconds (for the
                document-level metadata synthesis call, which is heavier than chunk batches)

        """
        if timeout_ms is not None:
            return timeout_ms
        if for_metadata:
            return self.settings.profiling_metadata_timeout_seconds * 1000
        return self.settings.profiling_timeout_seconds * 1000

    def _build_document_metadata_prompt(self) -> str:
        """Build document metadata synthesis system prompt.

        Always includes query synthesis sections — query generation is embedded
        in the same profiling LLM call at no additional cost.
        """
        queries_guidelines = QUERIES_GUIDELINES_TEMPLATE.format(
            queries_per_chunk=self.settings.query_synthesis_queries_per_chunk,
            max_total_queries=self.settings.query_synthesis_max_total_queries,
        )
        return DOCUMENT_METADATA_PROMPT_TEMPLATE.format(
            queries_json=QUERIES_JSON_ADDITION,
            queries_guidelines=queries_guidelines,
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
        # Use batch-relative numbering (1 of N) to avoid confusion with document-level indices
        chunks_text = []
        for i, chunk in enumerate(chunks):
            chunks_text.append(f"### Chunk {i + 1} of {len(chunks)}:\n{chunk.content}")

        user_content = (
            f"Profile the following {len(chunks)} chunks. You MUST return exactly {len(chunks)} profiles.\n\n"
            + "\n\n".join(chunks_text)
            + f"\n\nReturn a JSON array with EXACTLY {len(chunks)} objects in order: "
            + f"[profile for chunk 1, profile for chunk 2, ... profile for chunk {len(chunks)}]. "
            + "Each object must have: summary, topics."
        )

        # Add title chunk guidance when chunk 0 is in this batch
        if self.settings.title_chunk_enabled_default and any(c.chunk_index == 0 for c in chunks):
            user_content += (
                "\n\nNOTE: Chunk 1 is the document title/subject. Use context from the other chunks "
                "to infer what acronyms and identifiers in the title mean. Include the abbreviation "
                "and its expansion in the summary if useful."
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
            logger.warning("chunk_batch_profiling_failed", error=result.error_message, chunk_count=len(chunks))
            # Return failed results for all chunks, but preserve token count for cost tracking
            failed_results = [
                self.parser.create_failed_chunk_result(c, result.error_message or "LLM call failed") for c in chunks
            ]
            return failed_results, result.tokens_used

        # Parse the response using dedicated parser
        parsed_results = self.parser.parse_chunk_profiles(result.content, chunks)
        return parsed_results, result.tokens_used

    async def _generate_document_metadata(
        self,
        accumulated_summaries: list[str],
        document_metadata: dict | None,
        timeout_ms: int,
    ) -> tuple[DocumentProfile | None, list[SynthesizedQuery], int]:
        """Generate document-level metadata from accumulated chunk context.

        This is a separate, focused LLM call that synthesizes chunk summaries
        and topics into document-level metadata (synopsis,
        capability_manifest, queries).

        Args:
            accumulated_summaries: Context from all profiled chunks (summary + topics)
            document_metadata: Optional document metadata (title, source, etc.)
            timeout_ms: Timeout for LLM call

        Returns:
            Tuple of (document_profile, synthesized_queries, tokens_used)

        """
        if not accumulated_summaries:
            logger.warning("generate_document_metadata_called_with_no_summaries")
            return None, [], 0

        # Build user message with accumulated chunk context
        user_content = "Synthesize document-level metadata from these chunk profiles:\n\n"
        user_content += "\n".join(accumulated_summaries)

        # Build response instructions — always include per-chunk query synthesis
        queries_per_chunk = self.settings.query_synthesis_queries_per_chunk
        user_content += (
            "\n\nRespond with a JSON object containing:\n"
            "1. 'synopsis': 2-4 sentence summary of the ENTIRE document\n"
            "2. 'document_type': narrative, transactional, technical, or conversational\n"
            "3. 'capability_manifest': what questions this document can answer\n"
            f"4. 'chunk_queries': for each chunk, generate {queries_per_chunk} capability "
            "queries (see system prompt for format)"
        )

        if document_metadata:
            meta_str = json.dumps(document_metadata, indent=2, default=str)
            user_content = f"Document metadata:\n{meta_str}\n\n{user_content}"

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(
            user_content, f"document metadata ({len(accumulated_summaries)} summaries)"
        )
        if error_result:
            return None, [], 0

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call_for_profiling(
            message_sequence=message_sequence,
            system_prompt=self._build_document_metadata_prompt(),
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning("document_metadata_generation_failed", error=result.error_message)
            return None, [], result.tokens_used

        # Parse the response using dedicated parser
        metadata_response = self.parser.parse_document_metadata_response(result.content)
        if not metadata_response:
            logger.warning("failed_to_parse_document_metadata_response")
            return None, [], result.tokens_used

        # Build DocumentProfile from response (document_type already validated by parser)
        doc_profile = DocumentProfile(
            synopsis=metadata_response.synopsis,
            document_type=metadata_response.document_type,
            capability_manifest=metadata_response.capability_manifest,
        )

        return doc_profile, metadata_response.synthesized_queries, result.tokens_used

    def _is_chunk_profile_failed(self, result: ChunkProfileResult) -> bool:
        """Check if a chunk profile result is considered failed.

        A chunk is failed if:
        - result.success is False (LLM error, parse error, etc.)
        - result.profile.summary is empty (LLM returned no useful content)

        Args:
            result: The chunk profile result to check

        Returns:
            True if the profile should be retried, False if successful

        """
        if not result.success:
            return True
        return not result.profile.summary or not result.profile.summary.strip()

    async def _retry_failed_chunks(
        self,
        failed_chunks: list[ChunkData],
        successful_summaries: list[str],
        document_title: str | None,
        timeout_ms: int,
    ) -> tuple[list[ChunkProfileResult], int]:
        """Retry profiling for failed chunks with document context.

        Provides context from successful chunk summaries to help the LLM
        understand the document and generate better profiles for problematic chunks.

        Args:
            failed_chunks: Chunks that failed initial profiling
            successful_summaries: Summaries from successfully profiled chunks
            document_title: Optional document title for context
            timeout_ms: Timeout for the retry LLM call

        Returns:
            Tuple of (retry results, tokens used)

        """
        if not failed_chunks:
            return [], 0

        # Build context from successful summaries (limit to avoid token overflow)
        context_parts = []
        if document_title:
            context_parts.append(f"Document: {document_title}")

        if successful_summaries:
            # Limit context to first 10 summaries to avoid token overflow
            limited_summaries = successful_summaries[:10]
            context_parts.append("Context from successfully profiled chunks:")
            context_parts.extend(limited_summaries)

        context_str = "\n".join(context_parts) if context_parts else ""

        # Build user message with context and failed chunks
        chunks_text = []
        for i, chunk in enumerate(failed_chunks):
            chunks_text.append(f"### Chunk {i + 1} of {len(failed_chunks)}:\n{chunk.content}")

        user_content = ""
        if context_str:
            user_content = f"{context_str}\n\n---\n\n"

        user_content += (
            f"Profile the following {len(failed_chunks)} chunks. "
            f"You MUST return exactly {len(failed_chunks)} profiles.\n\n"
            + "\n\n".join(chunks_text)
            + f"\n\nReturn a JSON array with EXACTLY {len(failed_chunks)} objects in order. "
            + "Each object must have: summary, topics."
        )

        # Validate input doesn't exceed max tokens
        error_result = self._validate_input_tokens(user_content, f"retry batch ({len(failed_chunks)} chunks)")
        if error_result:
            failed_results = [
                self.parser.create_failed_chunk_result(c, error_result.error_message or "Input too large")
                for c in failed_chunks
            ]
            return failed_results, 0

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call_for_profiling(
            message_sequence=message_sequence,
            system_prompt=CHUNK_PROFILE_SYSTEM_PROMPT,
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning(
                "chunk_retry_profiling_failed",
                error=result.error_message,
                chunk_count=len(failed_chunks),
            )
            failed_results = [
                self.parser.create_failed_chunk_result(c, result.error_message or "Retry LLM call failed")
                for c in failed_chunks
            ]
            return failed_results, result.tokens_used

        # Parse the response
        parsed_results = self.parser.parse_chunk_profiles(result.content, failed_chunks)
        return parsed_results, result.tokens_used

    async def profile_chunks_incremental(
        self,
        chunks: list[ChunkData],
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], DocumentProfile | None, list[SynthesizedQuery], int, float]:
        """Profile chunks incrementally, then generate document metadata separately.

        This method separates chunk profiling from document metadata generation:
        1. All chunk batches are profiled uniformly using _profile_chunk_batch()
        2. Failed chunks are retried with document context (if retries enabled)
        3. A separate focused LLM call generates document-level metadata from accumulated summaries

        This separation improves reliability by reducing task complexity per LLM call.

        Args:
            chunks: List of chunk data to profile
            document_metadata: Optional document metadata (title, source, etc.)
            timeout_ms: Optional timeout override per batch

        Returns:
            Tuple of (chunk_results, document_profile, synthesized_queries, total_tokens, coverage_percent)

        """
        if not chunks:
            return [], None, [], 0, 100.0

        timeout = self._resolve_timeout_ms(timeout_ms)
        metadata_timeout = self._resolve_timeout_ms(timeout_ms, for_metadata=True)
        batch_size = self.settings.chunk_profiling_batch_size
        all_results: list[ChunkProfileResult] = []
        total_tokens = 0

        # Build a map from chunk_id to original ChunkData for retry lookup
        chunk_map = {c.chunk_id: c for c in chunks}

        # Phase 1: Profile ALL chunks in batches (uniform processing, no special cases)
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            batch_results, tokens = await self._profile_chunk_batch(batch, timeout)
            all_results.extend(batch_results)
            total_tokens += tokens

        # Phase 2: Identify failures and retry (if enabled)
        max_retries = self.settings.profiling_max_retries
        for retry_attempt in range(max_retries):
            # Find failed results and their corresponding chunk data
            failed_indices = []
            for i, result in enumerate(all_results):
                if self._is_chunk_profile_failed(result):
                    failed_indices.append(i)

            if not failed_indices:
                break  # No failures to retry

            # Get ChunkData for failed chunks
            failed_chunks = [chunk_map[all_results[i].chunk_id] for i in failed_indices]

            # Collect successful summaries for context
            successful_summaries = [
                f"Chunk {r.chunk_index}: {r.profile.summary}"
                for r in all_results
                if not self._is_chunk_profile_failed(r)
            ]

            # Get document title for context
            doc_title = document_metadata.get("title") if document_metadata else None

            logger.info(
                "retrying_failed_chunk_profiles",
                retry_attempt=retry_attempt + 1,
                max_retries=max_retries,
                failed_count=len(failed_chunks),
                successful_context_count=len(successful_summaries),
            )

            # Retry failed chunks
            retry_results, retry_tokens = await self._retry_failed_chunks(
                failed_chunks=failed_chunks,
                successful_summaries=successful_summaries,
                document_title=doc_title,
                timeout_ms=timeout,
            )
            total_tokens += retry_tokens

            # Merge retry results back into all_results
            for idx, retry_result in zip(failed_indices, retry_results, strict=True):
                all_results[idx] = retry_result

        # Phase 3: Calculate coverage and accumulate chunk context for metadata
        successful_count = sum(1 for r in all_results if not self._is_chunk_profile_failed(r))
        coverage_percent = (successful_count / len(chunks)) * 100 if chunks else 100.0

        accumulated_summaries = []
        for r in all_results:
            if self._is_chunk_profile_failed(r):
                continue
            entry = f"Chunk {r.chunk_index}: {r.profile.summary}"
            if r.profile.topics:
                entry += f"\n  Topics: {', '.join(r.profile.topics)}"
            accumulated_summaries.append(entry)

        logger.info(
            "chunk_profiling_coverage",
            total_chunks=len(chunks),
            successful_chunks=successful_count,
            coverage_percent=round(coverage_percent, 1),
        )

        # Phase 4: Generate document metadata from accumulated chunk context (separate LLM call)
        doc_profile, queries, metadata_tokens = await self._generate_document_metadata(
            accumulated_summaries,
            document_metadata,
            metadata_timeout,
        )
        total_tokens += metadata_tokens

        return all_results, doc_profile, queries, total_tokens, coverage_percent
