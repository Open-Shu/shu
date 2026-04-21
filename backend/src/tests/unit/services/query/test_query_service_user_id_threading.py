"""SHU-718 regression tests: user_id must reach the embedding service on
every RAG retrieval path.

Before SHU-718, similarity / keyword / hybrid / multi_surface all called
``embedding_service.embed_query(...)`` without forwarding ``user_id``, so the
resulting ``llm_usage`` row for request_type='embedding' landed with NULL
user_id despite ``conversation.user_id`` being in scope at the chat boundary.

These tests guard both the explicit forward (``user_id="u-1"`` reaches
``embed_query``) and the default-None behaviour (legacy callers don't
break).

Test structure mirrors SHU-700's ``TestUserIdThreading`` pattern in
``test_extract_text_with_ocr_fallback.py`` and
``test_ingestion_service_properties.py::TestIngestEmailUserIdThreading``.

Patch note: the mixin modules re-import ``get_embedding_service`` /
``get_vector_store`` inside function bodies (deferred imports to avoid
loading sentence-transformers at module load). Patches therefore target the
source modules (``shu.core.embedding_service``, ``shu.core.vector_store``)
rather than the mixin modules themselves.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.schemas.query import QueryRequest, SimilaritySearchRequest
from shu.services.query_service import QueryService


def _make_kb_mock(status: str = "current", embedding_model: str = "test-model") -> MagicMock:
    kb = MagicMock()
    kb.embedding_status = status
    kb.embedding_model = embedding_model
    kb.get_rag_config = MagicMock(return_value={"max_chunks_per_document": 2})
    return kb


def _make_query_service() -> QueryService:
    config_manager = MagicMock()
    config_manager.get_rag_max_chunks.return_value = 10
    config_manager.get_rag_search_type.return_value = "hybrid"
    config_manager.get_rag_search_threshold.return_value = 0.0
    config_manager.get_title_weighting_enabled.return_value = True
    config_manager.get_title_weight_multiplier.return_value = 1.0
    config_manager.get_hybrid_similarity_weight.return_value = 0.7
    config_manager.get_hybrid_keyword_weight.return_value = 0.3

    db = AsyncMock()
    qs = QueryService(db=db, config_manager=config_manager)

    qs._verify_knowledge_base = AsyncMock(return_value=_make_kb_mock())
    qs._get_rag_config = AsyncMock(return_value={"max_chunks_per_document": 2, "max_chunks": 5})
    qs._maybe_escalate_full_documents = AsyncMock(return_value=None)
    return qs


def _make_embedding_service() -> MagicMock:
    svc = MagicMock()
    svc.embed_query = AsyncMock(return_value=[0.1] * 1024)
    return svc


def _stub_similarity_response() -> dict:
    """Minimum shape the dispatcher reads for the similarity branch."""
    return {
        "results": [],
        "total_results": 0,
        "query": "hello",
        "execution_time": 0.0,
        "threshold": 0.0,
        "embedding_model": "test-model",
    }


class TestUserIdThreadingRagQueryEmbed:
    """SHU-718 regression: user_id must reach embed_query on every retrieval
    path that fires for a RAG-enabled chat turn.
    """

    @pytest.mark.asyncio
    async def test_similarity_search_forwards_user_id(self):
        """similarity_search must forward user_id to embed_query."""
        qs = _make_query_service()
        embed_svc = _make_embedding_service()

        # Empty vector store result → fast-path return after embed_query fires.
        vector_store = MagicMock()
        vector_store.search = AsyncMock(return_value=[])

        req = SimilaritySearchRequest(query="hello world", limit=10, threshold=0.0)

        with (
            patch("shu.core.embedding_service.get_embedding_service", AsyncMock(return_value=embed_svc)),
            patch("shu.core.vector_store.get_vector_store", AsyncMock(return_value=vector_store)),
        ):
            await qs.similarity_search("kb-1", req, user_id="u-1")

        embed_svc.embed_query.assert_awaited_once()
        assert embed_svc.embed_query.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.asyncio
    async def test_similarity_search_defaults_to_none(self):
        """similarity_search called without user_id must pass user_id=None."""
        qs = _make_query_service()
        embed_svc = _make_embedding_service()
        vector_store = MagicMock()
        vector_store.search = AsyncMock(return_value=[])

        req = SimilaritySearchRequest(query="hello world", limit=10, threshold=0.0)

        with (
            patch("shu.core.embedding_service.get_embedding_service", AsyncMock(return_value=embed_svc)),
            patch("shu.core.vector_store.get_vector_store", AsyncMock(return_value=vector_store)),
        ):
            await qs.similarity_search("kb-1", req)

        assert embed_svc.embed_query.call_args.kwargs.get("user_id") is None

    @pytest.mark.asyncio
    async def test_keyword_search_title_match_precompute_forwards_user_id(self):
        """When title weighting is enabled and the query matches a document
        title, keyword_search precomputes the query embedding once for all
        title-match chunk lookups — user_id must reach that precompute call
        and forward into _get_title_match_chunks.
        """
        qs = _make_query_service()
        embed_svc = _make_embedding_service()

        # Simulate a single title match row and empty content chunks.
        title_match_row = MagicMock()
        title_match_row.document_id = "doc-1"
        title_match_row.document_title = "Widget spec"
        title_match_row.title_score = 8.0

        title_result = MagicMock()
        title_result.fetchall = MagicMock(return_value=[title_match_row])

        empty_result = MagicMock()
        empty_result.fetchall = MagicMock(return_value=[])

        # First db.execute() is the title-match SELECT, subsequent are content search.
        qs.db.execute = AsyncMock(side_effect=[title_result, empty_result, empty_result])
        # Short-circuit the inner chunk lookup so we only assert the precompute path.
        qs._get_title_match_chunks = AsyncMock(return_value=[])

        with patch("shu.core.embedding_service.get_embedding_service", AsyncMock(return_value=embed_svc)):
            await qs.keyword_search("kb-1", "widget spec report", limit=10, user_id="u-1")

        embed_svc.embed_query.assert_awaited_once()
        assert embed_svc.embed_query.call_args.kwargs.get("user_id") == "u-1"
        assert qs._get_title_match_chunks.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.asyncio
    async def test_get_title_match_chunks_fallback_forwards_user_id(self):
        """When _get_title_match_chunks is called without a precomputed
        query_embedding, it falls back to embedding the query itself. That
        fallback path must forward user_id — guards a sleeper bug, since no
        production caller triggers this branch today.
        """
        qs = _make_query_service()
        embed_svc = _make_embedding_service()

        # Minimal chunk row carrying an embedding so cosine scoring runs.
        chunk_row = MagicMock()
        chunk_row.id = "c-1"
        chunk_row.document_id = "doc-1"
        chunk_row.knowledge_base_id = "kb-1"
        chunk_row.chunk_index = 0
        chunk_row.content = "widget specification"
        chunk_row.char_count = 20
        chunk_row.word_count = 2
        chunk_row.token_count = 2
        chunk_row.start_char = 0
        chunk_row.end_char = 20
        chunk_row.embedding_model = "test-model"
        chunk_row.embedding_created_at = None
        chunk_row.created_at = None
        chunk_row.document_title = "Widget"
        chunk_row.source_id = "s-1"
        chunk_row.source_url = None
        chunk_row.file_type = "md"
        chunk_row.source_type = None
        chunk_row.chunk_metadata = None
        chunk_row.embedding = [0.1] * 1024
        chunk_row.total_content_chunks = 1

        chunks_result = MagicMock()
        chunks_result.fetchall = MagicMock(return_value=[chunk_row])
        qs.db.execute = AsyncMock(return_value=chunks_result)

        with patch("shu.core.embedding_service.get_embedding_service", AsyncMock(return_value=embed_svc)):
            await qs._get_title_match_chunks(
                document_id="doc-1",
                query="widget spec",
                max_chunks=3,
                knowledge_base_id="kb-1",
                query_embedding=None,
                user_id="u-1",
            )

        embed_svc.embed_query.assert_awaited_once()
        assert embed_svc.embed_query.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.asyncio
    async def test_hybrid_search_forwards_user_id_to_both_subsearches(self):
        """hybrid_search must forward user_id to both similarity_search and
        keyword_search. Guards against a hybrid call producing a NULL-user_id
        row via either sub-search.
        """
        qs = _make_query_service()

        qs.similarity_search = AsyncMock(
            return_value={"results": [], "total_results": 0}
        )
        qs.keyword_search = AsyncMock(
            return_value={"results": [], "total_results": 0}
        )

        await qs.hybrid_search("kb-1", "hello world", limit=10, user_id="u-1")

        assert qs.similarity_search.call_args.kwargs.get("user_id") == "u-1"
        assert qs.keyword_search.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.asyncio
    async def test_multi_surface_search_mixin_forwards_user_id_to_orchestrator(self):
        """_multi_surface_search mixin must forward user_id into the
        MultiSurfaceSearchService orchestrator's .search() call.
        """
        qs = _make_query_service()

        fake_search_service = MagicMock()
        fake_search_service.search = AsyncMock(return_value=([], {}, []))

        with (
            patch("shu.core.embedding_service.get_embedding_service", AsyncMock()),
            patch("shu.core.vector_store.get_vector_store", AsyncMock()),
            patch("shu.core.database.get_async_session_local", MagicMock()),
            patch(
                "shu.services.retrieval.MultiSurfaceSearchService",
                return_value=fake_search_service,
            ),
            patch("shu.services.retrieval.ScoreFusionService", MagicMock()),
        ):
            await qs._multi_surface_search(
                "00000000-0000-0000-0000-000000000001",
                "hello world",
                limit=10,
                user_id="u-1",
            )

        fake_search_service.search.assert_awaited_once()
        assert fake_search_service.search.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.parametrize(
        "search_type,method_name",
        [
            ("keyword", "keyword_search"),
            ("hybrid", "hybrid_search"),
            ("multi_surface", "_multi_surface_search"),
        ],
    )
    @pytest.mark.asyncio
    async def test_query_documents_dispatches_user_id_per_strategy(
        self, search_type, method_name
    ):
        """The query_documents dispatcher must forward user_id to whichever
        strategy method it selects based on ``request.query_type``. Similarity
        is covered separately because its response shape differs.
        """
        qs = _make_query_service()

        # QueryResponse-shape stub covers keyword/hybrid/multi_surface branches.
        stub = {
            "results": [],
            "total_results": 0,
            "query": "hello",
            "query_type": search_type,
            "execution_time": 0.0,
            "similarity_threshold": 0.0,
            "embedding_model": "test-model",
        }
        setattr(qs, method_name, AsyncMock(return_value=stub))

        req = QueryRequest(query="hello world", query_type=search_type, limit=10)
        await qs.query_documents("kb-1", req, user_id="u-1")

        called = getattr(qs, method_name)
        called.assert_awaited_once()
        assert called.call_args.kwargs.get("user_id") == "u-1"

    @pytest.mark.asyncio
    async def test_query_documents_dispatches_user_id_to_similarity(self):
        """Similarity branch has a distinct response shape; covered separately."""
        qs = _make_query_service()
        qs.similarity_search = AsyncMock(return_value=_stub_similarity_response())

        req = QueryRequest(query="hello world", query_type="similarity", limit=10)
        await qs.query_documents("kb-1", req, user_id="u-1")

        qs.similarity_search.assert_awaited_once()
        assert qs.similarity_search.call_args.kwargs.get("user_id") == "u-1"
