"""Unit tests for KBImportExportService serialization and embedding helpers."""

import io
import json
import math
import os
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import ValidationError
from shu.models.document import Document, DocumentChunk, DocumentQuery
from shu.services.kb_import_export_service import KBImportExportService
from shu.utils.embedding_codec import decode_embedding, encode_embedding


class TestEncodeDecodeEmbedding:
    """Tests for _encode_embedding and _decode_embedding round-trip."""

    def test_round_trip_preserves_values(self) -> None:
        vec = [0.1, 0.2, -0.3, 1.5, 0.0]
        encoded = encode_embedding(vec)
        decoded = decode_embedding(encoded)
        assert len(decoded) == len(vec)
        for a, b in zip(vec, decoded):
            assert math.isclose(a, b, rel_tol=1e-6)

    def test_encode_none_returns_none(self) -> None:
        assert encode_embedding(None) is None

    def test_decode_none_returns_none(self) -> None:
        assert decode_embedding(None) is None

    def test_encode_returns_string(self) -> None:
        result = encode_embedding([1.0, 2.0])
        assert isinstance(result, str)

    def test_empty_list_round_trip(self) -> None:
        encoded = encode_embedding([])
        decoded = decode_embedding(encoded)
        assert decoded == []

    def test_large_vector_round_trip(self) -> None:
        vec = [float(i) / 1000 for i in range(1024)]
        encoded = encode_embedding(vec)
        decoded = decode_embedding(encoded)
        assert len(decoded) == 1024
        for a, b in zip(vec, decoded):
            assert math.isclose(a, b, rel_tol=1e-6)


def _mock_document(export_index: int = 0) -> MagicMock:
    """Build a mock Document with all fields."""
    doc = MagicMock()
    doc.source_id = "src-1"
    doc.source_type = "plugin:google_drive"
    doc.title = "Test Doc"
    doc.file_type = "pdf"
    doc.content = "Some content"
    doc.content_hash = "abc123"
    doc.processing_status = "processed"
    doc.synopsis = "A summary"
    doc.synopsis_embedding = [0.1, 0.2, 0.3]
    doc.document_type = "technical"
    doc.capability_manifest = {"answers_questions_about": ["testing"]}
    doc.relational_context = {"participants": []}
    doc.profiling_status = "complete"
    doc.profiling_coverage_percent = 100.0
    doc.word_count = 50
    doc.character_count = 300
    doc.chunk_count = 2
    doc.extraction_method = "pymupdf"
    doc.extraction_engine = None
    doc.extraction_confidence = 0.95
    doc.extraction_duration = 1.2
    doc.extraction_metadata = {"pages": 3}
    doc.source_url = "https://example.com/doc"
    doc.source_metadata = '{"key": "val"}'
    doc.source_hash = "md5hash"
    doc.source_modified_at = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
    doc.file_size = 102400
    doc.mime_type = "application/pdf"
    doc.serialize_for_export = lambda ei, no_embeddings: Document.serialize_for_export(doc, ei, no_embeddings)
    return doc


def _mock_chunk() -> MagicMock:
    """Build a mock DocumentChunk with all fields."""
    chunk = MagicMock()
    chunk.chunk_index = 0
    chunk.content = "Chunk text"
    chunk.embedding = [0.4, 0.5, 0.6]
    chunk.summary = "Chunk summary"
    chunk.summary_embedding = [0.7, 0.8, 0.9]
    chunk.keywords = ["keyword1"]
    chunk.topics = ["topic1"]
    chunk.char_count = 100
    chunk.word_count = 20
    chunk.start_char = 0
    chunk.end_char = 100
    chunk.embedding_model = "all-MiniLM-L6-v2"
    chunk.token_count = 25
    chunk.chunk_metadata = None
    chunk.serialize_for_export = lambda ei, no_embeddings: DocumentChunk.serialize_for_export(chunk, ei, no_embeddings)
    return chunk


