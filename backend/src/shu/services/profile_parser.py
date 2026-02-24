"""Profile parsing utilities for document and chunk profiling.

Extracted from ProfilingService to adhere to Single Responsibility Principle.
This module handles JSON parsing and validation of LLM profile responses.
"""

import json
import re

import structlog

from ..schemas.profiling import (
    CapabilityManifest,
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    FinalBatchResponse,
    UnifiedChunkProfile,
    UnifiedProfilingResponse,
)

logger = structlog.get_logger(__name__)


class ProfileParser:
    """Parses LLM responses into profile data structures."""

    # Limits for truncation
    MAX_ONE_LINER_LENGTH = 100
    MAX_SUMMARY_LENGTH = 500
    MAX_KEYWORDS = 15
    MAX_TOPICS = 10
    DEFAULT_MAX_QUERIES = 20

    def __init__(self, max_queries: int | None = None) -> None:
        """Initialize parser with configurable limits.

        Args:
            max_queries: Maximum number of synthesized queries to keep (default: 20)

        """
        self.max_queries = max_queries if max_queries is not None else self.DEFAULT_MAX_QUERIES

    def _parse_capability_manifest(self, data: dict) -> CapabilityManifest:
        """Parse capability manifest from JSON data.

        Args:
            data: Raw JSON data containing capability_manifest key

        Returns:
            CapabilityManifest with parsed or default values

        """
        manifest_data = data.get("capability_manifest", {})
        return CapabilityManifest(
            answers_questions_about=manifest_data.get("answers_questions_about", []),
            provides_information_type=manifest_data.get("provides_information_type", []),
            authority_level=manifest_data.get("authority_level", "secondary"),
            completeness=manifest_data.get("completeness", "partial"),
            question_domains=manifest_data.get("question_domains", []),
        )

    def _parse_chunks(self, chunks_data: list) -> list[UnifiedChunkProfile]:
        """Parse chunk profiles from JSON data.

        Args:
            chunks_data: List of chunk dictionaries from JSON

        Returns:
            List of UnifiedChunkProfile with truncated fields

        """
        chunks = []
        for pos, chunk_data in enumerate(chunks_data):
            raw_index = chunk_data.get("index")
            try:
                index = int(raw_index) if raw_index is not None else pos
            except (ValueError, TypeError):
                index = pos
            chunks.append(
                UnifiedChunkProfile(
                    index=index,
                    one_liner=chunk_data.get("one_liner", "")[: self.MAX_ONE_LINER_LENGTH],
                    summary=chunk_data.get("summary", "")[: self.MAX_SUMMARY_LENGTH],
                    keywords=chunk_data.get("keywords", [])[: self.MAX_KEYWORDS],
                    topics=chunk_data.get("topics", [])[: self.MAX_TOPICS],
                )
            )
        return chunks

    def _parse_synthesized_queries(self, queries: list) -> list[str]:
        """Parse synthesized queries, handling both string and object formats.

        Args:
            queries: List of queries (strings or dicts with query_text)

        Returns:
            List of non-empty query strings, capped at configured max_queries

        """
        if not queries:
            return []
        result = []
        for q in queries:
            if q is None:
                continue
            if isinstance(q, dict):
                value = q.get("query_text")
                if value is None:
                    continue
                text = str(value).strip()
            else:
                text = str(q).strip()
            if text:
                result.append(text)
        return result[: self.max_queries]

    def parse_unified_response(self, content: str) -> UnifiedProfilingResponse | None:
        """Parse unified profiling LLM response.

        Args:
            content: Raw LLM response content

        Returns:
            UnifiedProfilingResponse if parsing succeeds, None otherwise

        """
        try:
            json_str = self.extract_json(content)
            data = json.loads(json_str)

            return UnifiedProfilingResponse(
                synopsis=data.get("synopsis", ""),
                chunks=self._parse_chunks(data.get("chunks", [])),
                document_type=(data.get("document_type") or "narrative").lower(),
                capability_manifest=self._parse_capability_manifest(data),
                synthesized_queries=self._parse_synthesized_queries(data.get("synthesized_queries", [])),
            )
        except Exception as e:
            logger.warning(
                "failed_to_parse_unified_response",
                error=str(e),
                content_length=len(content) if content else 0,
            )
            return None

    def parse_final_batch_response(self, content: str) -> FinalBatchResponse | None:
        """Parse final batch profiling LLM response.

        The final batch includes both chunk profiles AND document-level metadata.

        Args:
            content: Raw LLM response content

        Returns:
            FinalBatchResponse if parsing succeeds, None otherwise

        """
        try:
            json_str = self.extract_json(content)
            data = json.loads(json_str)

            return FinalBatchResponse(
                chunks=self._parse_chunks(data.get("chunks", [])),
                synopsis=data.get("synopsis", ""),
                document_type=(data.get("document_type") or "narrative").lower(),
                capability_manifest=self._parse_capability_manifest(data),
                synthesized_queries=self._parse_synthesized_queries(data.get("synthesized_queries", [])),
            )
        except Exception as e:
            logger.warning(
                "failed_to_parse_final_batch_response",
                error=str(e),
                content_length=len(content) if content else 0,
            )
            return None

    def parse_chunk_profiles(self, content: str, chunks: list[ChunkData]) -> list[ChunkProfileResult]:
        """Parse LLM response into ChunkProfileResults.

        Args:
            content: Raw LLM response content
            chunks: Original chunks for ID/index mapping

        Returns:
            List of ChunkProfileResult (one per input chunk)

        """
        try:
            json_str = self.extract_json(content)
            data = json.loads(json_str)

            # Handle single object vs array
            if isinstance(data, dict):
                data = [data]

            results = []
            for i, chunk in enumerate(chunks):
                if i < len(data):
                    profile_data = data[i]
                    profile = ChunkProfile(
                        one_liner=profile_data.get("one_liner", "")[: self.MAX_ONE_LINER_LENGTH],
                        summary=profile_data.get("summary", "")[: self.MAX_SUMMARY_LENGTH],
                        keywords=profile_data.get("keywords", [])[: self.MAX_KEYWORDS],
                        topics=profile_data.get("topics", [])[: self.MAX_TOPICS],
                    )
                    results.append(
                        ChunkProfileResult(
                            chunk_id=chunk.chunk_id,
                            chunk_index=chunk.chunk_index,
                            profile=profile,
                            success=True,
                        )
                    )
                else:
                    # Missing profile for this chunk
                    results.append(self.create_failed_chunk_result(chunk, "No profile in response"))
            return results

        except Exception as e:
            logger.warning("failed_to_parse_chunk_profiles", error=str(e))
            return [self.create_failed_chunk_result(c, str(e)) for c in chunks]

    def extract_json(self, content: str) -> str:
        """Extract JSON from LLM response, handling markdown code blocks.

        Args:
            content: Raw response that may contain markdown

        Returns:
            Cleaned JSON string

        """
        content = content.strip()
        # Scan for a fenced code block anywhere in the response
        # Allow optional whitespace/newline after the opening fence
        match = re.search(r"```(?:\w+)?\s*(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return content.strip()

    @staticmethod
    def create_failed_chunk_result(chunk: ChunkData, error: str) -> ChunkProfileResult:
        """Create failed chunk results.

        Args:
            chunk: The chunk that failed profiling
            error: Error message

        Returns:
            ChunkProfileResult with success=False

        """
        return ChunkProfileResult(
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            profile=ChunkProfile(one_liner="", summary="", keywords=[], topics=[]),
            success=False,
            error=error,
        )
