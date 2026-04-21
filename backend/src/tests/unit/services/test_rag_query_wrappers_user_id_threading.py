"""SHU-718 regression tests: the ``execute_rag_queries`` helpers in both
``rag_query_processing.py`` and ``rag_query_rewrite.py`` must forward
``user_id`` down into ``QueryService.query_documents``.

Two copies of this helper exist today (the ticket discussion flagged that
both live in the codebase and both reach ``query_documents``). Each needs
its own regression test so neither silently regresses user attribution on
retrieval-side embedding ``llm_usage`` rows.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.schemas.query import QueryRequest, RagRewriteMode
from shu.services import rag_query_processing, rag_query_rewrite


def _make_query_request() -> QueryRequest:
    return QueryRequest(query="hello world how are you", query_type="hybrid", limit=10)


def _build_request(_kb_id, _rag_config, query_text):
    return QueryRequest(query=query_text, query_type="hybrid", limit=10)


def _rag_config_response() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump = MagicMock(return_value={"minimum_query_words": 1, "search_type": "hybrid"})
    return cfg


class TestExecuteRagQueriesUserIdThreading:
    """Both ``execute_rag_queries`` helpers must forward user_id into
    ``QueryService.query_documents`` so the embedding llm_usage row
    attributes to the originating user (SHU-718).
    """

    @pytest.mark.asyncio
    async def test_rag_query_processing_forwards_user_id_to_query_documents(self):
        db_session = AsyncMock()
        config_manager = MagicMock()
        current_user = MagicMock()
        current_user.id = "user-42"

        query_service = MagicMock()
        query_service.query_documents = AsyncMock(return_value={"results": []})

        kb_service = MagicMock()
        kb_service.get_rag_config = AsyncMock(return_value=_rag_config_response())

        with patch.object(
            rag_query_processing, "KnowledgeBaseService", return_value=kb_service
        ):
            await rag_query_processing.execute_rag_queries(
                db_session=db_session,
                config_manager=config_manager,
                query_service=query_service,
                current_user=current_user,
                query_text="hello world how are you",
                knowledge_base_ids=["kb-1"],
                request_builder=_build_request,
                rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
                user_id="user-42",
            )

        query_service.query_documents.assert_awaited_once()
        assert query_service.query_documents.call_args.kwargs.get("user_id") == "user-42"

    @pytest.mark.asyncio
    async def test_rag_query_processing_defaults_user_id_to_none(self):
        """Legacy callers that don't pass user_id must still work and
        propagate ``user_id=None`` — guards against the signature change
        accidentally requiring the kwarg.
        """
        db_session = AsyncMock()
        config_manager = MagicMock()
        current_user = MagicMock()
        current_user.id = "user-42"

        query_service = MagicMock()
        query_service.query_documents = AsyncMock(return_value={"results": []})

        kb_service = MagicMock()
        kb_service.get_rag_config = AsyncMock(return_value=_rag_config_response())

        with patch.object(
            rag_query_processing, "KnowledgeBaseService", return_value=kb_service
        ):
            await rag_query_processing.execute_rag_queries(
                db_session=db_session,
                config_manager=config_manager,
                query_service=query_service,
                current_user=current_user,
                query_text="hello world how are you",
                knowledge_base_ids=["kb-1"],
                request_builder=_build_request,
                rag_rewrite_mode=RagRewriteMode.RAW_QUERY,
            )

        assert query_service.query_documents.call_args.kwargs.get("user_id") is None

    @pytest.mark.asyncio
    async def test_rag_query_rewrite_forwards_user_id_to_query_documents(self):
        """Second copy of execute_rag_queries (rag_query_rewrite.py) must
        also thread user_id — this is the one the ticket's "intermediate
        rag_query_* wrapper" clause covers.
        """
        db_session = AsyncMock()
        config_manager = MagicMock()
        current_user = MagicMock()
        current_user.id = "user-42"

        query_service = MagicMock()
        query_service.query_documents = AsyncMock(return_value={"results": []})

        kb_service = MagicMock()
        kb_service.get_rag_config = AsyncMock(return_value=_rag_config_response())

        with patch.object(
            rag_query_rewrite, "KnowledgeBaseService", return_value=kb_service
        ):
            await rag_query_rewrite.execute_rag_queries(
                db_session=db_session,
                config_manager=config_manager,
                query_service=query_service,
                current_user=current_user,
                query_text="hello world how are you",
                knowledge_base_ids=["kb-1"],
                request_builder=_build_request,
                apply_rewrite=False,  # skip the side-call rewrite path
                user_id="user-42",
            )

        query_service.query_documents.assert_awaited_once()
        assert query_service.query_documents.call_args.kwargs.get("user_id") == "user-42"