def _mock_query() -> MagicMock:
    """Build a mock DocumentQuery with all fields."""
    query = MagicMock()
    query.query_text = "What is the Q3 revenue?"
    query.query_embedding = [1.0, 1.1, 1.2]
    query.serialize_for_export = lambda ei, no_embeddings: DocumentQuery.serialize_for_export(query, ei, no_embeddings)
    return query


class TestSerializeDocument:
    """Tests for Document.serialize_for_export."""

    def test_serializes_all_fields(self) -> None:
        doc = _mock_document()
        result = Document.serialize_for_export(doc, 5, no_embeddings=False)
        assert result["export_index"] == 5
        assert result["title"] == "Test Doc"
        assert result["source_type"] == "plugin:google_drive"
        assert result["synopsis"] == "A summary"
        assert result["synopsis_embedding"] is not None
        assert result["capability_manifest"] == {"answers_questions_about": ["testing"]}
        assert result["source_modified_at"] == "2026-01-15T10:30:00+00:00"

    def test_no_embeddings_omits_synopsis_embedding(self) -> None:
        doc = _mock_document()
        result = Document.serialize_for_export(doc, 0, no_embeddings=True)
        assert result["synopsis_embedding"] is None
        assert result["synopsis"] == "A summary"

    def test_none_source_modified_at(self) -> None:
        doc = _mock_document()
        doc.source_modified_at = None
        result = Document.serialize_for_export(doc, 0, no_embeddings=False)
        assert result["source_modified_at"] is None


class TestSerializeChunk:
    """Tests for DocumentChunk.serialize_for_export."""

    def test_serializes_all_fields(self) -> None:
        chunk = _mock_chunk()
        result = DocumentChunk.serialize_for_export(chunk, 3, no_embeddings=False)
        assert result["export_index"] == 3
        assert result["chunk_index"] == 0
        assert result["content"] == "Chunk text"
        assert result["embedding"] is not None
        assert result["summary_embedding"] is not None
        assert result["keywords"] == ["keyword1"]

    def test_no_embeddings_omits_vectors(self) -> None:
        chunk = _mock_chunk()
        result = DocumentChunk.serialize_for_export(chunk, 0, no_embeddings=True)
        assert result["embedding"] is None
        assert result["summary_embedding"] is None
        assert result["summary"] == "Chunk summary"
        assert result["content"] == "Chunk text"


class TestSerializeQuery:
    """Tests for DocumentQuery.serialize_for_export."""

    def test_serializes_all_fields(self) -> None:
        query = _mock_query()
        result = DocumentQuery.serialize_for_export(query, 7, no_embeddings=False)
        assert result["export_index"] == 7
        assert result["query_text"] == "What is the Q3 revenue?"
        assert result["query_embedding"] is not None

    def test_no_embeddings_omits_vector(self) -> None:
        query = _mock_query()
        result = DocumentQuery.serialize_for_export(query, 0, no_embeddings=True)
        assert result["query_embedding"] is None
        assert result["query_text"] == "What is the Q3 revenue?"


