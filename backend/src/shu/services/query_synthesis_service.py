"""Query Synthesis Service (SHU-353).

This service generates hypothetical queries that a document can answer.
It is a pure LLM-facing service with no database access. The profiling
orchestrator handles DB operations and calls this service for LLM work.

Key responsibilities:
- Identify main ideas/topics from document synopsis or text
- Generate diverse queries (interrogative, imperative, declarative) for each topic
- Respect configurable min/max query limits
- Enforce token limits on LLM calls
"""

import json
import time

import structlog

from ..core.config import Settings
from ..schemas.query_synthesis import (
    MainIdea,
    QuerySynthesisResult,
    SynthesizedQuery,
)
from ..utils.tokenization import estimate_tokens
from .side_call_service import SideCallResult, SideCallService

logger = structlog.get_logger(__name__)


# System prompt for identifying main ideas
MAIN_IDEAS_SYSTEM_PROMPT = """You are a document analysis assistant. Your task is to identify the main ideas and topics in a document.

Analyze the document and identify 3-10 main ideas or topics that represent what the document is about. Each main idea should be something a user might want to search for or ask about.

Return a JSON array of main ideas:
[
    {"topic": "topic name", "description": "brief description of what this covers"},
    ...
]

Be specific and focus on actionable topics that users would search for. Avoid generic topics like "introduction" or "conclusion"."""

# System prompt for generating queries
QUERY_GENERATION_SYSTEM_PROMPT = """You are a search query generation assistant. Your task is to generate diverse queries that users might use to find information in a document.

For each main idea/topic provided, generate 1-3 queries that a user might use to find this information. Include a mix of:
- **Interrogative**: Questions (e.g., "What is the deadline for the proposal?")
- **Imperative**: Commands (e.g., "Tell me about the vendor selection decision")
- **Declarative**: Keyword searches (e.g., "Azure vs AWS comparison")
- **Interpretive**: Why/how questions (e.g., "Why did we choose vendor A?")
- **Temporal**: Time-based queries (e.g., "When was this policy updated?")
- **Structural**: Content structure queries (e.g., "Key terms of the agreement")

Return a JSON array of queries:
[
    {"query_text": "the query", "query_type": "interrogative|imperative|declarative|interpretive|temporal|structural", "topic_covered": "which topic this addresses"},
    ...
]

Generate natural, realistic queries that users would actually type. Vary the phrasing and query types."""


