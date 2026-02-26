"""Unit tests for KbSearchService.

Tests field validation, operator validation, search result shaping,
pagination, and get_document access control.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.services.kb_search_service import (
    CHUNK_SEARCHABLE_FIELDS,
    DOCUMENT_SEARCHABLE_FIELDS,
    JSONB_ARRAY_OPERATORS,
    JSONB_OBJECT_OPERATORS,
    PAGE_SIZE,
    TEXT_OPERATORS,
    KbSearchService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    return AsyncMock()


@pytest.fixture
def service(mock_db):
    """Create a KbSearchService with a mocked DB session."""
    return KbSearchService(mock_db)


# ---------------------------------------------------------------------------
# Static helper methods
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for KbSearchService static helper methods."""

    def test_get_operator_fn_text_eq(self):
        """Should return a callable for text/eq."""
        fn = KbSearchService._get_operator_fn("text", "eq")
        assert fn is not None

    def test_get_operator_fn_text_invalid(self):
        """Should return None for an invalid text operator."""
        fn = KbSearchService._get_operator_fn("text", "nonexistent")
        assert fn is None

    def test_get_operator_fn_jsonb_array_contains(self):
        """Should return a callable for jsonb_array/contains."""
        fn = KbSearchService._get_operator_fn("jsonb_array", "contains")
        assert fn is not None

    def test_get_operator_fn_jsonb_object_path_contains(self):
        """Should return a callable for jsonb_object/path_contains."""
        fn = KbSearchService._get_operator_fn("jsonb_object", "path_contains")
        assert fn is not None

    def test_get_operator_fn_unknown_type(self):
        """Should return None for an unknown field type."""
        fn = KbSearchService._get_operator_fn("unknown_type", "eq")
        assert fn is None

    def test_valid_operators_text(self):
        """Should return text operator names."""
        ops = KbSearchService._valid_operators_for_type("text")
        assert set(ops) == {"eq", "contains", "icontains"}

    def test_valid_operators_jsonb_array(self):
        """Should return JSONB array operator names."""
        ops = KbSearchService._valid_operators_for_type("jsonb_array")
        assert set(ops) == {"contains", "has_key", "has_any"}

    def test_valid_operators_jsonb_object(self):
        """Should return JSONB object operator names."""
        ops = KbSearchService._valid_operators_for_type("jsonb_object")
        assert set(ops) == {"contains", "has_key", "path_contains"}

    def test_valid_operators_unknown(self):
        """Should return empty list for unknown type."""
        ops = KbSearchService._valid_operators_for_type("bogus")
        assert ops == []

    def test_serialize_datetime_none(self):
        """Should return None for None input."""
        assert KbSearchService._serialize_datetime(None) is None

    def test_serialize_datetime_with_datetime(self):
        """Should return ISO string for a datetime object."""
        dt = datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = KbSearchService._serialize_datetime(dt)
        assert "2025-01-15" in result
        assert isinstance(result, str)

    def test_serialize_datetime_with_string(self):
        """Should return str() for non-datetime non-None values."""
        result = KbSearchService._serialize_datetime("already a string")
        assert result == "already a string"

    def test_error_dict_structure(self):
        """_error_dict should return structured error with status, code, and message."""
        result = KbSearchService._error_dict("some_code", "something broke")
        assert result["status"] == "error"
        assert result["error"]["code"] == "some_code"
        assert result["error"]["message"] == "something broke"


# ---------------------------------------------------------------------------
# search_chunks
# ---------------------------------------------------------------------------


