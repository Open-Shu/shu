"""Corpus ingestion utility for benchmark evaluation.

Ingests BEIR corpus documents into a Shu knowledge base via the existing
ingest_text() pipeline, then polls until all documents reach the target
processing status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from sqlalchemy import text

from .beir_loader import BeirCorpusEntry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Terminal statuses where a document is considered "done"
TERMINAL_STATUSES = frozenset({"content_processed", "rag_processed", "profile_processed", "error"})

# Statuses that indicate profiling has completed (multi-surface search ready)
PROFILED_STATUSES = frozenset({"profile_processed"})

# Statuses that indicate at least embedding is done (basic search ready)
EMBEDDED_STATUSES = frozenset({"content_processed", "rag_processed", "profile_processed"})

# The benchmark plugin name used for source_type identification and cleanup
BENCHMARK_PLUGIN_NAME = "benchmark"


@dataclass
class IngestionResult:
    """Result of ingesting a single BEIR document."""

    beir_id: str
    shu_doc_id: str
    status: str
    error: str | None = None


@dataclass
class IngestionSummary:
    """Summary of a bulk corpus ingestion."""

    total: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[IngestionResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def doc_ids(self) -> list[str]:
        """Return all successfully ingested Shu document IDs."""
        return [r.shu_doc_id for r in self.results if r.error is None]


class CorpusIngestor:
    """Ingest BEIR corpus documents into a Shu knowledge base."""

    def __init__(self, db: AsyncSession, kb_id: str, user_id: str):
        self.db = db
        self.kb_id = kb_id
        self.user_id = user_id

    async def ingest_corpus(
        self,
        corpus: dict[str, BeirCorpusEntry],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestionSummary:
        """Ingest all corpus documents via ingest_text().

        Sets source_id to the BEIR _id for reverse mapping.
        Documents with existing source_id are skipped (dedup by content hash).

        Args:
            corpus: Dict of BEIR doc_id -> BeirCorpusEntry.
            progress_callback: Called with (completed, total) after each document.

        Returns:
            IngestionSummary with per-document results.
        """
        from shu.services.ingestion_service import ingest_text

        summary = IngestionSummary(total=len(corpus))
        start = time.monotonic()

        for i, (beir_id, entry) in enumerate(corpus.items()):
            try:
                # Pass title and text separately — the chunking pipeline
                # handles title injection (as a separate title chunk or
                # prepended to chunk 0, depending on KB config).
                result = await ingest_text(
                    self.db,
                    self.kb_id,
                    plugin_name=BENCHMARK_PLUGIN_NAME,
                    user_id=self.user_id,
                    title=entry.title or beir_id,
                    content=entry.text,
                    source_id=beir_id,
                )

                doc_id = result.get("document_id", "")
                action = result.get("action", "ingested")

                if action == "skipped":
                    summary.skipped += 1
                else:
                    summary.ingested += 1

                summary.results.append(IngestionResult(beir_id=beir_id, shu_doc_id=str(doc_id), status=action))

            except Exception as e:
                logger.error("Failed to ingest BEIR doc '%s': %s", beir_id, e)
                summary.failed += 1
                summary.results.append(IngestionResult(beir_id=beir_id, shu_doc_id="", status="error", error=str(e)))

            if progress_callback:
                progress_callback(i + 1, summary.total)

        summary.elapsed_seconds = time.monotonic() - start
        logger.info(
            "Corpus ingestion complete: %d ingested, %d skipped, %d failed (%.1fs)",
            summary.ingested,
            summary.skipped,
            summary.failed,
            summary.elapsed_seconds,
        )
        return summary

    async def wait_for_processing(
        self,
        target_statuses: frozenset[str] = EMBEDDED_STATUSES,
        *,
        timeout: float = 3600,
        poll_interval: float = 5.0,
        progress_callback: Callable[[int, int, dict[str, int]], None] | None = None,
    ) -> dict[str, str]:
        """Poll until all documents in the KB reach a target status.

        Uses a single SQL query to check all document statuses at once.

        Args:
            target_statuses: Set of acceptable terminal statuses.
            timeout: Maximum seconds to wait.
            poll_interval: Seconds between polls.
            progress_callback: Called with (done, total, status_counts).

        Returns:
            Dict of {shu_doc_id: final_status} for all documents.

        Raises:
            TimeoutError: If not all documents complete within timeout.
        """
        accept = target_statuses | {"error"}
        start = time.monotonic()

        while True:
            status_rows = await self._query_all_statuses()
            total = len(status_rows)

            if total == 0:
                logger.warning("No documents found in KB %s", self.kb_id)
                return {}

            done = {doc_id: status for doc_id, status in status_rows.items() if status in accept}
            status_counts = self._count_statuses(status_rows)

            if progress_callback:
                progress_callback(len(done), total, status_counts)

            if len(done) == total:
                errors = {doc_id for doc_id, status in done.items() if status == "error"}
                if errors:
                    logger.warning("%d documents ended in error state", len(errors))
                logger.info(
                    "All %d documents reached terminal status (%.1fs): %s",
                    total,
                    time.monotonic() - start,
                    status_counts,
                )
                return done

            elapsed = time.monotonic() - start
            if elapsed > timeout:
                pending = {doc_id: status for doc_id, status in status_rows.items() if status not in accept}
                raise TimeoutError(
                    f"{len(pending)} of {total} documents did not complete within {timeout}s. "
                    f"Status breakdown: {status_counts}"
                )

            await asyncio.sleep(poll_interval)

    async def build_id_map(self, profiled_only: bool = False) -> dict[str, str]:
        """Build mapping of Shu document UUID -> BEIR source_id.

        Queries all documents in the KB that were ingested by the benchmark
        plugin and returns their source_id mapping.

        Args:
            profiled_only: If True, only include documents with processing_status='profile_processed'.

        Returns:
            Dict of {shu_doc_uuid_str: beir_doc_id}.
        """
        status_filter = "AND processing_status = 'profile_processed'" if profiled_only else ""
        result = await self.db.execute(
            text(f"""
                SELECT id::text, source_id
                FROM documents
                WHERE knowledge_base_id = :kb_id
                  AND source_type = :source_type
                  AND source_id IS NOT NULL
                  {status_filter}
            """),
            {"kb_id": self.kb_id, "source_type": f"plugin:{BENCHMARK_PLUGIN_NAME}"},
        )
        rows = result.fetchall()
        id_map = {row[0]: row[1] for row in rows}
        logger.info("Built ID map: %d Shu documents -> BEIR IDs (profiled_only=%s)", len(id_map), profiled_only)
        return id_map

    async def _query_all_statuses(self) -> dict[str, str]:
        """Query processing_status for all documents in the KB."""
        result = await self.db.execute(
            text("""
                SELECT id::text, processing_status
                FROM documents
                WHERE knowledge_base_id = :kb_id
            """),
            {"kb_id": self.kb_id},
        )
        return {row[0]: row[1] for row in result.fetchall()}

    @staticmethod
    def _count_statuses(status_rows: dict[str, str]) -> dict[str, int]:
        """Count documents by status."""
        counts: dict[str, int] = {}
        for status in status_rows.values():
            counts[status] = counts.get(status, 0) + 1
        return counts
