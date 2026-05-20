"""Document Profiling Orchestrator.

DB-aware layer that coordinates document and chunk profiling. This orchestrator:
- Loads document and chunk records from the database
- Manages profiling_status transitions (pending -> in_progress -> complete/failed)
- Delegates to ProfilingService for LLM work
- Persists profile results back to the database
- Uses two-phase profiling: all chunks profiled first, then document metadata generated separately

Artifact embedding (synopsis, chunk summaries, synthesized queries) is handled
separately by embed_profile_artifacts(), called from the INGESTION_EMBED worker
job handler after profiling completes. See SHU-637.
"""

import time

from sqlalchemy import delete, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

from ..core.config import Settings
from ..core.embedding_service import get_embedding_service
from ..core.vector_store import VectorEntry, get_vector_store
from ..models.document import Document, DocumentChunk, DocumentQuery
from ..schemas.profiling import (
    ChunkData,
    ProfilingMode,
    ProfilingResult,
    SynthesizedQuery,
)
from .profiling_service import ProfilingService
from .side_call_service import SideCallService

logger = get_logger(__name__)


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

    async def run_for_document(self, document_id: str, *, user_id: str | None = None) -> ProfilingResult:
        """Run profiling for a document and its chunks.

        This is the main entry point called by ingestion integration.

        Uses two-phase profiling: all chunks are profiled first in batches,
        then document metadata is generated from accumulated summaries in a separate call.

        Args:
            document_id: ID of the document to profile
            user_id: Originating ingestion user. Currently accepted for API
                parity but NOT yet threaded into the internal side-call chain,
                so side-call llm_usage rows emitted during profiling still land
                with NULL user_id. Follow-up ticket will plumb this through
                ProfilingService and its SideCallService invocations.

        Returns:
            ProfilingResult with document and chunk profiles

        """
        start_time = time.time()
        total_tokens = 0

        # Load document
        document = await self.db.get(Document, document_id)
        if not document:
            logger.warning("document_not_found_for_profiling", extra={"document_id": document_id})
            return ProfilingResult(
                document_id=document_id,
                document_profile=None,
                chunk_profiles=[],
                profiling_mode=ProfilingMode.CHUNK_AGGREGATION,
                success=False,
                skipped=True,
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
                extra={"document_id": document_id, "chunk_count": len(chunks)},
            )

            # Extract the only chunk data we need past Phase 1 — (index → id)
            # mapping for query provenance resolution in _persist_queries. The
            # ORM chunk list itself, plus every row's ``content`` column (the
            # largest field by far), can be released before the long-running
            # Phase 2 LLM call. Observed: 200 chunks x ~2 KB content each hold
            # ~400 KB of Python strings + ORM wrappers for the entire Phase 2
            # duration (10-30s), multiplied by SHU_PROFILING_MAX_CONCURRENT_TASKS.
            chunk_count = len(chunks)
            chunk_index_to_id: dict[int, str] = {
                c.chunk_index: c.id  # type: ignore[misc]
                for c in chunks
            }

            # Phase 1: Profile chunks in batches, skipping batches where all
            # chunks already have summaries.  Commit after each batch so work
            # is not lost if a later phase fails.
            (
                phase1_tokens,
                chunks_skipped,
                chunks_profiled,
            ) = await self._profile_chunks_incrementally(chunks, document_id)
            total_tokens += phase1_tokens

            # Release ORM chunks from the SQLAlchemy identity map before Phase
            # 2 (SHU-731). The identity map otherwise pins every loaded row for
            # the life of the session, and a profiling run can span tens of
            # seconds under OpenRouter latency. Per-object expunge keeps the
            # ``document`` row attached so downstream mark_* calls still work.
            for _chunk in chunks:
                try:
                    self.db.expunge(_chunk)
                except InvalidRequestError:  # pragma: no cover — already detached
                    pass
            chunks.clear()

            # Phase 2: Generate document metadata from DB-sourced summaries.
            # Re-read summaries from the database (not in-memory results) so
            # that retries after a metadata-only failure don't need to
            # re-profile any chunks. Column-only query — does NOT re-populate
            # the identity map with full ORM rows (SHU-731).
            accumulated_summaries = await self._load_chunk_summaries(document_id)

            successful_count = len(accumulated_summaries)
            coverage_percent = (successful_count / chunk_count) * 100 if chunk_count else 100.0

            logger.info(
                "chunk_profiling_coverage",
                extra={
                    "document_id": document_id,
                    "total_chunks": chunk_count,
                    "successful_chunks": successful_count,
                    "chunks_skipped": chunks_skipped,
                    "chunks_profiled": chunks_profiled,
                    "coverage_percent": round(coverage_percent, 1),
                },
            )

            doc_profile, synthesized_queries, metadata_tokens = await self.profiling_service.generate_document_metadata(
                accumulated_summaries,
                document_metadata={"title": document.title},
            )
            total_tokens += metadata_tokens

            if not doc_profile:
                logger.warning(
                    "document_metadata_generation_returned_none",
                    extra={
                        "document_id": document_id,
                        "summary_count": len(accumulated_summaries),
                        "metadata_tokens": metadata_tokens,
                        "total_chunks": chunk_count,
                    },
                )

            # Persist document-level profile
            await self._persist_document_profile(document, doc_profile, coverage_percent)

            # Persist synthesized queries (even if empty, to delete stale queries on re-profile)
            # Isolated from main try block so query failures don't mark profiling as failed
            queries_created = 0
            if doc_profile:
                try:
                    queries_created = await self._persist_queries(document, synthesized_queries, chunk_index_to_id)
                except Exception as e:
                    await self.db.rollback()
                    logger.warning("query_persistence_failed", extra={"document_id": document_id, "error": str(e)})

            duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                "profiling_complete",
                extra={
                    "document_id": document_id,
                    "tokens_used": total_tokens,
                    "queries_created": queries_created,
                    "coverage_percent": round(coverage_percent, 1),
                    "duration_ms": duration_ms,
                },
            )

            return ProfilingResult(
                document_id=document_id,
                document_profile=doc_profile,
                # SHU-731: no longer surface per-chunk results to callers.
                # The worker never reads this field; accumulating N chunk
                # results across the Phase 2 LLM call was pure retention.
                chunk_profiles=[],
                profiling_mode=ProfilingMode.CHUNK_AGGREGATION,
                success=doc_profile is not None,
                error=None if doc_profile else "Failed to generate document profile",
                tokens_used=total_tokens,
                duration_ms=duration_ms,
                chunk_coverage_percent=coverage_percent,
            )

        except Exception as e:
            logger.exception("profiling_failed", extra={"document_id": document_id, "error": str(e)})
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

    async def _profile_chunks_incrementally(
        self,
        chunks: list[DocumentChunk],
        document_id: str,
    ) -> tuple[int, int, int]:
        """Profile chunks in batches, skipping batches where all chunks are already profiled.

        Walks through chunks sequentially in batch-sized windows. If every chunk
        in the batch already has a non-empty summary, skips it. If any chunk in
        the batch is missing a summary, sends the whole batch (preserving
        sequential context for the LLM). Commits results after each batch.

        Args:
            chunks: All document chunks in order.
            document_id: Document ID for logging.

        Returns:
            Tuple of (total tokens, chunks skipped, chunks profiled).

        """
        batch_size = self.settings.chunk_profiling_batch_size
        total_tokens = 0
        chunks_skipped = 0
        chunks_profiled = 0

        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i : i + batch_size]

            # Check if all chunks in this batch already have summaries
            all_have_summaries = all(c.summary and c.summary.strip() for c in batch_chunks)

            if all_have_summaries:
                chunks_skipped += len(batch_chunks)
                continue

            # At least one chunk needs profiling — send the full batch
            chunk_data = [
                ChunkData(
                    chunk_id=c.id,
                    chunk_index=c.chunk_index,
                    content=c.content,
                )
                for c in batch_chunks
            ]

            batch_results, tokens = await self.profiling_service.profile_chunk_batch(
                chunk_data,
            )
            total_tokens += tokens

            # Persist this batch immediately
            chunk_map = {c.id: c for c in batch_chunks}
            for result in batch_results:
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
                        topics=result.profile.topics,
                    )
            await self.db.commit()
            chunks_profiled += len(batch_chunks)

            logger.debug(
                "chunk_batch_committed",
                extra={
                    "document_id": document_id,
                    "batch_start": i,
                    "batch_size": len(batch_chunks),
                    "tokens": tokens,
                },
            )

        return total_tokens, chunks_skipped, chunks_profiled

    async def _load_chunk_summaries(self, document_id: str) -> list[str]:
        """Load committed chunk summaries from the database for metadata generation.

        Returns accumulated summary strings in chunk_index order, matching the
        format expected by generate_document_metadata().

        Uses a column-only query (chunk_index/summary/topics) to avoid loading
        the full DocumentChunk row — crucially skipping the ``content`` column
        which is by far the largest — and to avoid populating the SQLAlchemy
        identity map with ORM rows that Phase 2 never otherwise needs (SHU-731).
        """
        stmt = (
            select(  # type: ignore[call-overload]
                DocumentChunk.chunk_index,
                DocumentChunk.summary,
                DocumentChunk.topics,
            )
            .where(DocumentChunk.document_id == document_id)
            .where(DocumentChunk.summary.isnot(None))  # type: ignore[union-attr]
            .order_by(DocumentChunk.chunk_index)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        accumulated = []
        for row in rows:
            # Row may be a SQLAlchemy Row (.chunk_index/.summary/.topics) when
            # executed against a real engine, or a 3-tuple when tests return a
            # plain iterable. Support both.
            if hasattr(row, "chunk_index"):
                chunk_index = row.chunk_index
                summary = row.summary or ""
                topics = row.topics if isinstance(row.topics, list) else []
            else:
                chunk_index, summary, topics = row
                summary = summary or ""
                topics = topics if isinstance(topics, list) else []
            if not summary.strip():
                continue
            entry = f"Chunk {chunk_index}: {summary}"
            if topics:
                entry += f"\n  Topics: {', '.join(topics)}"
            accumulated.append(entry)

        return accumulated

    async def _persist_document_profile(
        self,
        document: Document,
        doc_profile,
        coverage_percent: float,
    ) -> None:
        """Persist document-level profile (synopsis, type, manifest).

        Separated from chunk persistence so that chunk results survive
        even if metadata generation fails.

        """
        if doc_profile:
            document.mark_profiling_complete(
                synopsis=doc_profile.synopsis,
                document_type=doc_profile.document_type.value,
                capability_manifest=doc_profile.capability_manifest.model_dump(),
                coverage_percent=coverage_percent,
            )
        else:
            document.mark_profiling_failed("Failed to generate document profile")

        await self.db.commit()

        logger.info(
            "document_profile_persisted",
            extra={
                "document_id": document.id,
                "doc_profile_success": doc_profile is not None,
                "coverage_percent": round(coverage_percent, 1),
            },
        )

    async def _persist_queries(
        self,
        document: Document,
        queries: list[SynthesizedQuery],
        chunk_index_to_id: dict[int, str] | list[DocumentChunk],
    ) -> int:
        """Persist synthesized queries to the database.

        Deletes any existing queries for this document before creating new ones
        to handle re-profiling scenarios. Resolves chunk_index from the LLM
        response to chunk_id for direct FK linkage (SHU-645).

        Args:
            document: The document being profiled
            queries: List of SynthesizedQuery with optional chunk_index provenance
            chunk_index_to_id: Pre-computed ``{chunk_index: chunk_id}`` map, or
                (legacy) a list of DocumentChunk ORM rows from which the map is
                derived. The map form lets callers release the ORM chunk list
                before calling into this method (SHU-731).

        Returns:
            Number of queries created

        """
        # Delete existing queries for this document (re-profiling case)
        await self.db.execute(delete(DocumentQuery).where(DocumentQuery.document_id == document.id))

        # Accept either the map or a chunk list for callers that haven't been
        # migrated (e.g. existing unit tests).
        if isinstance(chunk_index_to_id, dict):
            index_map: dict[int, str] = chunk_index_to_id
        else:
            index_map = {
                c.chunk_index: c.id  # type: ignore[misc]
                for c in chunk_index_to_id
            }

        queries_created = 0
        for sq in queries:
            if sq.query_text.strip():
                source_chunk_id = index_map.get(sq.chunk_index) if sq.chunk_index is not None else None
                doc_query = DocumentQuery.create_for_document(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    query_text=sq.query_text.strip(),
                    source_chunk_id=source_chunk_id,
                )
                self.db.add(doc_query)
                queries_created += 1

        # Always commit to flush the DELETE even if no new queries were created
        await self.db.commit()
        if queries_created > 0:
            logger.info(
                "queries_persisted",
                extra={"document_id": document.id, "queries_created": queries_created},
            )

        return queries_created

    async def is_profiling_enabled(self) -> bool:
        """Check if document profiling is enabled."""
        return self.settings.enable_document_profiling

    async def get_profiling_status(self, document_id: str) -> str | None:
        """Get the current profiling status of a document."""
        document = await self.db.get(Document, document_id)
        return document.profiling_status if document else None


# ---------------------------------------------------------------------------
# Standalone artifact embedding (called from INGESTION_EMBED worker handler)
# ---------------------------------------------------------------------------


async def embed_profile_artifacts(
    db: AsyncSession, document: Document, *, user_id: str | None = None
) -> tuple[bool, int, int]:
    """Embed synopsis, chunk summaries, and synthesized query vectors for a profiled document.

    This is a standalone function (not on ProfilingOrchestrator) because it only
    needs a DB session and the embedding service — no LLM client or profiling
    settings. Called from the INGESTION_EMBED worker handler after profiling
    completes. See SHU-637.

    Encoder selection:
    - Synopsis + chunk summaries: document encoder (``embed_texts`` / ``encode_document``)
    - Synthesized queries: query encoder (``embed_queries`` / ``encode_query``)

    Args:
        db: Async database session.
        document: The profiled document (must have synopsis set).
        user_id: Optional user attribution for llm_usage rows on the
            embedding API calls (threaded from the ingestion job).

    Returns:
        Tuple of (synopsis_embedded, chunk_summaries_embedded_count, queries_embedded_count).

    """
    embedding_service = await get_embedding_service()
    vector_store = await get_vector_store()

    synopsis_embedded = False
    chunk_summaries_embedded = 0
    queries_embedded = 0

    # Phase 1: Embed synopsis using document encoder
    if document.synopsis and document.synopsis.strip():
        embeddings = await embedding_service.embed_texts([str(document.synopsis)], user_id=user_id)
        if not embeddings:
            logger.warning("synopsis_embedding_empty", extra={"document_id": document.id})
        else:
            await vector_store.store_embeddings(
                "synopses",
                [VectorEntry(id=document.id, vector=embeddings[0])],
                db=db,
            )
            synopsis_embedded = True

    if synopsis_embedded:
        await db.commit()

    # Phase 2: Embed chunk summaries using document encoder (SHU-632)
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .where(DocumentChunk.summary.isnot(None))  # type: ignore[union-attr]
        .where(DocumentChunk.summary_embedding.is_(None))  # type: ignore[union-attr]
    )
    result = await db.execute(stmt)
    chunks_to_embed = list(result.scalars().all())

    if chunks_to_embed:
        try:
            summary_texts = [str(c.summary) for c in chunks_to_embed]
            embeddings = await embedding_service.embed_texts(summary_texts, user_id=user_id)
            entries = [VectorEntry(id=c.id, vector=emb) for c, emb in zip(chunks_to_embed, embeddings, strict=True)]
            await vector_store.store_embeddings("chunk_summaries", entries, db=db)
            chunk_summaries_embedded = len(entries)
        except Exception:
            logger.warning("chunk_summary_embedding_failed", extra={"document_id": document.id}, exc_info=True)
            raise

    if chunk_summaries_embedded:
        await db.commit()

    # Phase 3: Embed synthesized queries using query encoder
    stmt = (
        select(DocumentQuery)
        .where(DocumentQuery.document_id == document.id)
        .where(DocumentQuery.query_embedding.is_(None))
    )
    result = await db.execute(stmt)
    queries = list(result.scalars().all())

    if queries:
        try:
            query_texts = [q.query_text for q in queries]
            embeddings = await embedding_service.embed_queries(query_texts, user_id=user_id)
            entries = [VectorEntry(id=q.id, vector=emb) for q, emb in zip(queries, embeddings, strict=True)]
            await vector_store.store_embeddings("queries", entries, db=db)
            queries_embedded = len(queries)
        except Exception:
            logger.warning("query_embedding_failed", extra={"document_id": document.id}, exc_info=True)
            raise

    await db.commit()

    if synopsis_embedded or chunk_summaries_embedded or queries_embedded:
        logger.info(
            "profile_artifacts_embedded",
            extra={
                "document_id": document.id,
                "synopsis_embedded": synopsis_embedded,
                "chunk_summaries_embedded": chunk_summaries_embedded,
                "queries_embedded": queries_embedded,
            },
        )

    return synopsis_embedded, chunk_summaries_embedded, queries_embedded
