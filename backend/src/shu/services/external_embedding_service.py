"""External embedding service that calls API-hosted embedding models.

Implements the EmbeddingService protocol by calling a provider's embeddings
API (initially OpenRouter, which follows the OpenAI embeddings format).
Uses its own httpx client rather than routing through UnifiedLLMClient,
because the request/response format differs from chat completions.

TODO: Abstract provider-specific formatting when we support more than
OpenRouter for embedding models.
"""

from decimal import Decimal
from typing import Any

import httpx

from ..core.logging import get_logger

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
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._dimension = dimension
        self._provider_id = provider_id
        self._model_id = model_id

    def __repr__(self) -> str:
        """Redact API key from repr to prevent leaking credentials in logs/tracebacks."""
        return f"ExternalEmbeddingService(model={self._model_name!r}, dim={self._dimension})"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response_data = await self._call_embeddings_api(texts)
        entries = sorted(response_data["data"], key=lambda e: e["index"])

        await self._record_usage(response_data.get("usage"))

        return [entry["embedding"] for entry in entries]

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed_texts([text])
        if not results:
            raise ValueError(
                f"embed_texts returned no results for query text "
                f"(model={self._model_name}, input_length={len(text)})"
            )
        return results[0]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return await self.embed_texts(texts)

    async def _call_embeddings_api(self, texts: list[str]) -> dict[str, Any]:
        # See docs here: https://openrouter.ai/docs/api/reference/embeddings
        payload = {
            "model": self._model_name,
            "input": [{"content": [{"type": "text", "text": t}]} for t in texts],
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

    async def _record_usage(self, usage: dict[str, Any] | None) -> None:
        """Record embedding API usage in llm_usage. Best-effort — failures are logged, not raised."""
        if not usage:
            return

        try:
            from ..core.database import get_async_session_local
            from ..models.llm_provider import LLMUsage

            prompt_tokens = usage.get("prompt_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            cost = usage.get("cost", 0)

            record = LLMUsage(
                provider_id=self._provider_id,
                model_id=self._model_id,
                request_type="embedding",
                input_tokens=prompt_tokens,
                output_tokens=0,
                total_tokens=total_tokens,
                input_cost=Decimal(str(cost)),
                output_cost=Decimal("0"),
                total_cost=Decimal(str(cost)),
                success=True,
            )

            session_factory = get_async_session_local()
            async with session_factory() as session:
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to record embedding usage: %s", e)
