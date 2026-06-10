import hashlib
import json
import logging
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


def _stub_httpx(monkeypatch, *, response=None, raises=None) -> _FakeAsyncClient:
    """Replace httpx.AsyncClient with a stub. Returns the captured client."""
    client = _FakeAsyncClient(response=response, raises=raises)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: client)
    return client


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
    content, is_error, cost = await WebSearchTool(api_key=None).execute({"query": "anything"})
    assert content == "web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_returns_unavailable_when_api_key_empty():
    content, is_error, cost = await WebSearchTool(api_key="").execute({"query": "anything"})
    assert content == "web search is unavailable: SHU_BRAVE_SEARCH_API_KEY is not set"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_missing_query_argument():
    content, is_error, cost = await WebSearchTool(api_key="real-key").execute({})
    assert content == "web search failed: missing or empty `query` argument"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_empty_query_argument():
    content, is_error, cost = await WebSearchTool(api_key="real-key").execute({"query": "   "})
    assert content == "web search failed: missing or empty `query` argument"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_builds_correct_request_shape(monkeypatch):
    client = _stub_httpx(monkeypatch, response=_FakeResponse({"web": {"results": []}}))

    await WebSearchTool(api_key="my-key").execute({"query": "python tutorials"})
    req = client.captured
    assert req["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert req["params"]["q"] == "python tutorials"
    assert req["headers"]["X-Subscription-Token"] == "my-key"
    assert req["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_execute_returns_configured_cost_on_success(monkeypatch):
    # SHU-816: cost is the configured per-query rate, returned alongside
    # the content on every successful Brave response.
    _stub_httpx(monkeypatch, response=_FakeResponse({"web": {"results": []}}))

    tool = WebSearchTool(api_key="my-key", cost_per_query=Decimal("0.005"))
    _, is_error, cost = await tool.execute({"query": "anything"})
    assert is_error is False
    assert cost == Decimal("0.005")


@pytest.mark.asyncio
async def test_execute_zero_cost_success_is_still_success(monkeypatch):
    # Regression for SHU-816 H2: zero cost must not be inferred as a
    # failure. A tool with no configured rate (default constructor) that
    # returns successfully bills 0 but records is_error=False, and the
    # content is real JSON — not an error string in the wrong column.
    _stub_httpx(monkeypatch, response=_FakeResponse({"web": {"results": []}}))

    content, is_error, cost = await WebSearchTool(api_key="my-key").execute({"query": "anything"})
    assert is_error is False
    assert cost == Decimal("0")
    assert json.loads(content) == {}


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
    _stub_httpx(monkeypatch, response=_FakeResponse(payload))

    content, is_error, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    parsed = json.loads(content)
    assert is_error is False
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
    _stub_httpx(monkeypatch, response=_FakeResponse(payload))

    content, _, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
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
    _stub_httpx(monkeypatch, response=_FakeResponse({}))

    content, is_error, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert is_error is False
    assert json.loads(content) == {}


@pytest.mark.asyncio
async def test_execute_truncates_to_result_limit(monkeypatch):
    # _RESULT_LIMIT = 10; verify we don't pass the whole 30-item Brave
    # response through to the model.
    payload = {
        "web": {
            "results": [
                {"title": f"r{i}", "url": f"https://example.com/{i}", "description": f"d{i}"}
                for i in range(30)
            ]
        }
    }
    _stub_httpx(monkeypatch, response=_FakeResponse(payload))

    content, _, _ = await WebSearchTool(api_key="key").execute({"query": "anything"})
    parsed = json.loads(content)
    assert len(parsed["web"]) == 10


@pytest.mark.asyncio
async def test_execute_handles_http_status_error(monkeypatch):
    _stub_httpx(monkeypatch, response=_FakeResponse({}, status_code=429))

    content, is_error, cost = await WebSearchTool(
        api_key="key", cost_per_query=Decimal("0.005")
    ).execute({"query": "anything"})
    # Even with a configured cost, failures bill zero — we record the
    # attempt as a failure row but don't charge the user for it.
    assert content == "web search failed: HTTP 429 from Brave"
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_handles_timeout(monkeypatch):
    _stub_httpx(monkeypatch, raises=httpx.TimeoutException("slow"))

    content, is_error, cost = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert "timed out" in content
    assert content.startswith("web search failed:")
    assert is_error is True
    assert cost == Decimal("0")


@pytest.mark.asyncio
async def test_execute_handles_generic_http_error(monkeypatch):
    _stub_httpx(monkeypatch, raises=httpx.ConnectError("dns blew up"))

    content, is_error, cost = await WebSearchTool(api_key="key").execute({"query": "anything"})
    assert content.startswith("web search failed:")
    assert "dns blew up" in content
    assert is_error is True
    assert cost == Decimal("0")


# ----------------------------------------------------------------------
# H3 regression: queries can contain user PII and must NOT appear in logs.
# ----------------------------------------------------------------------


_PII_QUERY = "patient John Doe SSN 123-45-6789 hypertension"


def _assert_query_not_in_logs(caplog, query: str) -> None:
    # Check both the rendered message and the raw args — we want to know
    # the query never touches the logging system, not just that it's
    # missing from the final formatted string.
    for record in caplog.records:
        assert query not in record.getMessage(), f"raw query leaked in: {record.getMessage()!r}"
        if record.args:
            for arg in (record.args if isinstance(record.args, tuple) else (record.args,)):
                assert query != arg, f"raw query leaked in record args: {record.args!r}"


@pytest.mark.asyncio
async def test_success_log_does_not_leak_raw_query(monkeypatch, caplog):
    _stub_httpx(monkeypatch, response=_FakeResponse({"web": {"results": []}}))

    with caplog.at_level(logging.DEBUG):
        await WebSearchTool(api_key="key").execute({"query": _PII_QUERY})

    _assert_query_not_in_logs(caplog, _PII_QUERY)
    # Sanity: the fingerprint hash IS logged so we still have an audit
    # trail to correlate with billing rows.
    expected_hash = hashlib.sha256(_PII_QUERY.encode("utf-8")).hexdigest()[:12]
    assert any(expected_hash in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_http_status_error_log_does_not_leak_raw_query(monkeypatch, caplog):
    _stub_httpx(monkeypatch, response=_FakeResponse({}, status_code=429))

    with caplog.at_level(logging.DEBUG):
        await WebSearchTool(api_key="key").execute({"query": _PII_QUERY})

    _assert_query_not_in_logs(caplog, _PII_QUERY)


@pytest.mark.asyncio
async def test_timeout_log_does_not_leak_raw_query(monkeypatch, caplog):
    _stub_httpx(monkeypatch, raises=httpx.TimeoutException("slow"))

    with caplog.at_level(logging.DEBUG):
        await WebSearchTool(api_key="key").execute({"query": _PII_QUERY})

    _assert_query_not_in_logs(caplog, _PII_QUERY)


@pytest.mark.asyncio
async def test_generic_http_error_log_does_not_leak_raw_query(monkeypatch, caplog):
    _stub_httpx(monkeypatch, raises=httpx.ConnectError("dns blew up"))

    with caplog.at_level(logging.DEBUG):
        await WebSearchTool(api_key="key").execute({"query": _PII_QUERY})

    _assert_query_not_in_logs(caplog, _PII_QUERY)