def _make_zip_bytes(manifest: dict | None = None, include_manifest: bool = True) -> bytes:
    """Create a zip archive in memory with an optional manifest.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if include_manifest and manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest))
    return buf.getvalue()


def _make_upload_file(content: bytes) -> AsyncMock:
    """Create a mock UploadFile.

    - ``.file`` is a seekable BytesIO (used by validate_import).
    - ``.read(size)`` yields chunks then b"" (used by start_import's
      streaming save).
    - ``.read()`` with no args returns all bytes (legacy compat).
    """
    file = AsyncMock()
    file.file = io.BytesIO(content)
    buf = io.BytesIO(content)

    async def _chunked_read(size=None):
        if size is None:
            return content
        chunk = buf.read(size)
        return chunk if chunk else b""

    file.read = AsyncMock(side_effect=_chunked_read)
    return file


def _valid_manifest(embedding_model: str = "test-model") -> dict:
    return {
        "schema_version": "1",
        "export_timestamp": "2026-03-23T18:00:00Z",
        "kb_name": "Test KB",
        "kb_description": "A test knowledge base",
        "embedding_model": embedding_model,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "counts": {"documents": 10, "chunks": 50, "queries": 20},
    }


class TestValidateImport:
    """Tests for validate_import."""

    @pytest.mark.asyncio
    async def test_valid_archive_returns_manifest_data(self) -> None:
        manifest = _valid_manifest("test-model")
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with patch("shu.services.kb_import_export_service.get_settings_instance") as mock_settings:
            mock_settings.return_value.default_embedding_model = "test-model"
            result = await service.validate_import(file)

        assert result.name == "Test KB"
        assert result.description == "A test knowledge base"
        assert result.embedding_model == "test-model"
        assert result.chunk_size == 1000
        assert result.chunk_overlap == 200
        assert result.schema_version == "1"
        assert result.document_count == 10
        assert result.chunk_count == 50
        assert result.query_count == 20

    @pytest.mark.asyncio
    async def test_embedding_model_match_true(self) -> None:
        manifest = _valid_manifest("same-model")
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with patch("shu.services.kb_import_export_service.get_settings_instance") as mock_settings:
            mock_settings.return_value.default_embedding_model = "same-model"
            result = await service.validate_import(file)

        assert result.embedding_model_match is True
        assert result.instance_embedding_model == "same-model"

    @pytest.mark.asyncio
    async def test_embedding_model_mismatch(self) -> None:
        manifest = _valid_manifest("archive-model")
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with patch("shu.services.kb_import_export_service.get_settings_instance") as mock_settings:
            mock_settings.return_value.default_embedding_model = "instance-model"
            result = await service.validate_import(file)

        assert result.embedding_model_match is False
        assert result.embedding_model == "archive-model"
        assert result.instance_embedding_model == "instance-model"

    @pytest.mark.asyncio
    async def test_invalid_zip_raises_validation_error(self) -> None:
        file = _make_upload_file(b"this is not a zip file")

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with pytest.raises(ValidationError, match="not a valid zip"):
            await service.validate_import(file)

    @pytest.mark.asyncio
    async def test_missing_manifest_raises_validation_error(self) -> None:
        content = _make_zip_bytes(include_manifest=False)
        file = _make_upload_file(content)

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with pytest.raises(ValidationError, match="missing manifest.json"):
            await service.validate_import(file)

    @pytest.mark.asyncio
    async def test_unsupported_schema_version_raises_validation_error(self) -> None:
        manifest = _valid_manifest()
        manifest["schema_version"] = "99"
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = AsyncMock()
        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        with pytest.raises(ValidationError, match="Unsupported schema version: 99"):
            await service.validate_import(file)


def _mock_kb() -> MagicMock:
    """Build a mock KnowledgeBase for export tests."""
    kb = MagicMock()
    kb.name = "Export Test KB"
    kb.slug = "export-test-kb"
    kb.description = "KB for export tests"
    kb.embedding_model = "test-model"
    kb.chunk_size = 512
    kb.chunk_overlap = 64
    kb.get_rag_config.return_value = {"search_type": "hybrid", "version": "1.0"}
    return kb


def _mock_scalars_result(docs: list) -> MagicMock:
    """Build a mock for result.scalars().all()."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = docs
    result.scalars.return_value = scalars
    return result


def _export_side_effects(docs, chunks, queries):
    """Build db.execute side_effect list for the three-pass export.

    Each pass does batch+empty, so 6 results total.
    """
    return [
        _mock_scalars_result(docs),
        _mock_scalars_result([]),
        _mock_scalars_result(chunks),
        _mock_scalars_result([]),
        _mock_scalars_result(queries),
        _mock_scalars_result([]),
    ]


