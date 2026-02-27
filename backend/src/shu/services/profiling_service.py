"""Document and Chunk Profiling Service.

This service generates document and chunk profiles using LLM inference.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Generate chunk profiles (summary, keywords, topics) for batch processing
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
- synopsis: Lead with the most important SPECIFIC facts from across the document. Synthesize, don't just list.{queries_guidelines}
- capability_manifest: Consolidate themes from all chunks into specific, queryable topics."""

# Query synthesis additions - injected into templates when enable_query_synthesis=True
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

    def _build_document_metadata_prompt(self) -> str:
        """Build document metadata synthesis system prompt.

        Injects query synthesis sections when enable_query_synthesis is True.
        """
        if self.settings.enable_query_synthesis:
            queries_guidelines = QUERIES_GUIDELINES_TEMPLATE.format(
                min_queries=self.settings.query_synthesis_min_queries,
                max_queries=self.settings.query_synthesis_max_queries,
            )
            return DOCUMENT_METADATA_PROMPT_TEMPLATE.format(
                queries_json=QUERIES_JSON_ADDITION,
                queries_guidelines=queries_guidelines,
            )
        return DOCUMENT_METADATA_PROMPT_TEMPLATE.format(
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
        # Use batch-relative numbering (1 of N) to avoid confusion with document-level indices
        chunks_text = []
        for i, chunk in enumerate(chunks):
            chunks_text.append(f"### Chunk {i + 1} of {len(chunks)}:\n{chunk.content}")

        user_content = (
            f"Profile the following {len(chunks)} chunks. You MUST return exactly {len(chunks)} profiles.\n\n"
            + "\n\n".join(chunks_text)
            + f"\n\nReturn a JSON array with EXACTLY {len(chunks)} objects in order: "
            + f"[profile for chunk 1, profile for chunk 2, ... profile for chunk {len(chunks)}]. "
            + "Each object must have: summary, keywords, topics."
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
    ) -> tuple[DocumentProfile | None, list[str], int]:
        """Generate document-level metadata from accumulated chunk summaries.

        This is a separate, focused LLM call that synthesizes all chunk summaries
        into document-level metadata (synopsis, capability_manifest, queries).

        Args:
            accumulated_summaries: Summaries from all profiled chunks
            document_metadata: Optional document metadata (title, source, etc.)
            timeout_ms: Timeout for LLM call

        Returns:
            Tuple of (document_profile, synthesized_queries, tokens_used)

        """
        if not accumulated_summaries:
            logger.warning("generate_document_metadata_called_with_no_summaries")
            return None, [], 0

        # Build user message with accumulated summaries
        user_content = "Synthesize document-level metadata from these chunk summaries:\n\n"
        user_content += "\n".join(accumulated_summaries)

        # Build response instructions - only request queries if enabled
        user_content += (
            "\n\nRespond with a JSON object containing:\n"
            "1. 'synopsis': 2-4 sentence summary of the ENTIRE document\n"
            "2. 'document_type': narrative, transactional, technical, or conversational\n"
            "3. 'capability_manifest': what questions this document can answer"
        )
        if self.settings.enable_query_synthesis:
            min_q = self.settings.query_synthesis_min_queries
            max_q = self.settings.query_synthesis_max_queries
            user_content += f"\n4. 'synthesized_queries': {min_q}-{max_q} queries this document can answer"

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

    async def profile_chunks_incremental(
        self,
        chunks: list[ChunkData],
        document_metadata: dict | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[list[ChunkProfileResult], DocumentProfile | None, list[str], int]:
        """Profile chunks incrementally, then generate document metadata separately.

        This method separates chunk profiling from document metadata generation:
        1. All chunk batches are profiled uniformly using _profile_chunk_batch()
        2. A separate focused LLM call generates document-level metadata from accumulated summaries

        This separation improves reliability by reducing task complexity per LLM call.

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
        metadata_timeout = self._resolve_timeout_ms(
            timeout_ms, for_query_synthesis=self.settings.enable_query_synthesis
        )
        batch_size = self.settings.chunk_profiling_batch_size
        all_results: list[ChunkProfileResult] = []
        accumulated_summaries: list[str] = []
        total_tokens = 0

        # Phase 1: Profile ALL chunks in batches (uniform processing, no special cases)
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            batch_results, tokens = await self._profile_chunk_batch(batch, timeout)
            all_results.extend(batch_results)
            total_tokens += tokens

            # Accumulate summaries for document metadata generation
            for result in batch_results:
                if result.success and result.profile.summary:
                    accumulated_summaries.append(f"Chunk {result.chunk_index}: {result.profile.summary}")

        # Phase 2: Generate document metadata from accumulated summaries (separate LLM call)
        doc_profile, queries, metadata_tokens = await self._generate_document_metadata(
            accumulated_summaries,
            document_metadata,
            metadata_timeout,
        )
        total_tokens += metadata_tokens

        return all_results, doc_profile, queries, total_tokens