class TestSearchChunks:
    """Tests for KbSearchService.search_chunks."""

    @pytest.mark.asyncio
    async def test_invalid_field_returns_error(self, service):
        """Should return error dict when field is not in CHUNK_SEARCHABLE_FIELDS."""
        result = await service.search_chunks(["kb1"], "nonexistent", "eq", "val")
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_field"
        assert "Invalid field" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_operator_returns_error(self, service):
        """Should return error dict when operator is invalid for the field type."""
        result = await service.search_chunks(["kb1"], "content", "has_key", "val")
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_operator"
        assert "Invalid operator" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_operator_for_jsonb_field(self, service):
        """Should return error dict when using text operator on JSONB array field."""
        result = await service.search_chunks(["kb1"], "keywords", "eq", "val")
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_operator"

    @pytest.mark.asyncio
    async def test_successful_search_returns_page_structure(self, service, mock_db):
        """Should return dict with results, total_results, page, page_size."""
        # Setup mock for count query
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        # Setup mock chunk
        mock_chunk = MagicMock()
        mock_chunk.id = "chunk-1"
        mock_chunk.document_id = "doc-1"
        mock_chunk.knowledge_base_id = "kb-1"
        mock_chunk.chunk_index = 0
        mock_chunk.summary = "A summary"
        mock_chunk.keywords = ["python"]
        mock_chunk.topics = ["programming"]
        mock_chunk.char_count = 500
        mock_chunk.word_count = 80
        mock_chunk.token_count = 100
        mock_chunk.start_char = 0
        mock_chunk.end_char = 500
        mock_chunk.embedding_model = "all-MiniLM-L6-v2"
        mock_chunk.created_at = datetime(2025, 1, 1, tzinfo=UTC)

        # Row result: tuple of (chunk, kb_name)
        rows_result = MagicMock()
        rows_result.all.return_value = [(mock_chunk, "My KB")]

        # First call -> count, second call -> rows
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "hello")

        assert result.get("status") != "error"
        assert result["total_results"] == 1
        assert result["page"] == 1
        assert result["page_size"] == PAGE_SIZE
        assert len(result["results"]) == 1

        chunk_result = result["results"][0]
        assert chunk_result["document_id"] == "doc-1"
        assert chunk_result["knowledge_base_name"] == "My KB"
        assert chunk_result["summary"] == "A summary"
        assert chunk_result["keywords"] == ["python"]
        # Excludes content and embedding per spec
        assert "content" not in chunk_result
        assert "embedding" not in chunk_result

    @pytest.mark.asyncio
    async def test_pagination_default_page_1(self, service, mock_db):
        """Default page should be 1."""
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        rows_result = MagicMock()
        rows_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "hello")
        assert result["page"] == 1

    @pytest.mark.asyncio
    async def test_pagination_page_2(self, service, mock_db):
        """Should respect page parameter."""
        count_result = MagicMock()
        count_result.scalar.return_value = 25
        rows_result = MagicMock()
        rows_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "hello", page=2)
        assert result["page"] == 2
        assert result["total_results"] == 25
        assert result["results"] == []


# ---------------------------------------------------------------------------
# search_documents
# ---------------------------------------------------------------------------


