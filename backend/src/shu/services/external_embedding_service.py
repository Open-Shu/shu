"""External embedding service that calls API-hosted embedding models.

Implements the EmbeddingService protocol by calling a provider's embeddings
API (initially OpenRouter, which follows the OpenAI embeddings format).
Uses its own httpx client rather than routing through UnifiedLLMClient,
because the request/response format differs from chat completions.

TODO: Abstract provider-specific formatting when we support more than
OpenRouter for embedding models.
"""

from typing import Any

import httpx

from ..core.exceptions import EmbeddingProviderError
from ..core.external_model_resolver import ensure_provider_and_model_active
from ..core.logging import get_logger
from ..core.safe_decimal import safe_decimal
from ..services.usage_recording import get_usage_recorder

logger = get_logger(__name__)


class ExternalEmbeddingService:
    """Embedding service backed by an external API provider.

    Conforms to the EmbeddingService protocol. Calls the provider's
    /embeddings endpoint following the OpenAI format. Records token
    usage in llm_usage after each API call.
    """

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        dimension: int,
        provider_id: str,
        model_id: str,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._dimension = dimension
        self._provider_id = provider_id
        self._model_id = model_id
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix

    def __repr__(self) -> str:
        """Redact API key from repr to prevent leaking credentials in logs/tracebacks."""
        return f"ExternalEmbeddingService(model={self._model_name!r}, dim={self._dimension})"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_texts(self, texts: list[str], *, user_id: str | None = None) -> list[list[float]]:
        return await self._embed_batch(texts, prefix=self._document_prefix, user_id=user_id)

    async def embed_query(self, text: str, *, user_id: str | None = None) -> list[float]:
        results = await self._embed_batch([text], prefix=self._query_prefix, user_id=user_id)
        if not results:
            raise EmbeddingProviderError(
                model_name=self._model_name,
                reason=f"embed_texts returned no results for query text (input_length={len(text)})",
            )
        return results[0]

    async def embed_queries(self, texts: list[str], *, user_id: str | None = None) -> list[list[float]]:
        return await self._embed_batch(texts, prefix=self._query_prefix, user_id=user_id)

    async def _embed_batch(
        self, texts: list[str], prefix: str = "", *, user_id: str | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        await ensure_provider_and_model_active(self._provider_id, self._model_id, call_type="embedding")
        response_data = await self._call_embeddings_api(texts, prefix=prefix)
        entries = response_data.get("data") or []
        if len(entries) != len(texts):
            raise EmbeddingProviderError(
                model_name=self._model_name,
                reason=f"Embedding API returned {len(entries)} results for {len(texts)} inputs",
            )
        entries = sorted(entries, key=lambda e: e["index"])
        await self._record_usage(response_data.get("usage"), user_id=user_id)
        return [entry["embedding"] for entry in entries]

    async def _call_embeddings_api(self, texts: list[str], prefix: str = "") -> dict[str, Any]:
        payload = {
            "model": self._model_name,
            "input": [{"content": [{"type": "text", "text": prefix + t}]} for t in texts],
            "encoding_format": "float",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._api_base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            )
            response.raise_for_status()

        return response.json()

    async def _record_usage(self, usage: dict[str, Any] | None, *, user_id: str | None = None) -> None:
        """Record embedding API usage in llm_usage. Best-effort — failures are logged, not raised.

        Cost-column contract (SHU-700 + SHU-715): wire cost on ``total_cost``
        when the provider returns it, otherwise ``Decimal(0)`` which triggers
        ``UsageRecorder``'s DB-rate fallback. Previously this path silently
        recorded $0 when the provider omitted ``usage.cost``; that was a
        latent leak — harmless for OpenRouter (always returns cost) but real
        the moment a direct-API embedding model with DB-priced rates is added.
        """
        if not usage:
            return

        # Always log the raw usage payload so costs can be reconstructed
        # from logs if DB recording ever fails.
        logger.info(
            "Embedding API usage",
            extra={
                "model": self._model_name,
                "raw_usage": usage,
            },
        )

        prompt_tokens = usage.get("prompt_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        await get_usage_recorder().record(
            provider_id=self._provider_id,
            model_id=self._model_id,
            request_type="embedding",
            user_id=user_id,
            input_tokens=prompt_tokens,
            total_tokens=total_tokens,
            total_cost=safe_decimal(usage.get("cost")),
        )