class QuerySynthesisService:
    """Service for generating hypothetical queries via LLM inference."""

    def __init__(
        self,
        side_call_service: SideCallService,
        settings: Settings,
    ) -> None:
        self.side_call = side_call_service
        self.settings = settings

    def _resolve_timeout_ms(self, timeout_ms: int | None) -> int:
        """Resolve timeout, using settings default if not provided."""
        return timeout_ms or (self.settings.query_synthesis_timeout_seconds * 1000)

    def _validate_input_tokens(self, content: str, context: str) -> SideCallResult | None:
        """Validate that content does not exceed profiling_max_input_tokens.

        Uses the same limit as profiling since both are ingestion-time LLM calls.
        """
        max_tokens = self.settings.profiling_max_input_tokens
        token_count = estimate_tokens(content)
        if token_count > max_tokens:
            error_msg = f"Input exceeds max tokens: {token_count} > {max_tokens}"
            logger.warning(
                "query_synthesis_input_too_large",
                context=context,
                token_count=token_count,
                max_tokens=max_tokens,
            )
            return SideCallResult(
                success=False,
                content="",
                tokens_used=0,
                error_message=error_msg,
            )
        return None

    async def synthesize_queries(
        self,
        document_text: str,
        synopsis: str | None = None,
        capability_manifest: dict | None = None,
        max_queries: int | None = None,
        timeout_ms: int | None = None,
    ) -> QuerySynthesisResult:
        """Generate hypothetical queries for a document.

        Uses a two-stage approach:
        1. Identify main ideas from synopsis (or document text if no synopsis)
        2. Generate queries for each main idea

        Args:
            document_text: Full text of the document
            synopsis: Optional document synopsis (improves quality)
            capability_manifest: Optional capability manifest from profiling
            max_queries: Override for max queries (defaults to settings)
            timeout_ms: Optional timeout override

        Returns:
            QuerySynthesisResult with synthesized queries

        """
        start_time = time.time()
        total_tokens = 0
        timeout = self._resolve_timeout_ms(timeout_ms)
        effective_max = max_queries or self.settings.query_synthesis_max_queries
        effective_min = self.settings.query_synthesis_min_queries

        # Stage 1: Identify main ideas
        # Use synopsis if available (more focused), otherwise use document text
        ideas_input = synopsis if synopsis else document_text[:8000]  # Truncate for ideas extraction

        # If we have capability manifest topics, we can skip idea extraction
        main_ideas: list[MainIdea] = []
        if capability_manifest and capability_manifest.get("answers_questions_about"):
            # Reuse topics from capability manifest
            topics = capability_manifest["answers_questions_about"]
            main_ideas = [MainIdea(topic=t, description="") for t in topics[:10]]
            logger.info(
                "reusing_capability_manifest_topics",
                topic_count=len(main_ideas),
            )
        else:
            # Extract main ideas via LLM
            main_ideas, ideas_tokens, ideas_result = await self._extract_main_ideas(
                ideas_input, timeout
            )
            total_tokens += ideas_tokens

            if not ideas_result.success:
                return QuerySynthesisResult(
                    queries=[],
                    main_ideas=[],
                    success=False,
                    error=f"Failed to extract main ideas: {ideas_result.error_message}",
                    tokens_used=total_tokens,
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        if not main_ideas:
            logger.warning("no_main_ideas_found")
            return QuerySynthesisResult(
                queries=[],
                main_ideas=[],
                success=False,
                error="No main ideas could be identified in the document",
                tokens_used=total_tokens,
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Stage 2: Generate queries for each main idea
        queries, query_tokens, query_result = await self._generate_queries(
            main_ideas,
            document_text,
            effective_max,
            effective_min,
            timeout,
        )
        total_tokens += query_tokens

        if not query_result.success:
            return QuerySynthesisResult(
                queries=[],
                main_ideas=main_ideas,
                success=False,
                error=f"Failed to generate queries: {query_result.error_message}",
                tokens_used=total_tokens,
                duration_ms=int((time.time() - start_time) * 1000),
            )

        duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "query_synthesis_complete",
            query_count=len(queries),
            main_idea_count=len(main_ideas),
            tokens_used=total_tokens,
            duration_ms=duration_ms,
        )

        return QuerySynthesisResult(
            queries=queries,
            main_ideas=main_ideas,
            success=True,
            tokens_used=total_tokens,
            duration_ms=duration_ms,
        )

    async def _extract_main_ideas(
        self,
        text: str,
        timeout_ms: int,
    ) -> tuple[list[MainIdea], int, SideCallResult]:
        """Extract main ideas from document text or synopsis.

        Returns:
            Tuple of (list of MainIdea, tokens used, SideCallResult)

        """
        # Validate input size
        error_result = self._validate_input_tokens(text, "main idea extraction")
        if error_result:
            return [], 0, error_result

        user_content = f"Document content:\n\n{text}"
        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=MAIN_IDEAS_SYSTEM_PROMPT,
            user_id=None,  # System operation
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning(
                "main_idea_extraction_failed",
                error=result.error_message,
            )
            return [], result.tokens_used, result

        # Parse the response
        main_ideas = self._parse_main_ideas(result.content)
        return main_ideas, result.tokens_used, result

    async def _generate_queries(
        self,
        main_ideas: list[MainIdea],
        document_text: str,
        max_queries: int,
        min_queries: int,
        timeout_ms: int,
    ) -> tuple[list[SynthesizedQuery], int, SideCallResult]:
        """Generate queries for the identified main ideas.

        Returns:
            Tuple of (list of SynthesizedQuery, tokens used, SideCallResult)

        """
        # Build the prompt with main ideas and document context
        ideas_json = json.dumps([{"topic": m.topic, "description": m.description} for m in main_ideas])

        # Include a sample of document text for context (truncated)
        doc_sample = document_text[:4000] if len(document_text) > 4000 else document_text

        user_content = f"""Main ideas to generate queries for:
{ideas_json}

Document sample for context:
{doc_sample}

Generate {min_queries}-{max_queries} queries total, covering all main ideas with a mix of query types."""

        # Validate input size
        error_result = self._validate_input_tokens(user_content, "query generation")
        if error_result:
            return [], 0, error_result

        message_sequence = [{"role": "user", "content": user_content}]

        result = await self.side_call.call(
            message_sequence=message_sequence,
            system_prompt=QUERY_GENERATION_SYSTEM_PROMPT,
            user_id=None,  # System operation
            timeout_ms=timeout_ms,
        )

        if not result.success:
            logger.warning(
                "query_generation_failed",
                error=result.error_message,
            )
            return [], result.tokens_used, result

        # Parse the response
        queries = self._parse_queries(result.content)

        # Enforce limits
        if len(queries) > max_queries:
            queries = queries[:max_queries]

        return queries, result.tokens_used, result

    def _parse_main_ideas(self, content: str) -> list[MainIdea]:
        """Parse main ideas from LLM response."""
        try:
            # Handle markdown code blocks
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                # Remove first and last lines (```json and ```)
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("main_ideas_not_a_list", content=content[:200])
                return []

            ideas = []
            for item in data:
                if isinstance(item, dict) and "topic" in item:
                    ideas.append(MainIdea(
                        topic=item.get("topic", ""),
                        description=item.get("description", ""),
                    ))
            return ideas

        except json.JSONDecodeError as e:
            logger.warning("main_ideas_json_parse_error", error=str(e), content=content[:200])
            return []

    def _parse_queries(self, content: str) -> list[SynthesizedQuery]:
        """Parse synthesized queries from LLM response."""
        try:
            # Handle markdown code blocks
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            data = json.loads(text)
            if not isinstance(data, list):
                logger.warning("queries_not_a_list", content=content[:200])
                return []

            queries = []
            for item in data:
                if isinstance(item, dict) and "query_text" in item:
                    queries.append(SynthesizedQuery(
                        query_text=item.get("query_text", ""),
                        query_type=item.get("query_type", "interrogative"),
                        topic_covered=item.get("topic_covered", ""),
                    ))
            return queries

        except json.JSONDecodeError as e:
            logger.warning("queries_json_parse_error", error=str(e), content=content[:200])
            return []