class TestSearchDocuments:
    """Tests for KbSearchService.search_documents."""

    @pytest.mark.asyncio
    async def test_invalid_field_returns_error(self, service):
        """Should return error dict when field is not in DOCUMENT_SEARCHABLE_FIELDS."""
        result = await service.search_documents(["kb1"], "bogus_field", "eq", "val")
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_field"
        assert "Invalid field" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_operator_returns_error(self, service):
        """Should return error dict when operator is invalid for field type."""
        result = await service.search_documents(["kb1"], "title", "has_any", ["a", "b"])
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_operator"

    @pytest.mark.asyncio
    async def test_invalid_operator_for_jsonb_object(self, service):
        """Should return error when using array operator on object field."""
        result = await service.search_documents(["kb1"], "capability_manifest", "has_any", ["x"])
        assert result["status"] == "error"
        assert result["error"]["code"] == "invalid_operator"

    @pytest.mark.asyncio
    async def test_successful_search_returns_page_structure(self, service, mock_db):
        """Should return dict with results, total_results, page, page_size."""
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        mock_doc = MagicMock()
        mock_doc.id = "doc-1"
        mock_doc.knowledge_base_id = "kb-1"
        mock_doc.title = "Test Doc"
        mock_doc.source_type = "filesystem"
        mock_doc.file_type = "pdf"
        mock_doc.file_size = 1024
        mock_doc.mime_type = "application/pdf"
        mock_doc.source_url = "file:///test.pdf"
        mock_doc.source_modified_at = None
        mock_doc.processing_status = "processed"
        mock_doc.synopsis = "A test document"
        mock_doc.document_type = "technical"
        mock_doc.capability_manifest = {"answers_questions_about": ["testing"]}
        mock_doc.relational_context = None
        mock_doc.profiling_status = "complete"
        mock_doc.word_count = 200
        mock_doc.character_count = 1000
        mock_doc.chunk_count = 3
        mock_doc.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        mock_doc.processed_at = datetime(2025, 1, 2, tzinfo=UTC)

        rows_result = MagicMock()
        rows_result.all.return_value = [(mock_doc, "My KB")]

        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_documents(["kb-1"], "title", "icontains", "test")

        assert result.get("status") != "error"
        assert result["total_results"] == 1
        assert result["page"] == 1
        assert result["page_size"] == PAGE_SIZE
        assert len(result["results"]) == 1

        doc_result = result["results"][0]
        assert doc_result["id"] == "doc-1"
        assert doc_result["knowledge_base_name"] == "My KB"
        assert doc_result["title"] == "Test Doc"
        # Document search excludes content per spec
        assert "content" not in doc_result

    @pytest.mark.asyncio
    async def test_empty_results(self, service, mock_db):
        """Should return empty results list with total_results=0."""
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        rows_result = MagicMock()
        rows_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_documents(["kb-1"], "title", "eq", "nonexistent")

        assert result["total_results"] == 0
        assert result["results"] == []
        assert result["page_size"] == PAGE_SIZE


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    """Tests for KbSearchService.get_document."""

    @pytest.mark.asyncio
    async def test_document_not_found(self, service, mock_db):
        """Should return error dict when document does not exist."""
        result_mock = MagicMock()
        result_mock.one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_document(["kb-1"], "nonexistent-doc")

        assert result["status"] == "error"
        assert result["error"]["code"] == "not_found"
        assert "not found" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_document_in_unbound_kb(self, service, mock_db):
        """Should return not_found when document's KB is not in the bound list.

        Access control is enforced at the SQL level (WHERE knowledge_base_id IN ...),
        so a document in an unbound KB is filtered out by the query and appears
        identical to a non-existent document.
        """
        result_mock = MagicMock()
        result_mock.one_or_none.return_value = None  # filtered out by SQL WHERE clause
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_document(["kb-1"], "doc-1")

        assert result["status"] == "error"
        assert result["error"]["code"] == "not_found"
        assert "not found" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_successful_retrieval_includes_content(self, service, mock_db):
        """Should return full document record including content."""
        mock_doc = MagicMock()
        mock_doc.id = "doc-1"
        mock_doc.knowledge_base_id = "kb-1"
        mock_doc.title = "Full Doc"
        mock_doc.content = "Full document text here."
        mock_doc.source_type = "filesystem"
        mock_doc.file_type = "txt"
        mock_doc.file_size = 25
        mock_doc.mime_type = "text/plain"
        mock_doc.source_url = None
        mock_doc.source_modified_at = None
        mock_doc.processing_status = "processed"
        mock_doc.synopsis = "A synopsis"
        mock_doc.document_type = "narrative"
        mock_doc.capability_manifest = None
        mock_doc.relational_context = None
        mock_doc.profiling_status = "complete"
        mock_doc.word_count = 4
        mock_doc.character_count = 25
        mock_doc.chunk_count = 1
        mock_doc.created_at = datetime(2025, 6, 1, tzinfo=UTC)
        mock_doc.processed_at = datetime(2025, 6, 2, tzinfo=UTC)

        result_mock = MagicMock()
        result_mock.one_or_none.return_value = (mock_doc, "My KB")
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_document(["kb-1"], "doc-1")

        assert result.get("status") != "error"
        assert result["id"] == "doc-1"
        assert result["content"] == "Full document text here."
        assert result["knowledge_base_name"] == "My KB"
        assert result["title"] == "Full Doc"

    @pytest.mark.asyncio
    async def test_document_with_multiple_bound_kbs(self, service, mock_db):
        """Should succeed when document's KB is one of several bound KBs."""
        mock_doc = MagicMock()
        mock_doc.id = "doc-1"
        mock_doc.knowledge_base_id = "kb-2"
        mock_doc.title = "Doc"
        mock_doc.content = "Content"
        mock_doc.source_type = "filesystem"
        mock_doc.file_type = "txt"
        mock_doc.file_size = 7
        mock_doc.mime_type = "text/plain"
        mock_doc.source_url = None
        mock_doc.source_modified_at = None
        mock_doc.processing_status = "processed"
        mock_doc.synopsis = None
        mock_doc.document_type = None
        mock_doc.capability_manifest = None
        mock_doc.relational_context = None
        mock_doc.profiling_status = None
        mock_doc.word_count = 1
        mock_doc.character_count = 7
        mock_doc.chunk_count = 1
        mock_doc.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        mock_doc.processed_at = None

        result_mock = MagicMock()
        result_mock.one_or_none.return_value = (mock_doc, "KB Two")
        mock_db.execute = AsyncMock(return_value=result_mock)

        result = await service.get_document(["kb-1", "kb-2", "kb-3"], "doc-1")

        assert result.get("status") != "error"
        assert result["id"] == "doc-1"
        assert result["knowledge_base_name"] == "KB Two"


