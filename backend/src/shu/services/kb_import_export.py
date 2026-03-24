"""Knowledge Base Import/Export Service.

Handles exporting a knowledge base to a portable zip archive and importing
it on another Shu instance without re-profiling.
"""

import base64
import io
import json
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.core.config import get_settings_instance
from shu.core.exceptions import ValidationError
from shu.core.logging import get_logger
from shu.core.queue_backend import QueueBackend
from shu.core.text import slugify
from shu.core.workload_routing import WorkloadType, enqueue_job
from shu.models.document import Document, DocumentChunk, DocumentQuery
from shu.models.knowledge_base import KnowledgeBase
from shu.schemas.knowledge_base import ImportManifestValidation, ImportStartResult
from shu.services.knowledge_base_service import KnowledgeBaseService

logger = get_logger(__name__)

EXPORT_BATCH_SIZE = 500
SCHEMA_VERSION = "1"


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime string into a datetime object.

    Handles both timezone-aware and naive ISO strings. Returns None if
    the value is None or unparseable.
    """
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


class KBImportExportService:
    """Service for importing and exporting knowledge bases."""

    def __init__(
        self,
        db: AsyncSession,
        kb_service: KnowledgeBaseService,
        queue: QueueBackend | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            db: Async database session.
            kb_service: Knowledge base service for PBAC-enforced lookups.
            queue: Queue backend for enqueuing import jobs. Required for start_import.

        """
        self.db = db
        self.kb_service = kb_service
        self.queue = queue

    @staticmethod
    def _encode_embedding(embedding: list[float] | None) -> str | None:
        """Base64-encode a float32 embedding vector.

        Args:
            embedding: List of floats, or None.

        Returns:
            Base64-encoded string, or None if input is None.

        """
        if embedding is None:
            return None
        return base64.b64encode(np.array(embedding, dtype=np.float32).tobytes()).decode("ascii")

    @staticmethod
    def _decode_embedding(data: str | None) -> list[float] | None:
        """Decode a base64-encoded float32 embedding vector.

        Args:
            data: Base64-encoded string, or None.

        Returns:
            List of floats, or None if input is None.

        """
        if data is None:
            return None
        return np.frombuffer(base64.b64decode(data), dtype=np.float32).tolist()

    @staticmethod
    def _serialize_document(doc: Document, export_index: int, no_embeddings: bool) -> dict[str, Any]:
        """Serialize a Document to a JSONL-compatible dict.

        Args:
            doc: The Document ORM instance.
            export_index: Sequential index for cross-referencing in the archive.
            no_embeddings: If True, omit all embedding vectors.

        Returns:
            Dict ready for JSON serialization.

        """
        encode = KBImportExportService._encode_embedding
        return {
            "export_index": export_index,
            "source_id": doc.source_id,
            "source_type": doc.source_type,
            "title": doc.title,
            "file_type": doc.file_type,
            "content": doc.content,
            "content_hash": doc.content_hash,
            "processing_status": doc.processing_status,
            "synopsis": doc.synopsis,
            "synopsis_embedding": None if no_embeddings else encode(doc.synopsis_embedding),
            "document_type": doc.document_type,
            "capability_manifest": doc.capability_manifest,
            "relational_context": doc.relational_context,
            "profiling_status": doc.profiling_status,
            "profiling_coverage_percent": doc.profiling_coverage_percent,
            "word_count": doc.word_count,
            "character_count": doc.character_count,
            "chunk_count": doc.chunk_count,
            "extraction_method": doc.extraction_method,
            "extraction_engine": doc.extraction_engine,
            "extraction_confidence": doc.extraction_confidence,
            "extraction_duration": doc.extraction_duration,
            "extraction_metadata": doc.extraction_metadata,
            "source_url": doc.source_url,
            "source_metadata": doc.source_metadata,
            "source_hash": doc.source_hash,
            "source_modified_at": (doc.source_modified_at.isoformat() if doc.source_modified_at else None),
            "file_size": doc.file_size,
            "mime_type": doc.mime_type,
        }

    @staticmethod
    def _serialize_chunk(chunk: DocumentChunk, export_index: int, no_embeddings: bool) -> dict[str, Any]:
        """Serialize a DocumentChunk to a JSONL-compatible dict.

        Args:
            chunk: The DocumentChunk ORM instance.
            export_index: The export_index of the parent document.
            no_embeddings: If True, omit all embedding vectors.

        Returns:
            Dict ready for JSON serialization.

        """
        encode = KBImportExportService._encode_embedding
        return {
            "export_index": export_index,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": None if no_embeddings else encode(chunk.embedding),
            "summary": chunk.summary,
            "summary_embedding": None if no_embeddings else encode(chunk.summary_embedding),
            "keywords": chunk.keywords,
            "topics": chunk.topics,
            "char_count": chunk.char_count,
            "word_count": chunk.word_count,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "embedding_model": chunk.embedding_model,
            "token_count": chunk.token_count,
            "chunk_metadata": chunk.chunk_metadata,
        }

    @staticmethod
    def _serialize_query(query: DocumentQuery, export_index: int, no_embeddings: bool) -> dict[str, Any]:
        """Serialize a DocumentQuery to a JSONL-compatible dict.

        Args:
            query: The DocumentQuery ORM instance.
            export_index: The export_index of the parent document.
            no_embeddings: If True, omit all embedding vectors.

        Returns:
            Dict ready for JSON serialization.

        """
        return {
            "export_index": export_index,
            "query_text": query.query_text,
            "query_embedding": (
                None if no_embeddings else KBImportExportService._encode_embedding(query.query_embedding)
            ),
        }

    @staticmethod
    def _read_manifest(content: bytes) -> dict[str, Any]:
        """Open a zip archive from raw bytes and return the parsed manifest.

        Args:
            content: Raw bytes of the zip file.

        Returns:
            Parsed manifest dict.

        Raises:
            ValidationError: If the file is not a valid zip, is missing
                manifest.json, contains invalid JSON, or has an unsupported
                schema version.

        """
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            raise ValidationError("Uploaded file is not a valid zip archive", "INVALID_ARCHIVE")

        if "manifest.json" not in zf.namelist():
            raise ValidationError("Archive missing manifest.json", "MISSING_MANIFEST")

        try:
            manifest = json.loads(zf.read("manifest.json"))
        except (json.JSONDecodeError, KeyError) as e:
            raise ValidationError(f"Invalid manifest.json: {e}", "INVALID_MANIFEST")

        schema_version = manifest.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise ValidationError(
                f"Unsupported schema version: {schema_version}. This instance supports version {SCHEMA_VERSION}.",
                "UNSUPPORTED_SCHEMA_VERSION",
            )

        return manifest

    async def validate_import(self, file: UploadFile) -> ImportManifestValidation:
        """Validate an import archive by reading only its manifest.

        Args:
            file: The uploaded zip file.

        Returns:
            ImportManifestValidation with manifest data and model match info.

        Raises:
            ValidationError: If the archive is invalid, missing manifest, or
                has an unsupported schema version.

        """
        content = await file.read()
        manifest = self._read_manifest(content)

        settings = get_settings_instance()
        instance_model = settings.default_embedding_model
        archive_model = manifest.get("embedding_model", "")
        counts = manifest.get("counts", {})

        return ImportManifestValidation(
            name=manifest.get("kb_name", ""),
            description=manifest.get("kb_description"),
            embedding_model=archive_model,
            chunk_size=manifest.get("chunk_size", 0),
            chunk_overlap=manifest.get("chunk_overlap", 0),
            schema_version=manifest.get("schema_version", ""),
            export_timestamp=manifest.get("export_timestamp", ""),
            document_count=counts.get("documents", 0),
            chunk_count=counts.get("chunks", 0),
            query_count=counts.get("queries", 0),
            embedding_model_match=(archive_model == instance_model),
            instance_embedding_model=instance_model,
        )

    async def _resolve_slug(self, base_slug: str) -> str:
        """Resolve slug conflicts by appending a short hash.

        If the base slug is taken, appends a 6-character hex suffix
        derived from a UUID. Collision probability is negligible.

        Args:
            base_slug: The initial slug derived from the KB name.

        Returns:
            A unique slug that doesn't conflict with existing KBs.

        """
        if not await self.kb_service._get_kb_by_slug(base_slug):
            return base_slug
        return f"{base_slug}-{uuid.uuid4().hex[:6]}"

    async def start_import(self, file: UploadFile, skip_embeddings: bool, owner_id: str) -> ImportStartResult:
        """Start a KB import: save archive, create KB row, enqueue job.

        Validates the archive, creates a KB in "importing" status with RAG
        config from the manifest, enqueues a background job, and returns
        immediately.

        Args:
            file: The uploaded zip archive.
            skip_embeddings: Whether to discard embeddings during import.
            owner_id: User ID to set as KB owner.

        Returns:
            ImportStartResult with the new KB's ID, name, slug, and status.

        Raises:
            ValidationError: If the archive is invalid.

        """
        content = await file.read()
        manifest = self._read_manifest(content)

        # Save archive to temp file for the worker
        archive_path = f"{tempfile.gettempdir()}/shu-import-{uuid.uuid4()}.zip"
        with open(archive_path, "wb") as f:
            f.write(content)

        # Resolve slug and create KB row
        kb_name = manifest.get("kb_name", "Imported KB")
        base_slug = slugify(kb_name)
        if not base_slug:
            base_slug = "imported-kb"
        slug = await self._resolve_slug(base_slug)

        kb = KnowledgeBase(
            name=kb_name,
            slug=slug,
            description=manifest.get("kb_description"),
            embedding_model=manifest.get("embedding_model", ""),
            chunk_size=manifest.get("chunk_size", 1000),
            chunk_overlap=manifest.get("chunk_overlap", 200),
            status="importing",
            owner_id=owner_id,
            import_progress={"phase": "queued"},
            sync_enabled=False,
        )

        rag_config = manifest.get("rag_config")
        if rag_config:
            kb.update_rag_config(rag_config)

        self.db.add(kb)
        await self.db.commit()
        await self.db.refresh(kb)

        # Enqueue background job
        await enqueue_job(
            self.queue,
            WorkloadType.KB_IMPORT,
            payload={
                "knowledge_base_id": kb.id,
                "archive_path": archive_path,
                "skip_embeddings": skip_embeddings,
            },
        )

        logger.info(
            "KB import enqueued",
            extra={"kb_id": kb.id, "slug": slug, "archive_path": archive_path},
        )

        return ImportStartResult(
            knowledge_base_id=kb.id,
            name=kb.name,
            slug=kb.slug,
            status=kb.status,
        )

    def _build_document_record(
        self, row: dict[str, Any], new_id: str, kb_id: str, skip_embeddings: bool
    ) -> dict[str, Any]:
        """Build a document insert dict from a JSONL row.

        Args:
            row: Parsed JSONL dict from the archive.
            new_id: New UUID for this document.
            kb_id: Target knowledge base ID.
            skip_embeddings: Whether to discard embeddings.

        Returns:
            Dict suitable for bulk insert into the documents table.

        """
        now = datetime.now(UTC)
        record: dict[str, Any] = {
            "id": new_id,
            "knowledge_base_id": kb_id,
            "source_id": row.get("source_id", ""),
            "source_type": row.get("source_type", "import"),
            "title": row.get("title", ""),
            "file_type": row.get("file_type", "unknown"),
            "content": row.get("content", ""),
            "content_hash": row.get("content_hash"),
            "source_hash": row.get("source_hash"),
            "source_url": row.get("source_url"),
            "source_metadata": row.get("source_metadata"),
            "source_modified_at": _parse_datetime(row.get("source_modified_at")),
            "file_size": row.get("file_size"),
            "mime_type": row.get("mime_type"),
            "word_count": row.get("word_count"),
            "character_count": row.get("character_count"),
            "chunk_count": row.get("chunk_count", 0),
            "extraction_method": row.get("extraction_method"),
            "extraction_engine": row.get("extraction_engine"),
            "extraction_confidence": row.get("extraction_confidence"),
            "extraction_duration": row.get("extraction_duration"),
            "extraction_metadata": row.get("extraction_metadata"),
            "synopsis": row.get("synopsis"),
            "document_type": row.get("document_type"),
            "capability_manifest": row.get("capability_manifest"),
            "relational_context": row.get("relational_context"),
            "profiling_status": row.get("profiling_status"),
            "profiling_coverage_percent": row.get("profiling_coverage_percent"),
            "created_at": now,
            "updated_at": now,
            "processed_at": now,
        }

        if skip_embeddings:
            record["synopsis_embedding"] = None
            record["processing_status"] = "pending"
        else:
            record["synopsis_embedding"] = self._decode_embedding(row.get("synopsis_embedding"))
            record["processing_status"] = row.get("processing_status", "processed")

        return record

    async def _update_import_progress(self, kb_id: str, **fields: Any) -> None:
        """Update the import_progress JSON on a KB row.

        Merges the provided fields into the existing progress dict.

        Args:
            kb_id: Knowledge base ID.
            **fields: Key-value pairs to merge into import_progress.

        """
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        result = await self.db.execute(stmt)
        kb = result.scalar_one()
        current = kb.import_progress or {}
        kb.import_progress = {**current, **fields}
        await self.db.commit()

    async def execute_import(self, archive_path: str, kb_id: str, skip_embeddings: bool) -> None:
        """Execute the full import from a zip archive.

        Called by the background worker. Reads JSONL files line-by-line,
        assigns new UUIDs, remaps relationships, and bulk-inserts in batches.
        Updates import_progress on the KB row as it goes.

        Args:
            archive_path: Path to the temp zip file on disk.
            kb_id: ID of the KB row created by start_import.
            skip_embeddings: Whether to discard embedding vectors.

        """
        try:
            await self._execute_import_inner(archive_path, kb_id, skip_embeddings)
        except Exception as e:
            logger.error("KB import failed", extra={"kb_id": kb_id, "error": str(e)}, exc_info=True)
            await self._mark_import_failed(kb_id, str(e), archive_path)
            raise

    async def _mark_import_failed(self, kb_id: str, error: str, archive_path: str) -> None:
        """Mark the KB as failed and clean up the archive file.

        Args:
            kb_id: Knowledge base ID.
            error: Error message to store.
            archive_path: Path to the temp archive to delete.

        """
        import os

        try:
            stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            result = await self.db.execute(stmt)
            kb = result.scalar_one_or_none()
            if kb:
                kb.status = "error"
                kb.import_progress = {**(kb.import_progress or {}), "error": error}
                await self.db.commit()
        except Exception:
            logger.error("Failed to mark KB as error", extra={"kb_id": kb_id}, exc_info=True)
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)

    async def _execute_import_inner(self, archive_path: str, kb_id: str, skip_embeddings: bool) -> None:
        """Inner import logic, called by execute_import with error wrapping."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            counts = manifest.get("counts", {})
            total_docs = counts.get("documents", 0)
            total_chunks = counts.get("chunks", 0)
            total_queries = counts.get("queries", 0)

            await self._update_import_progress(
                kb_id,
                phase="documents",
                documents_done=0,
                documents_total=total_docs,
                chunks_done=0,
                chunks_total=total_chunks,
                queries_done=0,
                queries_total=total_queries,
                started_at=datetime.now(UTC).isoformat(),
            )

            # Phase 1: Documents
            export_index_to_doc_id: dict[int, str] = {}
            doc_batch: list[dict[str, Any]] = []
            docs_done = 0

            raw_docs = zf.read("documents.jsonl").decode("utf-8") if "documents.jsonl" in zf.namelist() else ""
            for raw_line in raw_docs.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed document JSONL line", extra={"kb_id": kb_id})
                    continue

                if "export_index" not in row:
                    logger.warning("Skipping document line missing export_index", extra={"kb_id": kb_id})
                    continue

                new_id = str(uuid.uuid4())
                export_index_to_doc_id[row["export_index"]] = new_id
                doc_batch.append(self._build_document_record(row, new_id, kb_id, skip_embeddings))

                if len(doc_batch) >= EXPORT_BATCH_SIZE:
                    await self.db.execute(pg_insert(Document).values(doc_batch))
                    await self.db.commit()
                    docs_done += len(doc_batch)
                    await self._update_import_progress(kb_id, documents_done=docs_done)
                    doc_batch.clear()

            # Flush remaining documents
            if doc_batch:
                await self.db.execute(pg_insert(Document).values(doc_batch))
                await self.db.commit()
                docs_done += len(doc_batch)
                await self._update_import_progress(kb_id, documents_done=docs_done)
                doc_batch.clear()

            # Phase 2: Chunks
            chunks_done = await self._import_chunks(zf, kb_id, skip_embeddings, export_index_to_doc_id)
            await self._update_import_progress(kb_id, phase="chunks", chunks_done=chunks_done)

            # Phase 3: Queries
            queries_done = await self._import_queries(zf, kb_id, skip_embeddings, export_index_to_doc_id)
            await self._update_import_progress(kb_id, phase="queries", queries_done=queries_done)

            # Phase 4: Finalization
            await self._finalize_import(kb_id, docs_done, chunks_done, skip_embeddings, archive_path)

    async def _import_chunks(
        self,
        zf: zipfile.ZipFile,
        kb_id: str,
        skip_embeddings: bool,
        export_index_to_doc_id: dict[int, str],
    ) -> int:
        """Import chunks from the archive.

        Reads chunks.jsonl line-by-line, remaps document_id via the
        export_index lookup, and bulk-inserts in batches.

        Args:
            zf: Open ZipFile.
            kb_id: Target knowledge base ID.
            skip_embeddings: Whether to discard embedding vectors.
            export_index_to_doc_id: Mapping from export_index to new document UUID.

        Returns:
            Number of chunks imported.

        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        raw = zf.read("chunks.jsonl").decode("utf-8") if "chunks.jsonl" in zf.namelist() else ""
        batch: list[dict[str, Any]] = []
        total = 0

        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed chunk JSONL line", extra={"kb_id": kb_id})
                continue

            export_index = row.get("export_index")
            if export_index is None or "chunk_index" not in row:
                logger.warning("Skipping chunk line missing required fields", extra={"kb_id": kb_id})
                continue

            doc_id = export_index_to_doc_id.get(export_index)
            if doc_id is None:
                logger.warning(
                    "Skipping chunk with unknown export_index",
                    extra={"kb_id": kb_id, "export_index": export_index},
                )
                continue

            now = datetime.now(UTC)
            decode = self._decode_embedding
            record: dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "knowledge_base_id": kb_id,
                "chunk_index": row["chunk_index"],
                "content": row.get("content", ""),
                "summary": row.get("summary"),
                "keywords": row.get("keywords"),
                "topics": row.get("topics"),
                "char_count": row.get("char_count", 0),
                "word_count": row.get("word_count"),
                "token_count": row.get("token_count"),
                "start_char": row.get("start_char"),
                "end_char": row.get("end_char"),
                "embedding_model": row.get("embedding_model"),
                "chunk_metadata": row.get("chunk_metadata"),
                "created_at": now,
                "updated_at": now,
            }

            if skip_embeddings:
                record["embedding"] = None
                record["summary_embedding"] = None
                record["embedding_created_at"] = None
            else:
                record["embedding"] = decode(row.get("embedding"))
                record["summary_embedding"] = decode(row.get("summary_embedding"))
                record["embedding_created_at"] = now

            batch.append(record)

            if len(batch) >= EXPORT_BATCH_SIZE:
                await self.db.execute(pg_insert(DocumentChunk).values(batch))
                await self.db.commit()
                total += len(batch)
                await self._update_import_progress(kb_id, chunks_done=total)
                batch.clear()

        if batch:
            await self.db.execute(pg_insert(DocumentChunk).values(batch))
            await self.db.commit()
            total += len(batch)
            batch.clear()

        return total

    async def _import_queries(
        self,
        zf: zipfile.ZipFile,
        kb_id: str,
        skip_embeddings: bool,
        export_index_to_doc_id: dict[int, str],
    ) -> int:
        """Import queries from the archive.

        Reads queries.jsonl line-by-line, remaps document_id via the
        export_index lookup, and bulk-inserts in batches.

        Args:
            zf: Open ZipFile.
            kb_id: Target knowledge base ID.
            skip_embeddings: Whether to discard embedding vectors.
            export_index_to_doc_id: Mapping from export_index to new document UUID.

        Returns:
            Number of queries imported.

        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        raw = zf.read("queries.jsonl").decode("utf-8") if "queries.jsonl" in zf.namelist() else ""
        batch: list[dict[str, Any]] = []
        total = 0

        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed query JSONL line", extra={"kb_id": kb_id})
                continue

            export_index = row.get("export_index")
            if export_index is None or "query_text" not in row:
                logger.warning("Skipping query line missing required fields", extra={"kb_id": kb_id})
                continue

            doc_id = export_index_to_doc_id.get(export_index)
            if doc_id is None:
                logger.warning(
                    "Skipping query with unknown export_index",
                    extra={"kb_id": kb_id, "export_index": export_index},
                )
                continue

            now = datetime.now(UTC)
            record: dict[str, Any] = {
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "knowledge_base_id": kb_id,
                "query_text": row["query_text"],
                "created_at": now,
                "updated_at": now,
            }

            if skip_embeddings:
                record["query_embedding"] = None
            else:
                record["query_embedding"] = self._decode_embedding(row.get("query_embedding"))

            batch.append(record)

            if len(batch) >= EXPORT_BATCH_SIZE:
                await self.db.execute(pg_insert(DocumentQuery).values(batch))
                await self.db.commit()
                total += len(batch)
                await self._update_import_progress(kb_id, queries_done=total)
                batch.clear()

        if batch:
            await self.db.execute(pg_insert(DocumentQuery).values(batch))
            await self.db.commit()
            total += len(batch)
            batch.clear()

        return total

    async def _finalize_import(
        self,
        kb_id: str,
        doc_count: int,
        chunk_count: int,
        skip_embeddings: bool,
        archive_path: str,
    ) -> None:
        """Finalize the import: update stats, set status, clean up.

        Args:
            kb_id: Knowledge base ID.
            doc_count: Number of documents imported.
            chunk_count: Number of chunks imported.
            skip_embeddings: Whether embeddings were skipped.
            archive_path: Path to the temp archive file to delete.

        """
        import os

        try:
            stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            result = await self.db.execute(stmt)
            kb = result.scalar_one()

            kb.document_count = doc_count
            kb.total_chunks = chunk_count
            kb.embedding_status = "stale" if skip_embeddings else "current"
            kb.status = "active"
            kb.import_progress = {**(kb.import_progress or {}), "phase": "complete"}

            await self.db.commit()

            logger.info(
                "KB import finalized",
                extra={
                    "kb_id": kb_id,
                    "documents": doc_count,
                    "chunks": chunk_count,
                    "embedding_status": kb.embedding_status,
                },
            )
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)
                logger.debug("Deleted temp archive", extra={"path": archive_path})

    async def export_kb(self, kb_id: str, user_id: str, no_embeddings: bool = False) -> tuple[str, str]:
        """Export a knowledge base to a zip archive on disk.

        Fetches the KB (with PBAC enforcement), then writes documents, chunks,
        and queries to JSONL files inside a zip archive. The manifest is written
        last after all counts are known.

        Args:
            kb_id: Knowledge base ID.
            user_id: User ID for PBAC enforcement.
            no_embeddings: If True, omit all embedding vectors from the archive.

        Returns:
            Tuple of (temp_file_path, suggested_filename).

        """
        kb = await self.kb_service.get_knowledge_base(kb_id, user_id)

        temp_path = f"{tempfile.gettempdir()}/shu-export-{uuid.uuid4()}.zip"

        doc_count = 0
        chunk_count = 0
        query_count = 0

        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            doc_count, chunk_count, query_count = await self._write_data_files(zf, kb_id, no_embeddings)

            manifest = {
                "schema_version": SCHEMA_VERSION,
                "export_timestamp": datetime.now(UTC).isoformat(),
                "kb_name": kb.name,
                "kb_description": kb.description,
                "embedding_model": kb.embedding_model,
                "chunk_size": kb.chunk_size,
                "chunk_overlap": kb.chunk_overlap,
                "rag_config": kb.get_rag_config(),
                "counts": {
                    "documents": doc_count,
                    "chunks": chunk_count,
                    "queries": query_count,
                },
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

        filename = f"{kb.slug}-export.zip"
        logger.info(
            "KB export complete",
            extra={
                "kb_id": kb_id,
                "documents": doc_count,
                "chunks": chunk_count,
                "queries": query_count,
                "no_embeddings": no_embeddings,
            },
        )
        return temp_path, filename

    async def _write_data_files(
        self,
        zf: zipfile.ZipFile,
        kb_id: str,
        no_embeddings: bool,
    ) -> tuple[int, int, int]:
        """Write documents.jsonl, chunks.jsonl, and queries.jsonl into the zip.

        Queries documents in batches with eager-loaded chunks and queries to
        keep memory bounded.

        Args:
            zf: Open ZipFile to write into.
            kb_id: Knowledge base ID.
            no_embeddings: Whether to omit embeddings.

        Returns:
            Tuple of (document_count, chunk_count, query_count).

        """
        doc_lines: list[str] = []
        chunk_lines: list[str] = []
        query_lines: list[str] = []

        export_index = 0
        offset = 0

        while True:
            stmt = (
                select(Document)
                .where(Document.knowledge_base_id == kb_id)
                .options(selectinload(Document.chunks), selectinload(Document.queries))
                .order_by(Document.id)
                .offset(offset)
                .limit(EXPORT_BATCH_SIZE)
            )
            result = await self.db.execute(stmt)
            docs = list(result.scalars().all())

            if not docs:
                break

            for doc in docs:
                doc_lines.append(
                    json.dumps(self._serialize_document(doc, export_index, no_embeddings), ensure_ascii=False)
                )

                for chunk in doc.chunks:
                    chunk_lines.append(
                        json.dumps(self._serialize_chunk(chunk, export_index, no_embeddings), ensure_ascii=False)
                    )

                for query in doc.queries:
                    query_lines.append(
                        json.dumps(self._serialize_query(query, export_index, no_embeddings), ensure_ascii=False)
                    )

                export_index += 1

            offset += EXPORT_BATCH_SIZE

        zf.writestr("documents.jsonl", "\n".join(doc_lines) + "\n" if doc_lines else "")
        zf.writestr("chunks.jsonl", "\n".join(chunk_lines) + "\n" if chunk_lines else "")
        zf.writestr("queries.jsonl", "\n".join(query_lines) + "\n" if query_lines else "")

        return len(doc_lines), len(chunk_lines), len(query_lines)