class TestExportKB:
    """Tests for export_kb."""

    @pytest.mark.asyncio
    async def test_creates_zip_with_all_files(self) -> None:
        import os

        doc = _mock_document()
        doc.id = "doc-1"
        chunk = _mock_chunk()
        chunk.document_id = "doc-1"
        query = _mock_query()
        query.document_id = "doc-1"

        kb = _mock_kb()
        kb_service = AsyncMock()
        kb_service.get_knowledge_base = AsyncMock(return_value=kb)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_export_side_effects([doc], [chunk], [query]))

        service = KBImportExportService(db, kb_service)
        temp_path, filename = await service.export_kb("kb-1", "user-1")

        try:
            assert filename == "export-test-kb-export.zip"
            assert os.path.exists(temp_path)

            with zipfile.ZipFile(temp_path, "r") as zf:
                names = zf.namelist()
                assert "manifest.json" in names
                assert "documents.jsonl" in names
                assert "chunks.jsonl" in names
                assert "queries.jsonl" in names
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @pytest.mark.asyncio
    async def test_manifest_has_correct_counts(self) -> None:
        import os

        doc1 = _mock_document()
        doc1.id = "doc-1"
        doc2 = _mock_document()
        doc2.id = "doc-2"

        c1 = _mock_chunk()
        c1.document_id = "doc-1"
        c2 = _mock_chunk()
        c2.document_id = "doc-1"
        c3 = _mock_chunk()
        c3.document_id = "doc-2"

        q1 = _mock_query()
        q1.document_id = "doc-1"
        q2 = _mock_query()
        q2.document_id = "doc-2"
        q3 = _mock_query()
        q3.document_id = "doc-2"

        kb = _mock_kb()
        kb_service = AsyncMock()
        kb_service.get_knowledge_base = AsyncMock(return_value=kb)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=_export_side_effects([doc1, doc2], [c1, c2, c3], [q1, q2, q3])
        )

        service = KBImportExportService(db, kb_service)
        temp_path, _ = await service.export_kb("kb-1", "user-1")

        try:
            with zipfile.ZipFile(temp_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json"))
                assert manifest["counts"]["documents"] == 2
                assert manifest["counts"]["chunks"] == 3
                assert manifest["counts"]["queries"] == 3
                assert manifest["kb_name"] == "Export Test KB"
                assert manifest["embedding_model"] == "test-model"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @pytest.mark.asyncio
    async def test_export_index_is_sequential(self) -> None:
        import os

        doc1 = _mock_document()
        doc1.id = "doc-1"
        doc2 = _mock_document()
        doc2.id = "doc-2"

        c1 = _mock_chunk()
        c1.document_id = "doc-1"
        c2 = _mock_chunk()
        c2.document_id = "doc-2"

        kb = _mock_kb()
        kb_service = AsyncMock()
        kb_service.get_knowledge_base = AsyncMock(return_value=kb)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_export_side_effects([doc1, doc2], [c1, c2], []))

        service = KBImportExportService(db, kb_service)
        temp_path, _ = await service.export_kb("kb-1", "user-1")

        try:
            with zipfile.ZipFile(temp_path, "r") as zf:
                doc_lines = zf.read("documents.jsonl").decode().strip().split("\n")
                assert len(doc_lines) == 2
                assert json.loads(doc_lines[0])["export_index"] == 0
                assert json.loads(doc_lines[1])["export_index"] == 1

                chunk_lines = zf.read("chunks.jsonl").decode().strip().split("\n")
                assert json.loads(chunk_lines[0])["export_index"] == 0
                assert json.loads(chunk_lines[1])["export_index"] == 1
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @pytest.mark.asyncio
    async def test_no_embeddings_omits_vectors(self) -> None:
        import os

        doc = _mock_document()
        doc.id = "doc-1"
        chunk = _mock_chunk()
        chunk.document_id = "doc-1"
        query = _mock_query()
        query.document_id = "doc-1"

        kb = _mock_kb()
        kb_service = AsyncMock()
        kb_service.get_knowledge_base = AsyncMock(return_value=kb)

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_export_side_effects([doc], [chunk], [query]))

        service = KBImportExportService(db, kb_service)
        temp_path, _ = await service.export_kb("kb-1", "user-1", no_embeddings=True)

        try:
            with zipfile.ZipFile(temp_path, "r") as zf:
                doc_data = json.loads(zf.read("documents.jsonl").decode().strip())
                assert doc_data["synopsis_embedding"] is None
                assert doc_data["synopsis"] == "A summary"

                chunk_data = json.loads(zf.read("chunks.jsonl").decode().strip())
                assert chunk_data["embedding"] is None
                assert chunk_data["summary_embedding"] is None

                query_data = json.loads(zf.read("queries.jsonl").decode().strip())
                assert query_data["query_embedding"] is None
                assert query_data["query_text"] == "What is the Q3 revenue?"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


