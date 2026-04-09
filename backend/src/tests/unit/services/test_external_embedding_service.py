"""Unit tests for ExternalEmbeddingService.

Tests cover:
- EmbeddingService protocol conformance
- embed_texts request payload and response parsing
- embed_query and embed_queries delegation
- dimension and model_name properties
- HTTP error propagation
- Empty input handling
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from shu.core.embedding_protocol import EmbeddingService
from shu.services.external_embedding_service import ExternalEmbeddingService

API_BASE = "https://openrouter.ai/api/v1"
API_KEY = "test-key"
MODEL = "qwen/qwen3-embedding-8b"
DIM = 1024
PROVIDER_ID = "provider-123"
MODEL_ID = "model-456"


def _make_service() -> ExternalEmbeddingService:
    return ExternalEmbeddingService(
        api_base_url=API_BASE,
        api_key=API_KEY,
        model_name=MODEL,
        dimension=DIM,
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
    )


def _mock_embeddings_response(embeddings: list[list[float]]) -> httpx.Response:
    """Build a fake httpx.Response matching the OpenRouter embeddings format."""
    data = [{"embedding": emb, "index": i} for i, emb in enumerate(embeddings)]
    return httpx.Response(
        status_code=200,
        json={"data": data, "model": MODEL, "usage": {"prompt_tokens": 10, "total_tokens": 10}},
        request=httpx.Request("POST", f"{API_BASE}/embeddings"),
    )


@contextmanager
def _patched_httpx(response=None, side_effect=None):
    """Patch httpx.AsyncClient and usage recording to isolate API tests from the DB."""
    with (
        patch("shu.services.external_embedding_service.httpx.AsyncClient") as mock_cls,
        patch.object(ExternalEmbeddingService, "_record_usage", new_callable=AsyncMock),
    ):
        mock_client = AsyncMock()
        if side_effect:
            mock_client.post = AsyncMock(side_effect=side_effect)
        else:
            mock_client.post = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client
        yield mock_client


class TestProtocolConformance:
    def test_isinstance_check(self):
        """A constructed instance should satisfy the EmbeddingService runtime_checkable protocol."""
        svc = _make_service()
        assert isinstance(svc, EmbeddingService)


class TestProperties:
    def test_dimension(self):
        """dimension property should return the value passed at construction."""
        svc = _make_service()
        assert svc.dimension == DIM

    def test_model_name(self):
        """model_name property should return the value passed at construction."""
        svc = _make_service()
        assert svc.model_name == MODEL

    def test_api_base_url_trailing_slash_stripped(self):
        """Constructor should strip trailing slashes from api_base_url to avoid double-slash in endpoint URLs."""
        svc = ExternalEmbeddingService(
            api_base_url="https://example.com/v1/",
            api_key="k",
            model_name="m",
            dimension=128,
            provider_id="p",
            model_id="m",
        )
        assert svc._api_base_url == "https://example.com/v1"

    def test_repr_redacts_api_key(self):
        """repr should not contain the API key."""
        svc = _make_service()
        r = repr(svc)
        assert API_KEY not in r
        assert MODEL in r


class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """Passing an empty list should short-circuit and return [] without making an API call."""
        svc = _make_service()
        result = await svc.embed_texts([])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_text(self):
        """Single text should produce correct OpenRouter payload format and parse the response embedding."""
        embedding = [0.1, 0.2, 0.3]

        with _patched_httpx(_mock_embeddings_response([embedding])) as mock_client:
            svc = _make_service()
            result = await svc.embed_texts(["hello"])

        assert result == [embedding]

        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == f"{API_BASE}/embeddings"
        payload = call_kwargs[1]["json"]
        assert payload["model"] == MODEL
        assert payload["input"] == [{"content": [{"type": "text", "text": "hello"}]}]
        assert payload["encoding_format"] == "float"
        assert "Bearer test-key" in call_kwargs[1]["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_multiple_texts_sorted_by_index(self):
        """When the API returns embeddings out of order, results should be sorted by index to match input order."""
        emb_0 = [1.0, 2.0]
        emb_1 = [3.0, 4.0]
        data = [
            {"embedding": emb_1, "index": 1},
            {"embedding": emb_0, "index": 0},
        ]
        response = httpx.Response(
            status_code=200,
            json={"data": data, "model": MODEL},
            request=httpx.Request("POST", f"{API_BASE}/embeddings"),
        )

        with _patched_httpx(response):
            svc = _make_service()
            result = await svc.embed_texts(["first", "second"])

        assert result == [emb_0, emb_1]


class TestEmbedQuery:
    @pytest.mark.asyncio
    async def test_delegates_to_embed_texts(self):
        """embed_query wraps the text in a single-element list and returns the first (only) result vector."""
        embedding = [0.5, 0.6]

        with _patched_httpx(_mock_embeddings_response([embedding])) as mock_client:
            svc = _make_service()
            result = await svc.embed_query("search query")

        assert result == embedding
        payload = mock_client.post.call_args[1]["json"]
        assert payload["input"] == [{"content": [{"type": "text", "text": "search query"}]}]


class TestEmbedQueries:
    @pytest.mark.asyncio
    async def test_delegates_to_embed_texts(self):
        """embed_queries should call the same API endpoint as embed_texts and return all vectors."""
        emb_0 = [0.1]
        emb_1 = [0.2]

        with _patched_httpx(_mock_embeddings_response([emb_0, emb_1])):
            svc = _make_service()
            result = await svc.embed_queries(["q1", "q2"])

        assert result == [emb_0, emb_1]

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """Empty query list should short-circuit and return [] without making an API call."""
        svc = _make_service()
        result = await svc.embed_queries([])
        assert result == []


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_http_timeout_propagates(self):
        """Timeouts from the provider should propagate as-is so the worker retry mechanism can handle them."""
        with _patched_httpx(side_effect=httpx.TimeoutException("read timeout")):
            svc = _make_service()
            with pytest.raises(httpx.TimeoutException):
                await svc.embed_texts(["test"])

    @pytest.mark.asyncio
    async def test_http_5xx_propagates(self):
        """Server errors (500) should propagate as HTTPStatusError — no silent fallback."""
        error_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", f"{API_BASE}/embeddings"),
        )

        with _patched_httpx(error_response):
            svc = _make_service()
            with pytest.raises(httpx.HTTPStatusError):
                await svc.embed_texts(["test"])

    @pytest.mark.asyncio
    async def test_http_401_propagates(self):
        """Auth failures (401) should propagate as HTTPStatusError — indicates misconfigured API key."""
        error_response = httpx.Response(
            status_code=401,
            text="Unauthorized",
            request=httpx.Request("POST", f"{API_BASE}/embeddings"),
        )

        with _patched_httpx(error_response):
            svc = _make_service()
            with pytest.raises(httpx.HTTPStatusError):
                await svc.embed_texts(["test"])