# ---------------------------------------------------------------------------
# _build_path_contains
# ---------------------------------------------------------------------------


class TestBuildPathContains:
    """Tests for KbSearchService._build_path_contains."""

    def test_returns_expression_for_list_value(self):
        """Should return a SQLAlchemy expression without raising for a list value."""
        col = MagicMock()
        path_result = MagicMock()
        col.__getitem__ = MagicMock(return_value=path_result)
        val = {"path": "answers_questions_about", "value": ["newsletter"]}

        expr = KbSearchService._build_path_contains(col, val)

        col.__getitem__.assert_called_once_with("answers_questions_about")
        assert expr is not None

    def test_returns_expression_for_string_value(self):
        """Should handle scalar string values in path."""
        col = MagicMock()
        path_result = MagicMock()
        col.__getitem__ = MagicMock(return_value=path_result)
        val = {"path": "category", "value": "news"}

        expr = KbSearchService._build_path_contains(col, val)

        col.__getitem__.assert_called_once_with("category")
        assert expr is not None

    def test_returns_expression_for_dict_value(self):
        """Should handle nested dict values in path."""
        col = MagicMock()
        path_result = MagicMock()
        col.__getitem__ = MagicMock(return_value=path_result)
        val = {"path": "meta", "value": {"key": "v"}}

        expr = KbSearchService._build_path_contains(col, val)

        col.__getitem__.assert_called_once_with("meta")
        assert expr is not None


# ---------------------------------------------------------------------------
# Operator combination tests (search_chunks)
# ---------------------------------------------------------------------------


def _configure_empty_db(mock_db: AsyncMock) -> None:
    """Set up mock_db.execute to return count=0 and an empty row list."""
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    rows_result = MagicMock()
    rows_result.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])


class TestSearchChunksOperatorCombinations:
    """Tests that each valid field/operator combination does not return an error."""

    # -- text field: content --

    @pytest.mark.asyncio
    async def test_content_eq(self, service, mock_db):
        """content/eq should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "content", "eq", "hello")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_content_contains(self, service, mock_db):
        """content/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "content", "contains", "hello")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_content_icontains(self, service, mock_db):
        """content/icontains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "content", "icontains", "hello")
        assert result.get("status") != "error"

    # -- text field: summary --

    @pytest.mark.asyncio
    async def test_summary_eq(self, service, mock_db):
        """summary/eq should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "summary", "eq", "intro")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_summary_contains(self, service, mock_db):
        """summary/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "summary", "contains", "intro")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_summary_icontains(self, service, mock_db):
        """summary/icontains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "summary", "icontains", "intro")
        assert result.get("status") != "error"

    # -- jsonb_array field: keywords --

    @pytest.mark.asyncio
    async def test_keywords_contains(self, service, mock_db):
        """keywords/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "keywords", "contains", ["python"])
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_keywords_has_key(self, service, mock_db):
        """keywords/has_key should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "keywords", "has_key", "python")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_keywords_has_any(self, service, mock_db):
        """keywords/has_any should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "keywords", "has_any", ["python", "sql"])
        assert result.get("status") != "error"

    # -- jsonb_array field: topics --

    @pytest.mark.asyncio
    async def test_topics_contains(self, service, mock_db):
        """topics/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "topics", "contains", ["databases"])
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_topics_has_key(self, service, mock_db):
        """topics/has_key should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "topics", "has_key", "databases")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_topics_has_any(self, service, mock_db):
        """topics/has_any should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_chunks(["kb-1"], "topics", "has_any", ["databases", "ml"])
        assert result.get("status") != "error"


# ---------------------------------------------------------------------------
# Operator combination tests (search_documents)
# ---------------------------------------------------------------------------


