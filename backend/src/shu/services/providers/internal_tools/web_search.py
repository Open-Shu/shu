"""Brave Search-backed `web_search` internal tool (SHU-816)."""

from __future__ import annotations

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


class WebSearchTool(InternalTool):
    """Web search via Brave Search API.

    The router exposes this tool to the model as ``int:web_search``.
    Returns a JSON object with up to three sections — ``web``,
    ``discussions``, and ``faq`` — giving the model multiple angles to
    ground its answer on.
    """

    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web for current information. The query supports standard "
        "search operators (e.g., `site:example.com` to restrict to a domain, "
        '`"exact phrase"` for exact-match, `-excluded` to exclude a term, '
        "`OR` between alternatives). Returns a JSON object with up to three "
        "sections: `web` (list of {title, url, snippet}), `discussions` "
        "(list of {title, url, question, top_comment}), and `faq` (list of "
        "{title, url, question, answer}). Empty sections are omitted."
    )

    def __init__(self, api_key: str | None, cost_per_query: Decimal = _ZERO) -> None:
        self._api_key = api_key
        # SHU-816: per-call rate to attribute to llm_usage.total_cost on
        # success. Failures + unconfigured-key path always bill zero —
        # we record the call as a failure row but never charge the user
        # for an attempt that didn't return data.
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

    async def execute(self, args: dict[str, Any]) -> tuple[str, Decimal]:
        if not self._api_key:
            return ("web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set", _ZERO)

        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ("web search failed: missing or empty `query` argument", _ZERO)

        # TODO(SHU-816 task 16 follow-up): Cache successful results via
        # CacheBackend. Proposed key shape:
        # `tool:web_search:<sha256(normalized_query)>`, global namespace
        # (results are public, no PII), only-on-success. TTL /
        # cache-backend injection path deferred until there's a separate
        # ticket for caching.

        logger.info("Performing Brave web search for: %s", query)

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
                "Brave web search returned HTTP %s for query %r",
                exc.response.status_code,
                query,
            )
            return (f"web search failed: HTTP {exc.response.status_code} from Brave", _ZERO)
        except httpx.TimeoutException:
            logger.warning(
                "Brave web search timed out after %ss for query %r",
                _REQUEST_TIMEOUT_SECONDS,
                query,
            )
            return (
                f"web search failed: Brave request timed out after {_REQUEST_TIMEOUT_SECONDS}s",
                _ZERO,
            )
        except httpx.HTTPError as exc:
            logger.warning("Brave web search HTTP error for query %r: %s", query, exc)
            return (f"web search failed: {exc}", _ZERO)
        except (ValueError, KeyError) as exc:
            # Unexpected: Brave returned a 2xx but the body didn't shape up.
            # Log with traceback so we notice if their schema drifts.
            logger.warning(
                "Brave web search returned a malformed response for query %r: %s",
                query,
                exc,
                exc_info=True,
            )
            return (f"web search failed: malformed Brave response ({exc})", _ZERO)

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

        return (json.dumps(output), self._cost_per_query)
