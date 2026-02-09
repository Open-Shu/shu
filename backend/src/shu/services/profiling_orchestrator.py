"""Document Profiling Orchestrator (SHU-343).

DB-aware layer that coordinates document and chunk profiling. This orchestrator:
- Loads document and chunk records from the database
- Computes document token count for routing decisions
- Manages profiling_status transitions (pending -> in_progress -> complete/failed)
- Delegates to ProfilingService for LLM work
- Persists profile results back to the database

SHU-344 (ingestion integration) calls this orchestrator rather than
re-implementing status or persistence logic.
"""

import time

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings
from ..models.document import Document, DocumentChunk
from ..schemas.profiling import (
    ChunkData,
    ChunkProfileResult,
    ProfilingMode,
    ProfilingResult,
)
from ..utils.tokenization import estimate_tokens
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

        This is the main entry point called by ingestion integration (SHU-344).

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
                profiling_mode=ProfilingMode.FULL_DOCUMENT,
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

            # Get full document text for token counting
            full_text = self._assemble_document_text(chunks)
            doc_tokens = estimate_tokens(full_text)

            # Determine profiling mode
            mode = self._choose_profiling_mode(doc_tokens)

            logger.info(
                "starting_document_profiling",
                document_id=document_id,
                doc_tokens=doc_tokens,
                chunk_count=len(chunks),
                mode=mode.value,
            )

            # Always profile chunks (for retrieval)
            chunk_data = [
                ChunkData(
                    chunk_id=c.id,
                    chunk_index=c.chunk_index,
                    content=c.content,
                )
                for c in chunks
            ]
            chunk_results, chunk_tokens = await self.profiling_service.profile_chunks(chunk_data)
            total_tokens += chunk_tokens

            # Profile document based on mode
            if mode == ProfilingMode.FULL_DOCUMENT:
                doc_profile, llm_result = await self.profiling_service.profile_document(
                    document_text=full_text,
                    document_metadata={"title": document.title},
                )
                total_tokens += llm_result.tokens_used
            else:
                # Aggregate from chunk profiles
                doc_profile, llm_result = await self.profiling_service.aggregate_chunk_profiles(
                    chunk_profiles=chunk_results,
                    document_metadata={"title": document.title},
                )
                total_tokens += llm_result.tokens_used

            # Persist results
            await self._persist_results(document, chunks, doc_profile, chunk_results)

            duration_ms = int((time.time() - start_time) * 1000)

            return ProfilingResult(
                document_id=document_id,
                document_profile=doc_profile,
                chunk_profiles=chunk_results,
                profiling_mode=mode,
                success=doc_profile is not None,
                error=None if doc_profile else "Failed to generate document profile",
                tokens_used=total_tokens,
                duration_ms=duration_ms,
            )

        except Exception as e:
            logger.exception("profiling_failed", document_id=document_id, error=str(e))
            document.mark_profiling_failed(str(e))
            await self.db.commit()

            return ProfilingResult(
                document_id=document_id,
                document_profile=None,
                chunk_profiles=[],
                profiling_mode=ProfilingMode.FULL_DOCUMENT,
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    def _choose_profiling_mode(self, doc_tokens: int) -> ProfilingMode:
        """Determine whether to use full-doc or chunk-aggregation profiling.

        Args:
            doc_tokens: Estimated token count of the full document

        Returns:
            ProfilingMode indicating which approach to use

        """
        if doc_tokens <= self.settings.profiling_full_doc_max_tokens:
            return ProfilingMode.FULL_DOCUMENT
        return ProfilingMode.CHUNK_AGGREGATION

    def _assemble_document_text(self, chunks: list[DocumentChunk]) -> str:
        """Assemble full document text from chunks."""
        return "\n\n".join(c.content for c in chunks)

    async def _persist_results(
        self,
        document: Document,
        chunks: list[DocumentChunk],
        doc_profile,
        chunk_results: list[ChunkProfileResult],
    ) -> None:
        """Persist profiling results to the database.

        Updates Document with profile data and marks complete/failed.
        Updates DocumentChunks with their profiles.
        """
        # Update document profile
        if doc_profile:
            document.mark_profiling_complete(
                synopsis=doc_profile.synopsis,
                document_type=doc_profile.document_type.value,
                capability_manifest=doc_profile.capability_manifest.model_dump(),
            )
        else:
            document.mark_profiling_failed("Failed to generate document profile")

        # Update chunk profiles
        chunk_map = {c.id: c for c in chunks}
        for result in chunk_results:
            chunk = chunk_map.get(result.chunk_id)
            if chunk and result.success:
                chunk.set_profile(
                    summary=result.profile.summary,
                    keywords=result.profile.keywords,
                    topics=result.profile.topics,
                )

        await self.db.commit()

        logger.info(
            "profiling_results_persisted",
            document_id=document.id,
            doc_profile_success=doc_profile is not None,
            chunks_profiled=sum(1 for r in chunk_results if r.success),
            chunks_failed=sum(1 for r in chunk_results if not r.success),
        )

    async def is_profiling_enabled(self) -> bool:
        """Check if document profiling is enabled."""
        return self.settings.enable_document_profiling

    async def get_profiling_status(self, document_id: str) -> str | None:
        """Get the current profiling status of a document."""
        document = await self.db.get(Document, document_id)
        return document.profiling_status if document else None