class TestSearchDocumentsOperatorCombinations:
    """Tests that each valid field/operator combination does not return an error."""

    # -- text field: title --

    @pytest.mark.asyncio
    async def test_title_eq(self, service, mock_db):
        """title/eq should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "title", "eq", "News")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_title_contains(self, service, mock_db):
        """title/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "title", "contains", "News")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_title_icontains(self, service, mock_db):
        """title/icontains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "title", "icontains", "news")
        assert result.get("status") != "error"

    # -- text field: content --

    @pytest.mark.asyncio
    async def test_content_eq(self, service, mock_db):
        """content/eq should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "content", "eq", "exact text")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_content_contains(self, service, mock_db):
        """content/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "content", "contains", "text")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_content_icontains(self, service, mock_db):
        """content/icontains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "content", "icontains", "text")
        assert result.get("status") != "error"

    # -- text field: synopsis --

    @pytest.mark.asyncio
    async def test_synopsis_eq(self, service, mock_db):
        """synopsis/eq should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "synopsis", "eq", "A summary")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_synopsis_contains(self, service, mock_db):
        """synopsis/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "synopsis", "contains", "summary")
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_synopsis_icontains(self, service, mock_db):
        """synopsis/icontains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(["kb-1"], "synopsis", "icontains", "summary")
        assert result.get("status") != "error"

    # -- jsonb_object field: capability_manifest --

    @pytest.mark.asyncio
    async def test_capability_manifest_contains(self, service, mock_db):
        """capability_manifest/contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(
            ["kb-1"], "capability_manifest", "contains", {"answers_questions_about": ["news"]}
        )
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_capability_manifest_has_key(self, service, mock_db):
        """capability_manifest/has_key should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(
            ["kb-1"], "capability_manifest", "has_key", "answers_questions_about"
        )
        assert result.get("status") != "error"

    @pytest.mark.asyncio
    async def test_capability_manifest_path_contains(self, service, mock_db):
        """capability_manifest/path_contains should succeed."""
        _configure_empty_db(mock_db)
        result = await service.search_documents(
            ["kb-1"],
            "capability_manifest",
            "path_contains",
            {"path": "answers_questions_about", "value": ["newsletter"]},
        )
        assert result.get("status") != "error"


# ---------------------------------------------------------------------------
# Pagination edge cases
# ---------------------------------------------------------------------------


class TestPagination:
    """Comprehensive pagination tests for search_chunks and search_documents."""

    def _make_chunk_row(self, idx: int) -> tuple:
        """Return a (mock_chunk, kb_name) tuple for use in rows_result.all()."""
        chunk = MagicMock()
        chunk.id = f"chunk-{idx}"
        chunk.document_id = "doc-1"
        chunk.knowledge_base_id = "kb-1"
        chunk.chunk_index = idx
        chunk.summary = None
        chunk.keywords = None
        chunk.topics = None
        chunk.char_count = 100
        chunk.word_count = 10
        chunk.token_count = 15
        chunk.start_char = 0
        chunk.end_char = 100
        chunk.embedding_model = None
        chunk.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        return (chunk, "Test KB")

    @pytest.mark.asyncio
    async def test_page_1_returns_up_to_page_size_results(self, service, mock_db):
        """Page 1 should return up to PAGE_SIZE rows when total > PAGE_SIZE."""
        count_result = MagicMock()
        count_result.scalar.return_value = 25
        rows_result = MagicMock()
        rows_result.all.return_value = [self._make_chunk_row(i) for i in range(PAGE_SIZE)]
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "x", page=1)

        assert result["page"] == 1
        assert result["total_results"] == 25
        assert len(result["results"]) == PAGE_SIZE

    @pytest.mark.asyncio
    async def test_page_2_returns_remaining_results(self, service, mock_db):
        """Page 2 should return the remainder when total > PAGE_SIZE."""
        count_result = MagicMock()
        count_result.scalar.return_value = 25
        rows_result = MagicMock()
        rows_result.all.return_value = [self._make_chunk_row(i) for i in range(5)]
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "x", page=2)

        assert result["page"] == 2
        assert result["total_results"] == 25
        assert len(result["results"]) == 5

    @pytest.mark.asyncio
    async def test_page_beyond_data_returns_empty_list(self, service, mock_db):
        """A page past the last result should return an empty results list."""
        count_result = MagicMock()
        count_result.scalar.return_value = 5
        rows_result = MagicMock()
        rows_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "content", "eq", "x", page=99)

        assert result["page"] == 99
        assert result["total_results"] == 5
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_total_results_reflects_all_matches_not_just_page(self, service, mock_db):
        """total_results should be the full count, not just the page count."""
        count_result = MagicMock()
        count_result.scalar.return_value = 100
        rows_result = MagicMock()
        rows_result.all.return_value = [self._make_chunk_row(i) for i in range(PAGE_SIZE)]
        mock_db.execute = AsyncMock(side_effect=[count_result, rows_result])

        result = await service.search_chunks(["kb-1"], "summary", "icontains", "x", page=1)

        assert result["total_results"] == 100
        assert len(result["results"]) == PAGE_SIZE
        assert result["page_size"] == PAGE_SIZE
