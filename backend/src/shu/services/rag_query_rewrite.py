"""Shared helpers for RAG query rewrite side-calls."""

import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.config import ConfigurationManager
from ..models.llm_provider import Message
from ..schemas.query import QueryRequest
from .knowledge_base_service import KnowledgeBaseService
from .query_service import COMPREHENSIVE_STOP_WORDS, QueryService
from .side_call_service import SideCallService

logger = logging.getLogger(__name__)

QueryRequestBuilder = Callable[[str, dict[str, Any], str], QueryRequest]


async def rewrite_query_for_rag(
    db_session: AsyncSession,
    config_manager: ConfigurationManager,
    current_user: User | None,
    query_text: str,
    prior_messages: Sequence[Message] | None = None,
    timeout_ms: int = 1200,
) -> tuple[str, dict[str, Any]]:
    """Invoke the side-call model to produce a retrieval-friendly query.

    Returns the (possibly rewritten) query along with diagnostics capturing
    the original text and whether a rewrite was applied.
    """
    if not query_text:
        diagnostics = {
            "original": "",
            "rewritten": "",
            "used": False,
            "timeout_ms": timeout_ms,
        }
        return query_text, diagnostics

    rewritten_query = query_text
    diagnostics = {
        "original": query_text[:200],
        "rewritten": query_text[:200],
        "used": False,
        "timeout_ms": timeout_ms,
    }

    try:
        side_service = SideCallService(db_session, config_manager)
        side_result = await side_service.propose_rag_query(
            current_user_query=query_text,
            prior_messages=list(prior_messages) if prior_messages else None,
            user_id=str(current_user.id) if current_user and getattr(current_user, "id", None) else "system",
            timeout_ms=timeout_ms,
        )

        if side_result and side_result.content:
            candidate = side_result.content.strip()
            if candidate:
                rewritten_query = candidate

        diagnostics = {
            "original": query_text[:200],
            "rewritten": rewritten_query[:200],
            "used": rewritten_query != query_text,
            "timeout_ms": timeout_ms,
        }

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("RAG query rewrite failed: %s", exc)

    return rewritten_query, diagnostics


async def execute_rag_queries(
    db_session: AsyncSession,
    config_manager: ConfigurationManager,
    query_service: QueryService,
    current_user: User | None,
    query_text: str,
    knowledge_base_ids: Sequence[str],
    request_builder: QueryRequestBuilder,
    prior_messages: Sequence[Message] | None = None,
    timeout_ms: int = 5000,
    apply_rewrite: bool = True,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Rewrite a query, enforce minimum word checks, and execute queries for KBs.

    Returns the rewritten query, diagnostics, and per-KB results containing
    the query response (if executed), associated RAG config, and skip metadata.
    """
    if apply_rewrite:
        rewritten_query, rewrite_diagnostics = await rewrite_query_for_rag(
            db_session=db_session,
            config_manager=config_manager,
            current_user=current_user,
            query_text=query_text,
            prior_messages=prior_messages,
            timeout_ms=timeout_ms,
        )
    else:
        rewritten_query = query_text
        rewrite_diagnostics = None

    words = rewritten_query.split()
    cleaned_words = [
        w.lower().strip('.,!?;:"()[]{}')
        for w in words
        if w.lower().strip('.,!?;:"()[]{}') not in COMPREHENSIVE_STOP_WORDS
    ]

    kb_service = KnowledgeBaseService(db_session, config_manager)
    responses: list[dict[str, Any]] = []

    for kb_id in knowledge_base_ids:
        rag_config: dict[str, Any] | None = None
        try:
            rag_config_response = await kb_service.get_rag_config(kb_id)
            rag_config = rag_config_response.model_dump()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to load RAG config for KB %s: %s", kb_id, exc)
            continue

        min_words = rag_config.get("minimum_query_words", 3)
        if len(words) < min_words or len(cleaned_words) == 0:
            if rewrite_diagnostics is not None:
                skipped = rewrite_diagnostics.setdefault("skipped", {})
                skipped[kb_id] = "insufficient_meaningful_words"
            continue

        try:
            query_request = request_builder(kb_id, rag_config, rewritten_query)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to build query request for KB %s: %s", kb_id, exc)
            continue

        try:
            response = await query_service.query_documents(kb_id, query_request)
            if hasattr(response, "model_dump"):
                response = response.model_dump()
            responses.append(
                {
                    "knowledge_base_id": kb_id,
                    "response": response,
                    "rag_config": rag_config,
                }
            )
        except Exception as exc:  # query execution error
            logger.warning("Failed to query documents for KB %s: %s", kb_id, exc)

    return rewritten_query, rewrite_diagnostics, responses
