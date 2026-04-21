"""Unit tests for ExternalEmbeddingService.

Tests cover:
- EmbeddingService protocol conformance
- embed_texts request payload and response parsing
- embed_query and embed_queries delegation
- dimension and model_name properties
- HTTP error propagation
- Empty input handling
- Inactive provider/model guard (SHU-705)
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from shu.core.embedding_protocol import EmbeddingService
from shu.core.exceptions import InactiveProviderError
from shu.services.external_embedding_service import ExternalEmbeddingService

API_BASE = "https://openrouter.ai/api/v1"
API_KEY = "test-key"
MODEL = "qwen/qwen3-embedding-8b"
DIM = 1024
PROVIDER_ID = "provider-123"
MODEL_ID = "model-456"


def _make_service(query_prefix: str = "", document_prefix: str = "") -> ExternalEmbeddingService:
    return ExternalEmbeddingService(
        api_base_url=API_BASE,
        api_key=API_KEY,
        model_name=MODEL,
        dimension=DIM,
        provider_id=PROVIDER_ID,
        model_id=MODEL_ID,
        query_prefix=query_prefix,
        document_prefix=document_prefix,
    )


@pytest.fixture(autouse=True)
def _stub_active_guard():
    """Bypass the DB-touching provider/model active check in all tests in this module.

    ``_embed_batch`` calls ``ensure_provider_and_model_active`` before the HTTP
    request, which opens a real session. Unit tests must not hit Postgres.
    Tests that want to exercise the guard (``TestInactiveProviderGuard``)
    re-patch the same symbol inside the test body — the inner patch wins.
    """
    with patch(
        "shu.services.external_embedding_service.ensure_provider_and_model_active",
        new_callable=AsyncMock,
    ) as stub:
        yield stub


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


class TestPrefixes:
    @pytest.mark.asyncio
    async def test_embed_texts_applies_document_prefix(self):
        """embed_texts should prepend the document prefix to each text in the API payload."""
        with _patched_httpx(_mock_embeddings_response([[0.1]])) as mock_client:
            svc = _make_service(document_prefix="search_document: ")
            await svc.embed_texts(["hello"])

        payload = mock_client.post.call_args[1]["json"]
        assert payload["input"] == [{"content": [{"type": "text", "text": "search_document: hello"}]}]

    @pytest.mark.asyncio
    async def test_embed_query_applies_query_prefix(self):
        """embed_query should prepend the query prefix to the text in the API payload."""
        with _patched_httpx(_mock_embeddings_response([[0.1]])) as mock_client:
            svc = _make_service(query_prefix="search_query: ")
            await svc.embed_query("hello")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["input"] == [{"content": [{"type": "text", "text": "search_query: hello"}]}]

    @pytest.mark.asyncio
    async def test_embed_queries_applies_query_prefix(self):
        """embed_queries should prepend the query prefix to each text in the API payload."""
        with _patched_httpx(_mock_embeddings_response([[0.1], [0.2]])) as mock_client:
            svc = _make_service(query_prefix="search_query: ")
            await svc.embed_queries(["q1", "q2"])

        payload = mock_client.post.call_args[1]["json"]
        assert payload["input"] == [
            {"content": [{"type": "text", "text": "search_query: q1"}]},
            {"content": [{"type": "text", "text": "search_query: q2"}]},
        ]

    @pytest.mark.asyncio
    async def test_no_prefix_when_empty_string(self):
        """When no prefix is configured, the text should be sent unmodified."""
        with _patched_httpx(_mock_embeddings_response([[0.1]])) as mock_client:
            svc = _make_service()
            await svc.embed_texts(["hello"])

        payload = mock_client.post.call_args[1]["json"]
        assert payload["input"] == [{"content": [{"type": "text", "text": "hello"}]}]

    @pytest.mark.asyncio
    async def test_query_and_document_prefixes_differ(self):
        """embed_texts and embed_query on the same service should use their respective prefixes."""
        svc = _make_service(query_prefix="query: ", document_prefix="passage: ")

        with _patched_httpx(_mock_embeddings_response([[0.1]])) as mock_client:
            await svc.embed_texts(["doc"])
        doc_payload = mock_client.post.call_args[1]["json"]

        with _patched_httpx(_mock_embeddings_response([[0.2]])) as mock_client:
            await svc.embed_query("q")
        query_payload = mock_client.post.call_args[1]["json"]

        assert doc_payload["input"] == [{"content": [{"type": "text", "text": "passage: doc"}]}]
        assert query_payload["input"] == [{"content": [{"type": "text", "text": "query: q"}]}]


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


class TestRecordUsageCostContract:
    """SHU-700: provider-authoritative cost contract — embedding rows must match
    the chat-path shape so downstream aggregators can identify wire-reported
    rows uniformly by `input_cost == 0 AND output_cost == 0 AND total_cost > 0`.
    """

    @pytest.mark.asyncio
    async def test_wire_cost_recorded_on_total_only(self):
        """Provider-reported `usage.cost` lands on `total_cost`; input/output stay 0."""
        from decimal import Decimal

        svc = _make_service()

        with patch("shu.services.usage_recording.record_llm_usage", new_callable=AsyncMock) as mock_record:
            await svc._record_usage(
                {"prompt_tokens": 100, "total_tokens": 100, "cost": "0.00042"},
                user_id="user-7",
            )

        mock_record.assert_awaited_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["total_cost"] == Decimal("0.00042")
        assert kwargs["input_cost"] == Decimal(0)
        assert kwargs["output_cost"] == Decimal(0)
        assert kwargs["user_id"] == "user-7"
        assert kwargs["request_type"] == "embedding"

    @pytest.mark.asyncio
    async def test_missing_wire_cost_records_zero(self):
        """Response without `cost` field (local provider) records all-zero costs, not NULL."""
        from decimal import Decimal

        svc = _make_service()

        with patch("shu.services.usage_recording.record_llm_usage", new_callable=AsyncMock) as mock_record:
            await svc._record_usage({"prompt_tokens": 50, "total_tokens": 50})

        kwargs = mock_record.call_args.kwargs
        assert kwargs["total_cost"] == Decimal(0)
        assert kwargs["input_cost"] == Decimal(0)
        assert kwargs["output_cost"] == Decimal(0)

    @pytest.mark.asyncio
    async def test_empty_usage_skips_record(self):
        """No usage block at all (early return path) must not call record_llm_usage."""
        svc = _make_service()

        with patch("shu.services.usage_recording.record_llm_usage", new_callable=AsyncMock) as mock_record:
            await svc._record_usage(None)

        mock_record.assert_not_called()


class TestInactiveProviderGuard:
    """Each public embed method must delegate the is_active check to
    ``ensure_provider_and_model_active`` BEFORE any HTTP / usage-recording
    side effect, and propagate its raise.

    What counts as inactive and the resulting log/exception content are
    the resolver's concern and live in ``test_external_model_resolver``.
    """

    @pytest.mark.parametrize(
        "method_name,inputs",
        [
            ("embed_texts", [["doc"]]),
            ("embed_query", ["q"]),
            ("embed_queries", [["q1", "q2"]]),
        ],
    )
    @pytest.mark.asyncio
    async def test_calls_resolver_with_embedding_call_type_and_propagates_raise(
        self, method_name: str, inputs: list
    ):
        with (
            patch(
                "shu.services.external_embedding_service.ensure_provider_and_model_active",
                new_callable=AsyncMock,
                side_effect=InactiveProviderError("provider inactive: " + PROVIDER_ID),
            ) as mock_guard,
            patch("shu.services.external_embedding_service.httpx.AsyncClient") as mock_http_cls,
            patch.object(ExternalEmbeddingService, "_record_usage", new_callable=AsyncMock) as mock_record,
        ):
            svc = _make_service()
            with pytest.raises(InactiveProviderError):
                await getattr(svc, method_name)(*inputs)

        mock_guard.assert_awaited_with(PROVIDER_ID, MODEL_ID, call_type="embedding")
        mock_http_cls.assert_not_called()
        mock_record.assert_not_called()
