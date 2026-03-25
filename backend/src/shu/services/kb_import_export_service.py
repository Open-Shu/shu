"""Knowledge Base Import/Export Service.

Handles exporting a knowledge base to a portable zip archive and importing
it on another Shu instance without re-profiling.
"""

import io
import json
import os
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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
MAX_IMPORT_ARCHIVE_SIZE = 500 * 1024 * 1024  # 500 MB


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
    def _read_manifest(source: Any) -> dict[str, Any]:
        """Open a zip archive and return the parsed manifest.

        Args:
            source: A seekable file-like object or path accepted by
                ``zipfile.ZipFile``.

        Returns:
            Parsed manifest dict.

        Raises:
            ValidationError: If the file is not a valid zip, is missing
                manifest.json, contains invalid JSON, or has an unsupported
                schema version.

        """
        try:
            zf = zipfile.ZipFile(source)
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

    @staticmethod
    def _iter_jsonl(zf: zipfile.ZipFile, name: str) -> Iterator[str]:
        """Yield non-empty lines from a JSONL file inside a zip archive.

        Reads line-by-line via a TextIOWrapper so the entire file is never
        loaded into memory at once.

        Args:
            zf: Open ZipFile.
            name: Name of the JSONL member file.

        Yields:
            Stripped, non-empty lines.

        """
        if name not in zf.namelist():
            return
        with zf.open(name) as raw:
            for raw_line in io.TextIOWrapper(raw, encoding="utf-8"):
                line = raw_line.strip()
                if line:
                    yield line

    @staticmethod
    async def _save_upload_to_temp(file: UploadFile) -> str:
        """Stream an uploaded file to a temp file with a size limit.

        Reads in 64 KB chunks so the full archive is never in memory.

        Returns:
            Path to the temp file on disk.

        Raises:
            ValidationError: If the file exceeds MAX_IMPORT_ARCHIVE_SIZE.

        """
        archive_path = f"{tempfile.gettempdir()}/shu-import-{uuid.uuid4()}.zip"
        total = 0
        try:
            with open(archive_path, "wb") as out:
                while chunk := await file.read(65536):
                    total += len(chunk)
                    if total > MAX_IMPORT_ARCHIVE_SIZE:
                        raise ValidationError(
                            f"Archive exceeds maximum size of {MAX_IMPORT_ARCHIVE_SIZE // (1024 * 1024)} MB",
                            "ARCHIVE_TOO_LARGE",
                        )
                    out.write(chunk)
        except ValidationError:
            if os.path.exists(archive_path):
                os.remove(archive_path)
            raise
        return archive_path

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
        manifest = self._read_manifest(file.file)

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
        if not await self.kb_service.slug_exists(base_slug):
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
        archive_path = await self._save_upload_to_temp(file)

        try:
            manifest = self._read_manifest(archive_path)
        except Exception:
            if os.path.exists(archive_path):
                os.remove(archive_path)
            raise

        # Enforce embedding model compatibility
        settings = get_settings_instance()
        archive_model = manifest.get("embedding_model", "")
        if archive_model != settings.default_embedding_model and not skip_embeddings:
            if os.path.exists(archive_path):
                os.remove(archive_path)
            raise ValidationError(
                f"Embedding model mismatch: archive uses '{archive_model}', "
                f"this instance uses '{settings.default_embedding_model}'. "
                "Set skip_embeddings=true to import without embeddings.",
                "EMBEDDING_MODEL_MISMATCH",
            )

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

        # Enqueue background job — clean up on failure
        try:
            await enqueue_job(
                self.queue,
                WorkloadType.KB_IMPORT,
                payload={
                    "knowledge_base_id": kb.id,
                    "archive_path": archive_path,
                    "skip_embeddings": skip_embeddings,
                },
            )
        except Exception:
            logger.error("Failed to enqueue KB import job", extra={"kb_id": kb.id}, exc_info=True)
            await self._mark_import_failed(kb.id, "Failed to enqueue import job", archive_path)
            raise

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

        Includes a status guard: if the KB is no longer in ``importing``
        status (e.g. a duplicate job delivery or a concurrent restart),
        the import is skipped to avoid duplicating rows.

        Args:
            archive_path: Path to the temp zip file on disk.
            kb_id: ID of the KB row created by start_import.
            skip_embeddings: Whether to discard embedding vectors.

        """
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        result = await self.db.execute(stmt)
        kb = result.scalar_one_or_none()
        if not kb or kb.status != "importing":
            logger.warning(
                "Skipping KB import — status is not 'importing'",
                extra={"kb_id": kb_id, "status": kb.status if kb else "missing"},
            )
            return

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
        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            embeddings_included = manifest.get("embeddings_included", True)
            effectively_skip = skip_embeddings or not embeddings_included
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

            for line in self._iter_jsonl(zf, "documents.jsonl"):
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
                doc_batch.append(Document.build_import_record(row, new_id, kb_id, effectively_skip))

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
            chunks_done = await self._import_related_entities(
                zf,
                "chunks.jsonl",
                kb_id,
                effectively_skip,
                export_index_to_doc_id,
                model=DocumentChunk,
                required_field="chunk_index",
                progress_key="chunks_done",
            )
            await self._update_import_progress(kb_id, phase="chunks", chunks_done=chunks_done)

            # Phase 3: Queries
            queries_done = await self._import_related_entities(
                zf,
                "queries.jsonl",
                kb_id,
                effectively_skip,
                export_index_to_doc_id,
                model=DocumentQuery,
                required_field="query_text",
                progress_key="queries_done",
            )
            await self._update_import_progress(kb_id, phase="queries", queries_done=queries_done)

            # Phase 4: Finalization
            await self._finalize_import(kb_id, docs_done, chunks_done, effectively_skip, archive_path)

    async def _import_related_entities(
        self,
        zf: zipfile.ZipFile,
        filename: str,
        kb_id: str,
        skip_embeddings: bool,
        export_index_to_doc_id: dict[int, str],
        model: type,
        required_field: str,
        progress_key: str,
    ) -> int:
        """Import chunks or queries from a JSONL file in the archive.

        Each model must implement ``build_import_record(row, doc_id, kb_id, skip_embeddings)``.

        Args:
            zf: Open ZipFile.
            filename: JSONL member name inside the zip.
            kb_id: Target knowledge base ID.
            skip_embeddings: Whether to discard embedding vectors.
            export_index_to_doc_id: Mapping from export_index to new document UUID.
            model: SQLAlchemy model class with ``build_import_record``.
            required_field: A field that must be present in each row (besides export_index).
            progress_key: Key name for ``_update_import_progress`` (e.g. "chunks_done").

        Returns:
            Number of entities imported.

        """
        entity_label = filename.removesuffix(".jsonl")
        batch: list[dict[str, Any]] = []
        total = 0

        for line in self._iter_jsonl(zf, filename):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Skipping malformed {entity_label} JSONL line", extra={"kb_id": kb_id})
                continue

            export_index = row.get("export_index")
            if export_index is None or required_field not in row:
                logger.warning(f"Skipping {entity_label} line missing required fields", extra={"kb_id": kb_id})
                continue

            doc_id = export_index_to_doc_id.get(export_index)
            if doc_id is None:
                logger.warning(
                    f"Skipping {entity_label} with unknown export_index",
                    extra={"kb_id": kb_id, "export_index": export_index},
                )
                continue

            batch.append(model.build_import_record(row, doc_id, kb_id, skip_embeddings))

            if len(batch) >= EXPORT_BATCH_SIZE:
                await self.db.execute(pg_insert(model).values(batch))
                await self.db.commit()
                total += len(batch)
                await self._update_import_progress(kb_id, **{progress_key: total})
                batch.clear()

        if batch:
            await self.db.execute(pg_insert(model).values(batch))
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
        try:
            stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            result = await self.db.execute(stmt)
            kb = result.scalar_one()

            if kb.status != "importing":
                logger.warning(
                    "Skipping import finalization — status changed",
                    extra={"kb_id": kb_id, "status": kb.status},
                )
                return

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
                "embeddings_included": not no_embeddings,
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

        Each entity type is queried and written in a separate pass so only one
        zip write handle is open at a time and memory stays bounded per batch.
        A ``doc_id → export_index`` mapping (~10 MB for 100K docs) is the only
        cross-pass state kept in memory.

        Args:
            zf: Open ZipFile to write into.
            kb_id: Knowledge base ID.
            no_embeddings: Whether to omit embeddings.

        Returns:
            Tuple of (document_count, chunk_count, query_count).

        """
        doc_id_to_index: dict[str, int] = {}

        doc_count = await self._write_documents(zf, kb_id, no_embeddings, doc_id_to_index)
        chunk_count = await self._write_related_entities(
            zf,
            "chunks.jsonl",
            kb_id,
            no_embeddings,
            doc_id_to_index,
            model=DocumentChunk,
            order_by=[DocumentChunk.document_id, DocumentChunk.chunk_index],
        )
        query_count = await self._write_related_entities(
            zf,
            "queries.jsonl",
            kb_id,
            no_embeddings,
            doc_id_to_index,
            model=DocumentQuery,
            order_by=[DocumentQuery.document_id, DocumentQuery.id],
        )

        return doc_count, chunk_count, query_count

    async def _write_documents(
        self,
        zf: zipfile.ZipFile,
        kb_id: str,
        no_embeddings: bool,
        doc_id_to_index: dict[str, int],
    ) -> int:
        """Write documents.jsonl and populate the doc_id → export_index map."""
        count = 0
        offset = 0

        with zf.open("documents.jsonl", "w") as f:
            while True:
                stmt = (
                    select(Document)
                    .where(Document.knowledge_base_id == kb_id)
                    .order_by(Document.id)
                    .offset(offset)
                    .limit(EXPORT_BATCH_SIZE)
                )
                result = await self.db.execute(stmt)
                docs = list(result.scalars().all())

                if not docs:
                    break

                for doc in docs:
                    export_index = count
                    doc_id_to_index[doc.id] = export_index
                    line = json.dumps(
                        doc.serialize_for_export(export_index, no_embeddings),
                        ensure_ascii=False,
                    )
                    f.write((line + "\n").encode("utf-8"))
                    count += 1

                offset += EXPORT_BATCH_SIZE

        return count

    async def _write_related_entities(
        self,
        zf: zipfile.ZipFile,
        filename: str,
        kb_id: str,
        no_embeddings: bool,
        doc_id_to_index: dict[str, int],
        model: type,
        order_by: list,
    ) -> int:
        """Write a JSONL file for entities that reference documents by export_index.

        Each entity's model must implement ``serialize_for_export(export_index, no_embeddings)``.

        Args:
            zf: Open ZipFile to write into.
            filename: Name of the JSONL file inside the zip.
            kb_id: Knowledge base ID.
            no_embeddings: Whether to omit embeddings.
            doc_id_to_index: Mapping from document ID to export_index.
            model: SQLAlchemy model class (must have knowledge_base_id, document_id).
            order_by: Columns to order by.

        Returns:
            Number of entities written.

        """
        count = 0
        offset = 0

        with zf.open(filename, "w") as f:
            while True:
                stmt = (
                    select(model)
                    .where(model.knowledge_base_id == kb_id)
                    .order_by(*order_by)
                    .offset(offset)
                    .limit(EXPORT_BATCH_SIZE)
                )
                result = await self.db.execute(stmt)
                rows = list(result.scalars().all())

                if not rows:
                    break

                for row in rows:
                    export_index = doc_id_to_index.get(row.document_id)
                    if export_index is None:
                        continue
                    line = json.dumps(
                        row.serialize_for_export(export_index, no_embeddings),
                        ensure_ascii=False,
                    )
                    f.write((line + "\n").encode("utf-8"))
                    count += 1

                offset += EXPORT_BATCH_SIZE

        return count
