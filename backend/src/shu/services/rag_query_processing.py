"""Shared helpers for preparing RAG retrieval queries."""

import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.config import ConfigurationManager
from ..models.llm_provider import Message
from ..schemas.query import QueryRequest, RagRewriteMode
from .knowledge_base_service import KnowledgeBaseService
from .query_service import COMPREHENSIVE_STOP_WORDS, QueryService
from .side_call_service import SideCallService

logger = logging.getLogger(__name__)

QueryRequestBuilder = Callable[[str, dict[str, Any], str], QueryRequest]


async def process_query_for_rag(
    db_session: AsyncSession,
    config_manager: ConfigurationManager,
    current_user: User | None,
    query_text: str,
    prior_messages: Sequence[Message] | None = None,
    timeout_ms: int = 1200,
    mode: RagRewriteMode = RagRewriteMode.RAW_QUERY,
) -> tuple[str, dict[str, Any]]:
    """Return the retrieval query and diagnostics for the chosen rewrite mode."""
    trimmed_query = (query_text or "").strip()
    diagnostics: dict[str, Any] = {
        "mode": mode.value,
        "original": trimmed_query[:200],
        "rewritten": trimmed_query[:200],
        "used": False,
        "timeout_ms": timeout_ms,
    }

    if mode == RagRewriteMode.NO_RAG:
        diagnostics["reason"] = "rag_disabled"
        return trimmed_query, diagnostics

    if not trimmed_query:
        diagnostics["reason"] = "empty_query"
        return trimmed_query, diagnostics

    if mode == RagRewriteMode.RAW_QUERY:
        diagnostics["rewritten"] = trimmed_query[:200]
        return trimmed_query, diagnostics

    processed_query = trimmed_query

    try:
        side_service = SideCallService(db_session, config_manager)
        side_result = None

        if mode == RagRewriteMode.REWRITE_ENHANCED:
            side_result = await side_service.propose_rag_query(
                current_user_query=trimmed_query,
                prior_messages=list(prior_messages) if prior_messages else None,
                user_id=str(current_user.id) if current_user and getattr(current_user, "id", None) else "system",
                timeout_ms=timeout_ms,
            )
        elif mode == RagRewriteMode.DISTILL_CONTEXT:
            side_result = await side_service.distill_rag_query(
                current_user_query=trimmed_query,
                user_id=str(current_user.id) if current_user and getattr(current_user, "id", None) else "system",
                timeout_ms=timeout_ms,
            )

        if side_result and side_result.content is not None:
            candidate = side_result.content.strip()
            if candidate:
                processed_query = candidate
                diagnostics["used"] = processed_query != trimmed_query

        if side_result and not side_result.success and side_result.error_message:
            diagnostics["error"] = side_result.error_message[:200]

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("RAG query processing failed: %s", exc)
        diagnostics["error"] = str(exc)

    diagnostics["rewritten"] = processed_query[:200]
    return processed_query, diagnostics


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
    rag_rewrite_mode: RagRewriteMode = RagRewriteMode.RAW_QUERY,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Process a query, enforce minimum word checks, and execute queries for KBs."""
    rewritten_query, rewrite_diagnostics = await process_query_for_rag(
        db_session=db_session,
        config_manager=config_manager,
        current_user=current_user,
        query_text=query_text,
        prior_messages=prior_messages,
        timeout_ms=timeout_ms,
        mode=rag_rewrite_mode,
    )

    if rag_rewrite_mode == RagRewriteMode.NO_RAG:
        return rewritten_query, rewrite_diagnostics, []

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
                response = response.model_dump(mode="json")
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
