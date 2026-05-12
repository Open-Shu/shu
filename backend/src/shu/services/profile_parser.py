"""Profile parsing utilities for document and chunk profiling.

Extracted from ProfilingService to adhere to Single Responsibility Principle.
This module handles JSON parsing and validation of LLM profile responses.
"""

import json
import re

from shu.core.logging import get_logger

from ..schemas.profiling import (
    CapabilityManifest,
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentMetadataResponse,
    DocumentType,
    SynthesizedQuery,
)

logger = get_logger(__name__)


class ProfileParser:
    """Parses LLM responses into profile data structures."""

    # Limits for truncation
    MAX_SUMMARY_LENGTH = 500
    MAX_TOPICS = 10
    DEFAULT_MAX_TOTAL_QUERIES = 100

    def __init__(self, max_total_queries: int | None = None) -> None:
        """Initialize parser with configurable limits.

        Args:
            max_total_queries: Maximum total queries per document (default: 100)

        """
        self.max_total_queries = max_total_queries if max_total_queries is not None else self.DEFAULT_MAX_TOTAL_QUERIES

    @staticmethod
    def _coerce_string(value: str | None) -> str:
        """Safely coerce a value to string, handling None.

        Args:
            value: Value that may be a string or None

        Returns:
            Empty string if None, otherwise str(value)

        """
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _coerce_list(value: list | None) -> list:
        """Safely coerce a value to list, handling None and non-list types.

        Args:
            value: Value that may be a list, None, or other type

        Returns:
            Empty list if None or not a list, otherwise the list

        """
        if value is None or not isinstance(value, list):
            return []
        return value

    def _parse_capability_manifest(self, data: dict) -> CapabilityManifest:
        """Parse capability manifest from JSON data.

        Handles None values gracefully by coercing to appropriate defaults.

        Args:
            data: Raw JSON data containing capability_manifest key

        Returns:
            CapabilityManifest with parsed or default values

        """
        manifest_data = data.get("capability_manifest") or {}
        return CapabilityManifest(
            answers_questions_about=self._coerce_list(manifest_data.get("answers_questions_about")),
            provides_information_type=self._coerce_list(manifest_data.get("provides_information_type")),
            authority_level=self._coerce_string(manifest_data.get("authority_level")) or "secondary",
            completeness=self._coerce_string(manifest_data.get("completeness")) or "partial",
            question_domains=self._coerce_list(manifest_data.get("question_domains")),
        )

    def _parse_chunk_queries(self, chunk_queries: list | None) -> list[SynthesizedQuery]:  # noqa: PLR0912
        """Parse per-chunk queries into a list of SynthesizedQuery with chunk provenance.

        Handles the current chunk_queries structure:
        [{"chunk_index": 0, "queries": [...]}, ...]

        Also supports legacy formats for backward compatibility:
        - {"chunk_index": N, "high": [...], "conversational": [...]}
        - {"chunk_index": N, "high": [...], "medium": [...]}
        - Flat list of strings
        - [{"query_text": "..."}]

        Args:
            chunk_queries: List of chunk query objects or legacy flat query list

        Returns:
            List of SynthesizedQuery with chunk_index provenance, capped at max_total_queries

        """
        if not chunk_queries:
            return []

        result: list[SynthesizedQuery] = []

        for item in chunk_queries:
            if item is None:
                continue

            if isinstance(item, dict):
                chunk_index = item.get("chunk_index")
                if isinstance(chunk_index, int):
                    pass  # keep it
                else:
                    chunk_index = None

                # Current format: {"chunk_index": N, "queries": [...]}
                if "queries" in item:
                    for query in self._coerce_list(item.get("queries")):
                        text = str(query).strip() if query else ""
                        if text:
                            result.append(SynthesizedQuery(query_text=text, chunk_index=chunk_index))

                # Legacy format: {"chunk_index": N, "high": [...], "conversational": [...]}
                elif "high" in item or "conversational" in item or "medium" in item:
                    for query in self._coerce_list(item.get("high")):
                        text = str(query).strip() if query else ""
                        if text:
                            result.append(SynthesizedQuery(query_text=text, chunk_index=chunk_index))
                    casual_queries = item.get("conversational") or item.get("medium")
                    for query in self._coerce_list(casual_queries):
                        text = str(query).strip() if query else ""
                        if text:
                            result.append(SynthesizedQuery(query_text=text, chunk_index=chunk_index))

                # Legacy format: {"query_text": "..."}
                elif "query_text" in item:
                    text = str(item["query_text"]).strip()
                    if text:
                        result.append(SynthesizedQuery(query_text=text, chunk_index=chunk_index))

            else:
                # Legacy format: plain string (no chunk provenance)
                text = str(item).strip()
                if text:
                    result.append(SynthesizedQuery(query_text=text, chunk_index=None))

        return result[: self.max_total_queries]

    def parse_document_metadata_response(self, content: str) -> DocumentMetadataResponse | None:
        """Parse document metadata synthesis LLM response.

        This parses responses from the dedicated document metadata generation call,
        which contains only document-level metadata (no chunk profiles).

        Args:
            content: Raw LLM response content

        Returns:
            DocumentMetadataResponse if parsing succeeds, None otherwise

        """
        try:
            json_str = self.extract_json(content)
            data = json.loads(json_str)

            # Convert document_type to enum with fallback (coerce non-strings safely)
            doc_type_str = (self._coerce_string(data.get("document_type")) or "narrative").lower()
            try:
                doc_type = DocumentType(doc_type_str)
            except ValueError:
                doc_type = DocumentType.NARRATIVE

            # Coerce and validate synopsis
            synopsis = self._coerce_string(data.get("synopsis"))
            if not synopsis.strip():
                logger.warning(
                    "document_metadata_response_empty_synopsis",
                    extra={"content_length": len(content) if content else 0},
                )

            # Parse queries - prefer new chunk_queries format, fallback to legacy synthesized_queries
            queries_data = data.get("chunk_queries") or data.get("synthesized_queries")
            parsed_queries = self._parse_chunk_queries(self._coerce_list(queries_data))

            return DocumentMetadataResponse(
                synopsis=synopsis,
                document_type=doc_type,
                capability_manifest=self._parse_capability_manifest(data),
                synthesized_queries=parsed_queries,
            )
        except Exception as e:
            logger.warning(
                "failed_to_parse_document_metadata_response",
                extra={"error": str(e), "content_length": len(content) if content else 0},
            )
            return None

    def parse_chunk_profiles(self, content: str, chunks: list[ChunkData]) -> list[ChunkProfileResult]:
        """Parse LLM response into ChunkProfileResults.

        Handles None values and non-list types gracefully by coercing to
        appropriate defaults before truncation.

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

            # Log mismatch between chunks requested and profiles returned
            if len(data) != len(chunks):
                logger.warning(
                    "chunk_profile_count_mismatch",
                    extra={
                        "chunks_requested": len(chunks),
                        "profiles_returned": len(data),
                        "content_length": len(content),
                    },
                )

            results = []
            for i, chunk in enumerate(chunks):
                if i < len(data):
                    profile_data = data[i]
                    # Coerce values to handle LLM returning null instead of missing keys
                    summary = self._coerce_string(profile_data.get("summary"))
                    topics = self._coerce_list(profile_data.get("topics"))
                    profile = ChunkProfile(
                        summary=summary[: self.MAX_SUMMARY_LENGTH],
                        topics=topics[: self.MAX_TOPICS],
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
            logger.warning("failed_to_parse_chunk_profiles", extra={"error": str(e)})
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
            profile=ChunkProfile(summary="", topics=[]),
            success=False,
            error=error,
        )
