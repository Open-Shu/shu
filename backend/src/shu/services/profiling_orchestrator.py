"""Document Profiling Orchestrator.

DB-aware layer that coordinates document and chunk profiling. This orchestrator:
- Loads document and chunk records from the database
- Manages profiling_status transitions (pending -> in_progress -> complete/failed)
- Delegates to ProfilingService for LLM work
- Persists profile results back to the database
- Uses two-phase profiling: all chunks profiled first, then document metadata generated separately
"""

import time

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings
from ..models.document import Document, DocumentChunk, DocumentQuery
from ..schemas.profiling import (
    ChunkData,
    ChunkProfileResult,
    ProfilingMode,
    ProfilingResult,
)
from .profiling_service import ProfilingService
from .side_call_service import SideCallService

logger = structlog.get_logger(__name__)


class ProfilingOrchestrator:
    """Orchestrates document profiling with DB awareness."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        side_call_service: SideCallService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.profiling_service = ProfilingService(side_call_service, settings)

    async def run_for_document(self, document_id: str) -> ProfilingResult:
        """Run profiling for a document and its chunks.

        This is the main entry point called by ingestion integration.

        Uses two-phase profiling: all chunks are profiled first in batches,
        then document metadata is generated from accumulated summaries in a separate call.

        Args:
            document_id: ID of the document to profile

        Returns:
            ProfilingResult with document and chunk profiles

        """
        start_time = time.time()
        total_tokens = 0

        # Load document
        document = await self.db.get(Document, document_id)
        if not document:
            logger.warning("document_not_found_for_profiling", document_id=document_id)
            return ProfilingResult(
                document_id=document_id,
                document_profile=None,
                chunk_profiles=[],
                profiling_mode=ProfilingMode.CHUNK_AGGREGATION,
                success=False,
                error=f"Document {document_id} not found",
            )

        # Mark profiling in progress
        document.mark_profiling_started()
        await self.db.commit()

        try:
            # Load chunks
            stmt = (
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
            )
            result = await self.db.execute(stmt)
            chunks = list(result.scalars().all())

            logger.info(
                "starting_document_profiling",
                document_id=document_id,
                chunk_count=len(chunks),
            )

            # Prepare chunk data
            chunk_data = [
                ChunkData(
                    chunk_id=c.id,
                    chunk_index=c.chunk_index,
                    content=c.content,
                )
                for c in chunks
            ]

            # Two-phase profiling with retry: all chunks profiled first,
            # failed chunks retried with context, then document metadata generated
            (
                chunk_results,
                doc_profile,
                synthesized_queries,
                tokens,
                coverage_percent,
            ) = await self.profiling_service.profile_chunks_incremental(
                chunks=chunk_data,
                document_metadata={"title": document.title},
            )
            total_tokens += tokens

            # Persist results (including coverage)
            await self._persist_results(document, chunks, doc_profile, chunk_results, coverage_percent)

            # Persist synthesized queries if enabled (even if empty, to delete stale queries on re-profile)
            # Isolated from main try block so query failures don't mark profiling as failed
            queries_created = 0
            if doc_profile and self.settings.enable_query_synthesis:
                try:
                    queries_created = await self._persist_queries(document, synthesized_queries)
                except Exception as e:
                    logger.warning("query_persistence_failed", document_id=document_id, error=str(e))

            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "profiling_complete",
                document_id=document_id,
                tokens_used=total_tokens,
                queries_created=queries_created,
                coverage_percent=round(coverage_percent, 1),
                duration_ms=duration_ms,
            )

            return ProfilingResult(
                document_id=document_id,
                document_profile=doc_profile,
                chunk_profiles=chunk_results,
                profiling_mode=ProfilingMode.CHUNK_AGGREGATION,
                success=doc_profile is not None,
                error=None if doc_profile else "Failed to generate document profile",
                tokens_used=total_tokens,
                duration_ms=duration_ms,
                chunk_coverage_percent=coverage_percent,
            )

        except Exception as e:
            logger.exception("profiling_failed", document_id=document_id, error=str(e))
            document.mark_profiling_failed(str(e))
            await self.db.commit()

            return ProfilingResult(
                document_id=document_id,
                document_profile=None,
                chunk_profiles=[],
                profiling_mode=ProfilingMode.CHUNK_AGGREGATION,
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _persist_queries(self, document: Document, queries: list[str]) -> int:
        """Persist synthesized queries to the database.

        Deletes any existing queries for this document before creating new ones
        to handle re-profiling scenarios.

        Args:
            document: The document being profiled
            queries: List of query strings

        Returns:
            Number of queries created

        """
        # Delete existing queries for this document (re-profiling case)
        await self.db.execute(delete(DocumentQuery).where(DocumentQuery.document_id == document.id))

        queries_created = 0
        for query_text in queries:
            if query_text.strip():  # Skip empty queries
                doc_query = DocumentQuery.create_for_document(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    query_text=query_text.strip(),
                )
                self.db.add(doc_query)
                queries_created += 1

        # Always commit to flush the DELETE even if no new queries were created
        await self.db.commit()
        if queries_created > 0:
            logger.info(
                "queries_persisted",
                document_id=document.id,
                queries_created=queries_created,
            )

        return queries_created

    async def _persist_results(
        self,
        document: Document,
        chunks: list[DocumentChunk],
        doc_profile,
        chunk_results: list[ChunkProfileResult],
        coverage_percent: float = 100.0,
    ) -> None:
        """Persist profiling results to the database.

        Updates Document with profile data and marks complete/failed.
        Updates DocumentChunks with their profiles.

        Args:
            document: The document being profiled
            chunks: List of DocumentChunk records
            doc_profile: DocumentProfile or None if generation failed
            chunk_results: Results for each chunk
            coverage_percent: Percentage of chunks successfully profiled

        """
        # Update document profile
        if doc_profile:
            document.mark_profiling_complete(
                synopsis=doc_profile.synopsis,
                document_type=doc_profile.document_type.value,
                capability_manifest=doc_profile.capability_manifest.model_dump(),
                coverage_percent=coverage_percent,
            )
        else:
            document.mark_profiling_failed("Failed to generate document profile")

        # Update chunk profiles
        # Only persist profiles for chunks that succeeded AND have non-empty summaries
        # (mirrors the failure detection in ProfilingService._is_chunk_profile_failed)
        chunk_map = {c.id: c for c in chunks}
        chunks_persisted = 0
        for result in chunk_results:
            chunk = chunk_map.get(result.chunk_id)
            if (
                chunk
                and result.success
                and result.profile
                and result.profile.summary
                and result.profile.summary.strip()
            ):
                chunk.set_profile(
                    summary=result.profile.summary,
                    keywords=result.profile.keywords,
                    topics=result.profile.topics,
                )
                chunks_persisted += 1

        await self.db.commit()

        logger.info(
            "profiling_results_persisted",
            document_id=document.id,
            doc_profile_success=doc_profile is not None,
            chunks_persisted=chunks_persisted,
            chunks_failed=len(chunk_results) - chunks_persisted,
            coverage_percent=round(coverage_percent, 1),
        )

    async def is_profiling_enabled(self) -> bool:
        """Check if document profiling is enabled."""
        return self.settings.enable_document_profiling

    async def get_profiling_status(self, document_id: str) -> str | None:
        """Get the current profiling status of a document."""
        document = await self.db.get(Document, document_id)
        return document.profiling_status if document else None
