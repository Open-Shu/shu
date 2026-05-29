import json
from decimal import Decimal

import httpx
import pytest

from shu.services.providers.internal_tools.web_search import WebSearchTool


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Captures the request shape and returns a scripted response."""

    def __init__(self, *, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.captured: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, *, params=None, headers=None):
        self.captured = {"url": url, "params": params, "headers": headers}
        if self.raises is not None:
            raise self.raises
        return self.response


def test_tool_name_is_bare_no_prefix():
    # The router owns the int: prefix — the tool's own name is bare.
    assert WebSearchTool.name == "web_search"
    assert ":" not in WebSearchTool.name
    assert "__" not in WebSearchTool.name


def test_parameter_schema_shape():
    tool = WebSearchTool(api_key="any")
    schema = tool.parameter_schema()
    assert schema["type"] == "object"
    assert set(schema["properties"].keys()) == {"query"}
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_execute_returns_unavailable_when_api_key_missing():
    content, cost = await WebSearchTool(api_key=None).execute({"query": "anything"})
    assert content == "web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set"
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_returns_unavailable_when_api_key_empty():
    content, cost = await WebSearchTool(api_key="").execute({"query": "anything"})
    assert content == "web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set"
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_missing_query_argument():
    content, cost = await WebSearchTool(api_key="real-key").execute({})
    assert content == "web search failed: missing or empty `query` argument"
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_empty_query_argument():
    content, cost = await WebSearchTool(api_key="real-key").execute({"query": "   "})
    assert content == "web search failed: missing or empty `query` argument"
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_builds_correct_request_shape(monkeypatch):
    captured: dict = {}

    def make_client(*args, **kwargs):
        client = _FakeAsyncClient(
            response=_FakeResponse({"web": {"results": []}}),
        )
        captured["client"] = client
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    await WebSearchTool(api_key="my-key").execute({"query": "python tutorials"})
    req = captured["client"].captured
    assert req["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert req["params"]["q"] == "python tutorials"
    assert req["headers"]["X-Subscription-Token"] == "my-key"
    assert req["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_execute_returns_configured_cost_on_success(monkeypatch):
    # SHU-816: cost is the configured per-query rate, returned alongside
    # the content on every successful Brave response.
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse({"web": {"results": []}}))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    tool = WebSearchTool(api_key="my-key", cost_per_query=Decimal("0.005"))
    _, cost = await tool.execute({"query": "anything"})
    assert cost == Decimal("0.005")


@pytest.mark.asyncio
async def test_execute_defaults_cost_to_zero_when_unconfigured(monkeypatch):
    # Default constructor: no cost_per_query → success bills 0.
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse({"web": {"results": []}}))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    _, cost = await WebSearchTool(api_key="my-key").execute({"query": "anything"})
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_formats_web_section(monkeypatch):
    payload = {
        "web": {
            "results": [
                {
                    "title": "Result 1",
                    "url": "https://example.com/1",
                    "description": "First snippet",
                },
                {
                    "title": "Result 2",
                    "url": "https://example.com/2",
                    "description": "Second snippet",
                },
            ]
        }
    }

    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse(payload))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    parsed = json.loads(content)
    assert parsed["web"] == [
        {"title": "Result 1", "url": "https://example.com/1", "snippet": "First snippet"},
        {"title": "Result 2", "url": "https://example.com/2", "snippet": "Second snippet"},
    ]


@pytest.mark.asyncio
async def test_execute_formats_discussions_and_faq_sections(monkeypatch):
    payload = {
        "web": {"results": []},
        "discussions": {
            "results": [
                {
                    "title": "Reddit thread",
                    "url": "https://reddit.com/r/x/comments/y",
                    "data": {
                        "question": "Why?",
                        "top_comment": "Because.",
                    },
                }
            ]
        },
        "faq": {
            "results": [
                {
                    "title": "FAQ page",
                    "url": "https://example.com/faq",
                    "question": "What is it?",
                    "answer": "It is a thing.",
                }
            ]
        },
    }

    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse(payload))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    parsed = json.loads(content)
    # web section was empty and is therefore omitted
    assert "web" not in parsed
    assert parsed["discussions"] == [
        {
            "title": "Reddit thread",
            "url": "https://reddit.com/r/x/comments/y",
            "question": "Why?",
            "top_comment": "Because.",
        }
    ]
    assert parsed["faq"] == [
        {
            "title": "FAQ page",
            "url": "https://example.com/faq",
            "question": "What is it?",
            "answer": "It is a thing.",
        }
    ]


@pytest.mark.asyncio
async def test_execute_returns_empty_object_when_all_sections_missing(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse({}))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert json.loads(content) == {}


@pytest.mark.asyncio
async def test_execute_handles_http_status_error(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(response=_FakeResponse({}, status_code=429))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, cost = await WebSearchTool(
        api_key="key", cost_per_query=Decimal("0.005")
    ).execute({"query": "anything"})
    # Even with a configured cost, failures bill zero — we record the
    # attempt as a failure row but don't charge the user for it.
    assert content == "web search failed: HTTP 429 from Brave"
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_handles_timeout(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(raises=httpx.TimeoutException("slow"))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, cost = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert "timed out" in content
    assert content.startswith("web search failed:")
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_handles_generic_http_error(monkeypatch):
    def make_client(*args, **kwargs):
        return _FakeAsyncClient(raises=httpx.ConnectError("dns blew up"))

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    content, cost = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert content.startswith("web search failed:")
    assert "dns blew up" in content
    assert cost == Decimal("0")
