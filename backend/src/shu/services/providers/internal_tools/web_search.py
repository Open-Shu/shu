"""Brave Search-backed `web_search` internal tool (SHU-816)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, ClassVar

import httpx

from shu.core.logging import get_logger

from .base import InternalTool

logger = get_logger(__name__)

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_RESULT_LIMIT = 10
_REQUEST_TIMEOUT_SECONDS = 10.0
_ZERO = Decimal("0")


def _query_fingerprint(query: str) -> str:
    # Search queries routinely contain user PII (names, medical terms,
    # internal IDs) routed through the model. Logging a short hash +
    # length keeps the audit trail without leaking the content into
    # long-retention prod logs.
    return hashlib.sha256(query.encode("utf-8", errors="replace")).hexdigest()[:12]


class WebSearchTool(InternalTool):
    """Web search via Brave Search API.

    The router exposes this tool to the model as ``int:web_search``.
    Returns a JSON object with up to three sections — ``web``,
    ``discussions``, and ``faq`` — giving the model multiple angles to
    ground its answer on.
    """

    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web for current information. Returns JSON with up to "
        "`web`, `discussions`, and `faq` arrays of result records (empty "
        "sections omitted). Supports search operators: `site:`, quotes, "
        "`-term`, `OR`."
    )

    def __init__(self, api_key: str | None, cost_per_query: Decimal = _ZERO) -> None:
        self._api_key = api_key
        # SHU-816: per-call rate attributed to llm_usage.total_cost on
        # success. Failure paths bill zero regardless of this value.
        self._cost_per_query = cost_per_query

    def parameter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    async def execute(self, args: dict[str, Any]) -> tuple[str, bool, Decimal]:
        if not self._api_key:
            return ("web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set", True, _ZERO)

        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ("web search failed: missing or empty `query` argument", True, _ZERO)

        # TODO(SHU-816 task 16 follow-up): Cache successful results via
        # CacheBackend. Proposed key shape:
        # `tool:web_search:<sha256(normalized_query)>`, global namespace
        # (results are public, no PII), only-on-success. TTL /
        # cache-backend injection path deferred until there's a separate
        # ticket for caching.

        query_id = _query_fingerprint(query)
        logger.info("Brave web search: query_len=%d query_hash=%s", len(query), query_id)

        try:
            # Per-call client mirrors the existing per-call pattern in
            # responses_adapter.py; cleanup is automatic via async with.
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    _BRAVE_SEARCH_URL,
                    params={"q": query, "count": _RESULT_LIMIT},
                    headers={
                        "X-Subscription-Token": self._api_key,
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Brave web search returned HTTP %s (query_hash=%s)",
                exc.response.status_code,
                query_id,
            )
            return (f"web search failed: HTTP {exc.response.status_code} from Brave", True, _ZERO)
        except httpx.TimeoutException:
            logger.warning(
                "Brave web search timed out after %ss (query_hash=%s)",
                _REQUEST_TIMEOUT_SECONDS,
                query_id,
            )
            return (
                f"web search failed: Brave request timed out after {_REQUEST_TIMEOUT_SECONDS}s",
                True,
                _ZERO,
            )
        except httpx.HTTPError as exc:
            logger.warning("Brave web search HTTP error (query_hash=%s): %s", query_id, exc)
            return (f"web search failed: {exc}", True, _ZERO)
        except (ValueError, KeyError) as exc:
            # Unexpected: Brave returned a 2xx but the body didn't shape up.
            # Log with traceback so we notice if their schema drifts.
            logger.warning(
                "Brave web search returned a malformed response (query_hash=%s): %s",
                query_id,
                exc,
                exc_info=True,
            )
            return (f"web search failed: malformed Brave response ({exc})", True, _ZERO)

        # Brave returns up to three result sections in a single response;
        # exposing all of them gives the model more angles for follow-up
        # research without an extra round-trip. We keep each record minimal
        # — only the fields that carry signal — and omit sections that
        # came back empty so the model isn't reasoning over noise.
        output: dict[str, list[dict[str, str]]] = {}

        web_results = (payload.get("web") or {}).get("results") or []
        if web_results:
            output["web"] = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", ""),
                }
                for item in web_results[:_RESULT_LIMIT]
            ]

        discussion_results = (payload.get("discussions") or {}).get("results") or []
        if discussion_results:
            output["discussions"] = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "question": (item.get("data") or {}).get("question", ""),
                    "top_comment": (item.get("data") or {}).get("top_comment", ""),
                }
                for item in discussion_results[:_RESULT_LIMIT]
            ]

        faq_results = (payload.get("faq") or {}).get("results") or []
        if faq_results:
            output["faq"] = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "question": item.get("question", ""),
                    "answer": item.get("answer", ""),
                }
                for item in faq_results[:_RESULT_LIMIT]
            ]

        return (json.dumps(output), False, self._cost_per_query)
