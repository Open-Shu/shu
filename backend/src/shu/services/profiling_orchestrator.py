"""Document Profiling Orchestrator.

DB-aware layer that coordinates document and chunk profiling. This orchestrator:
- Loads document and chunk records from the database
- Computes document token count for routing decisions
- Manages profiling_status transitions (pending -> in_progress -> complete/failed)
- Delegates to ProfilingService for LLM work
- Persists profile results back to the database
- For small docs: Uses unified profiling (synopsis + chunks + queries in one call)
- For large docs: Uses batch chunk profiling + aggregation
"""

import time

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import Settings
from ..models.document import Document, DocumentChunk, DocumentQuery
from ..schemas.profiling import (
    ChunkData,
    ChunkProfile,
    ChunkProfileResult,
    DocumentProfile,
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

        This is the main entry point called by ingestion integration.

        For small documents (≤ PROFILING_FULL_DOC_MAX_TOKENS):
        - Uses unified profiling: one LLM call produces synopsis, chunk profiles,
          capability manifest, and synthesized queries.

        For large documents:
        - Uses batch chunk profiling followed by aggregation.
        - Synthesized queries are generated during aggregation.

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

            # Prepare chunk data
            chunk_data = [
                ChunkData(
                    chunk_id=c.id,
                    chunk_index=c.chunk_index,
                    content=c.content,
                )
                for c in chunks
            ]

            doc_profile: DocumentProfile | None = None
            chunk_results: list[ChunkProfileResult] = []
            synthesized_queries: list[str] = []

            if mode == ProfilingMode.FULL_DOCUMENT:
                # Unified profiling: one LLM call for everything
                unified_result, llm_result = await self.profiling_service.profile_document_unified(
                    document_text=full_text,
                    chunks=chunk_data,
                    document_metadata={"title": document.title},
                )
                total_tokens += llm_result.tokens_used

                if unified_result:
                    # Convert unified response to standard structures
                    doc_profile = self._unified_to_document_profile(unified_result)
                    chunk_results = self._unified_to_chunk_results(unified_result, chunk_data)
                    synthesized_queries = unified_result.synthesized_queries
            else:
                # Large document: batch chunk profiling + aggregation
                chunk_results, chunk_tokens = await self.profiling_service.profile_chunks(chunk_data)
                total_tokens += chunk_tokens

                # Aggregate from chunk profiles (now includes queries)
                aggregate_result, llm_result = await self.profiling_service.aggregate_chunk_profiles(
                    chunk_profiles=chunk_results,
                    document_metadata={"title": document.title},
                )
                total_tokens += llm_result.tokens_used

                if aggregate_result:
                    doc_profile, synthesized_queries = aggregate_result

            # Persist results
            await self._persist_results(document, chunks, doc_profile, chunk_results)

            # Persist synthesized queries if enabled and we have them
            queries_created = 0
            if doc_profile and self.settings.enable_query_synthesis and synthesized_queries:
                queries_created = await self._persist_queries(document, synthesized_queries)

            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "profiling_complete",
                document_id=document_id,
                profiling_mode=mode.value,
                tokens_used=total_tokens,
                queries_created=queries_created,
                duration_ms=duration_ms,
            )

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

    def _unified_to_document_profile(self, unified) -> DocumentProfile:
        """Convert UnifiedProfilingResponse to DocumentProfile."""
        from ..schemas.profiling import DocumentType

        try:
            doc_type = DocumentType(unified.document_type.lower())
        except ValueError:
            doc_type = DocumentType.NARRATIVE

        return DocumentProfile(
            synopsis=unified.synopsis,
            document_type=doc_type,
            capability_manifest=unified.capability_manifest,
        )

    def _unified_to_chunk_results(self, unified, chunk_data: list[ChunkData]) -> list[ChunkProfileResult]:
        """Convert UnifiedProfilingResponse chunks to ChunkProfileResults."""
        # Build index map from unified chunks
        unified_chunks_by_index = {c.index: c for c in unified.chunks}

        results = []
        for chunk in chunk_data:
            unified_chunk = unified_chunks_by_index.get(chunk.chunk_index)
            if unified_chunk:
                profile = ChunkProfile(
                    one_liner=unified_chunk.one_liner,
                    summary=unified_chunk.summary,
                    keywords=unified_chunk.keywords,
                    topics=unified_chunk.topics,
                )
                results.append(ChunkProfileResult(
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    profile=profile,
                    success=True,
                ))
            else:
                results.append(ChunkProfileResult(
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    profile=ChunkProfile(one_liner="", summary="", keywords=[], topics=[]),
                    success=False,
                    error="No profile in unified response",
                ))
        return results

    async def _persist_queries(self, document: Document, queries: list[str]) -> int:
        """Persist synthesized queries to the database.

        Args:
            document: The document being profiled
            queries: List of query strings

        Returns:
            Number of queries created

        """
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

        if queries_created > 0:
            await self.db.commit()
            logger.info(
                "queries_persisted",
                document_id=document.id,
                queries_created=queries_created,
            )

        return queries_created

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
                    one_liner=result.profile.one_liner,
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
