"""Profile parsing utilities for document and chunk profiling (SHU-343).

Extracted from ProfilingService to adhere to Single Responsibility Principle.
This module handles JSON parsing and validation of LLM profile responses.
"""

import json

import structlog

from ..schemas.profiling import (
    CapabilityManifest,
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentProfile,
    DocumentType,
)

logger = structlog.get_logger(__name__)


class ProfileParser:
    """Parses LLM responses into profile data structures."""

    # Limits for truncation
    MAX_SUMMARY_LENGTH = 500
    MAX_KEYWORDS = 15
    MAX_TOPICS = 10

    def parse_document_profile(self, content: str) -> DocumentProfile | None:
        """Parse LLM response into DocumentProfile.

        Args:
            content: Raw LLM response content

        Returns:
            DocumentProfile if parsing succeeds, None otherwise

        """
        try:
            json_str = self.extract_json(content)
            data = json.loads(json_str)

            # Parse capability manifest
            manifest_data = data.get("capability_manifest", {})
            manifest = CapabilityManifest(
                answers_questions_about=manifest_data.get("answers_questions_about", []),
                provides_information_type=manifest_data.get("provides_information_type", []),
                authority_level=manifest_data.get("authority_level", "secondary"),
                completeness=manifest_data.get("completeness", "partial"),
                question_domains=manifest_data.get("question_domains", []),
            )

            # Parse document type with fallback
            doc_type_str = data.get("document_type", "narrative").lower()
            try:
                doc_type = DocumentType(doc_type_str)
            except ValueError:
                doc_type = DocumentType.NARRATIVE

            return DocumentProfile(
                synopsis=data.get("synopsis", ""),
                document_type=doc_type,
                capability_manifest=manifest,
            )
        except Exception as e:
            logger.warning(
                "failed_to_parse_document_profile",
                error=str(e),
                content=content[:500] if content else "",
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
            logger.warning(f"failed_to_parse_chunk_profiles: {e}")
            return [self.create_failed_chunk_result(c, str(e)) for c in chunks]

    def extract_json(self, content: str) -> str:
        """Extract JSON from LLM response, handling markdown code blocks.

        Args:
            content: Raw response that may contain markdown

        Returns:
            Cleaned JSON string

        """
        content = content.strip()
        # Handle ```json ... ``` blocks
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (code fence)
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            content = "\n".join(lines)
        return content.strip()

    @staticmethod
    def create_failed_chunk_result(chunk: ChunkData, error: str) -> ChunkProfileResult:
        """Factory method for creating failed chunk results.

        Args:
            chunk: The chunk that failed profiling
            error: Error message

        Returns:
            ChunkProfileResult with success=False

        """
        return ChunkProfileResult(
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            profile=ChunkProfile(summary="", keywords=[], topics=[]),
            success=False,
            error=error,
        )
