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

from ..core.logging import get_logger

logger = get_logger(__name__)


class ExternalEmbeddingService:
    """Embedding service backed by an external API provider.

    Conforms to the EmbeddingService protocol. Calls the provider's
    /embeddings endpoint following the OpenAI format.
    """

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
        dimension: int,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._dimension = dimension

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