def _make_export_archive(
    docs: list[dict] | None = None,
    chunks: list[dict] | None = None,
    queries: list[dict] | None = None,
    manifest_overrides: dict | None = None,
) -> str:
    """Create a real export zip archive on disk and return its path."""
    import tempfile
    import uuid

    manifest = {
        "schema_version": "1",
        "export_timestamp": "2026-03-23T18:00:00Z",
        "kb_name": "Import Test KB",
        "kb_description": "A test KB",
        "embedding_model": "test-model",
        "chunk_size": 512,
        "chunk_overlap": 64,
        "rag_config": {"search_type": "hybrid"},
        "counts": {
            "documents": len(docs or []),
            "chunks": len(chunks or []),
            "queries": len(queries or []),
        },
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)

    path = f"{tempfile.gettempdir()}/shu-test-{uuid.uuid4()}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if docs is not None:
            zf.writestr("documents.jsonl", "\n".join(json.dumps(d) for d in docs) + "\n")
        if chunks is not None:
            zf.writestr("chunks.jsonl", "\n".join(json.dumps(c) for c in chunks) + "\n")
        if queries is not None:
            zf.writestr("queries.jsonl", "\n".join(json.dumps(q) for q in queries) + "\n")
    return path


class TestResolveSlug:
    """Tests for _resolve_slug."""

    @pytest.mark.asyncio
    async def test_no_conflict_returns_base(self) -> None:
        db = AsyncMock()
        kb_service = MagicMock()
        kb_service.slug_exists = AsyncMock(return_value=False)
        service = KBImportExportService(db, kb_service)
        result = await service._resolve_slug("my-kb")
        assert result == "my-kb"

    @pytest.mark.asyncio
    async def test_appends_hash_on_conflict(self) -> None:
        db = AsyncMock()
        kb_service = MagicMock()
        kb_service.slug_exists = AsyncMock(return_value=True)
        service = KBImportExportService(db, kb_service)
        result = await service._resolve_slug("my-kb")
        assert result.startswith("my-kb-")
        assert len(result) == len("my-kb-") + 6  # 6-char hex suffix


def _make_mock_db_for_import() -> MagicMock:
    """Create a mock DB session suitable for start_import.

    db.add is synchronous, db.commit and db.refresh are async.
    db.refresh populates kb.id (simulating what the DB does on flush).
    """
    import uuid as _uuid

    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def _fake_refresh(obj):
        if getattr(obj, "id", None) is None:
            obj.id = str(_uuid.uuid4())

    db.refresh = AsyncMock(side_effect=_fake_refresh)
    return db


class TestStartImport:
    """Tests for start_import."""

    @pytest.mark.asyncio
    async def test_creates_kb_and_enqueues_job(self) -> None:
        manifest = _valid_manifest("test-model")
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = _make_mock_db_for_import()
        kb_service = MagicMock()
        kb_service.slug_exists = AsyncMock(return_value=False)
        queue = AsyncMock()

        service = KBImportExportService(db, kb_service, queue=queue)

        with (
            patch("shu.services.kb_import_export_service.enqueue_job", new_callable=AsyncMock),
            patch("shu.services.kb_import_export_service.get_settings_instance") as mock_settings,
        ):
            mock_settings.return_value.default_embedding_model = "test-model"
            result = await service.start_import(file, skip_embeddings=False, owner_id="user-1")

        assert result.name == "Test KB"
        assert result.status == "importing"
        assert result.slug == "test-kb"
        db.add.assert_called_once()
        db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_start_import_with_slug_conflict(self) -> None:
        manifest = _valid_manifest("test-model")
        content = _make_zip_bytes(manifest)
        file = _make_upload_file(content)

        db = _make_mock_db_for_import()
        kb_service = MagicMock()
        kb_service.slug_exists = AsyncMock(return_value=True)  # slug always taken
        queue = AsyncMock()

        service = KBImportExportService(db, kb_service, queue=queue)

        with (
            patch("shu.services.kb_import_export_service.enqueue_job", new_callable=AsyncMock),
            patch("shu.services.kb_import_export_service.get_settings_instance") as mock_settings,
        ):
            mock_settings.return_value.default_embedding_model = "test-model"
            result = await service.start_import(file, skip_embeddings=False, owner_id="user-1")

        assert result.slug.startswith("test-kb-")
        assert len(result.slug) > len("test-kb")


class TestBuildImportRecord:
    """Tests for Document.build_import_record."""

    def test_preserves_profiling_artifacts(self) -> None:
        row = {
            "synopsis": "A summary",
            "document_type": "technical",
            "capability_manifest": {"key": "val"},
            "relational_context": {"participants": []},
            "profiling_status": "complete",
            "profiling_coverage_percent": 95.0,
        }
        record = Document.build_import_record(row, "new-id", "kb-1", skip_embeddings=False)

        assert record["synopsis"] == "A summary"
        assert record["document_type"] == "technical"
        assert record["capability_manifest"] == {"key": "val"}
        assert record["profiling_status"] == "complete"
        assert record["profiling_coverage_percent"] == 95.0

    def test_skip_embeddings_nulls_embedding_and_sets_pending(self) -> None:
        encoded = encode_embedding([0.1, 0.2])
        row = {"synopsis_embedding": encoded, "processing_status": "processed"}
        record = Document.build_import_record(row, "new-id", "kb-1", skip_embeddings=True)

        assert record["synopsis_embedding"] is None
        assert record["processing_status"] == "pending"

    def test_without_skip_decodes_embedding(self) -> None:
        encoded = encode_embedding([0.1, 0.2])
        row = {"synopsis_embedding": encoded, "processing_status": "processed"}
        record = Document.build_import_record(row, "new-id", "kb-1", skip_embeddings=False)

        assert record["synopsis_embedding"] is not None
        assert len(record["synopsis_embedding"]) == 2
        assert record["processing_status"] == "processed"


class TestFinalizeImport:
    """Tests for _finalize_import."""

    @pytest.mark.asyncio
    async def test_sets_active_status_and_counts(self) -> None:
        kb = MagicMock()
        kb.import_progress = {"phase": "queries"}

        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = kb

        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_result)

        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        archive_path = _make_export_archive(docs=[])

        await service._finalize_import("kb-1", 10, 50, skip_embeddings=False, archive_path=archive_path)

        assert kb.status == "active"
        assert kb.document_count == 10
        assert kb.total_chunks == 50
        assert kb.embedding_status == "current"
        assert not os.path.exists(archive_path)

    @pytest.mark.asyncio
    async def test_stale_embedding_status_when_skipped(self) -> None:
        kb = MagicMock()
        kb.import_progress = {}

        scalar_result = MagicMock()
        scalar_result.scalar_one.return_value = kb

        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_result)

        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        archive_path = _make_export_archive(docs=[])

        await service._finalize_import("kb-1", 5, 20, skip_embeddings=True, archive_path=archive_path)

        assert kb.embedding_status == "stale"
        assert not os.path.exists(archive_path)


class TestMarkImportFailed:
    """Tests for _mark_import_failed."""

    @pytest.mark.asyncio
    async def test_sets_error_status_and_cleans_up(self) -> None:
        kb = MagicMock()
        kb.import_progress = {"phase": "documents"}

        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = kb

        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar_result)

        kb_service = MagicMock()
        service = KBImportExportService(db, kb_service)

        archive_path = _make_export_archive(docs=[])
        assert os.path.exists(archive_path)

        await service._mark_import_failed("kb-1", "something broke", archive_path)

        assert kb.status == "error"
        assert "something broke" in kb.import_progress["error"]
        assert not os.path.exists(archive_path)
        db.commit.assert_called()
