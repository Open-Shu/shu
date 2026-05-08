"""Unit tests for ExternalOCRService."""

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shu.billing.enforcement import SubscriptionInactiveError
from shu.core.exceptions import InactiveProviderError
from shu.core.ocr_service import OCRResult
from shu.services.external_ocr_service import ExternalOCRService
from tests.unit.conftest import disabled_billing_state


async def _consume_post_body(mock_client: MagicMock) -> tuple[dict, int]:
    """Reassemble and parse the JSON request body that the streaming iterator
    fed to ``httpx.AsyncClient.post(content=...)`` (SHU-738).

    Pre-SHU-738 these tests inspected ``call_args[1]["json"]`` directly. Now
    the body is built incrementally — this helper consumes the async iterator
    once and returns ``(parsed_payload, total_byte_length)`` so individual
    assertions don't have to re-iterate (async generators are single-use).
    """
    call = mock_client.post.call_args
    body_iter = call.kwargs["content"]
    chunks: list[bytes] = []
    async for chunk in body_iter:
        chunks.append(chunk)
    body_bytes = b"".join(chunks)
    return json.loads(body_bytes.decode("ascii")), len(body_bytes)

# OCR cost is now DB-sourced (cost_per_input_unit on the llm_models row, seeded from
# model_pricing.py). Tests that assert on total cost stub a model row with this rate.
_OCR_PER_PAGE_RATE = Decimal("0.002")


def _make_service(**kwargs) -> ExternalOCRService:
    defaults = {
        "api_key": "sk-test",
        "api_base_url": "https://api.mistral.ai/v1",
        "model_name": "mistral-ocr-latest",
    }
    defaults.update(kwargs)
    return ExternalOCRService(**defaults)


@pytest.fixture(autouse=True)
def _stub_active_guard():
    """Bypass the DB-touching active check in all tests in this module.

    ``_ensure_active`` opens a real session for the seed lookup AND calls
    ``ensure_provider_and_model_active`` (which opens another). Unit tests must
    not hit Postgres. Stubbing the wrapper as a whole covers both DB calls.
    ``TestExtractTextInactiveGuard`` overrides this fixture at class scope to
    let the real guard run against mocked sessions.
    """
    with patch.object(
        ExternalOCRService,
        "_ensure_active",
        new_callable=AsyncMock,
    ) as stub:
        yield stub


def _mock_ocr_response(pages: list[dict]) -> httpx.Response:
    """Build a mock httpx.Response with the given pages."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = {"pages": pages}
    response.raise_for_status = MagicMock()
    return response


@contextmanager
def _patched_httpx(response):
    """Patch httpx.AsyncClient AND ExternalOCRService._record_usage for full DB isolation.

    Previously only httpx was mocked, which let the real `_record_usage` method run
    and write rows to whatever database the host env pointed at (a live dev Postgres
    in typical setups). Tests that exercise the extract_text control flow don't need
    to verify usage persistence — that is covered separately in TestUsageRecording —
    so a blanket mock on `_record_usage` keeps these tests hermetic.
    """
    with (
        patch("shu.services.external_ocr_service.httpx.AsyncClient") as mock_client_cls,
        patch.object(ExternalOCRService, "_record_usage", new_callable=AsyncMock),
    ):
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        yield mock_client


class TestExternalOCRService:
    """Test ExternalOCRService API calls and response parsing."""

    @pytest.mark.asyncio
    async def test_extract_text_pdf(self):
        """Should send PDF as base64 data URL and parse page markdown."""
        pdf_bytes = b"%PDF-fake-content"
        response = _mock_ocr_response([
            {"index": 0, "markdown": "Page one text"},
            {"index": 1, "markdown": "Page two text"},
        ])

        svc = _make_service()

        with _patched_httpx(response) as mock_client:
            result = await svc.extract_text(file_bytes=pdf_bytes, mime_type="application/pdf")

        assert isinstance(result, OCRResult)
        assert result.text == "Page one text\n\nPage two text"
        assert result.engine == "mistral-ocr-latest"
        assert result.page_count == 2
        assert result.confidence is None

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://api.mistral.ai/v1/ocr"
        payload, body_len = await _consume_post_body(mock_client)
        assert payload["model"] == "mistral-ocr-latest"
        assert payload["document"]["type"] == "document_url"
        expected_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        assert payload["document"]["document_url"] == f"data:application/pdf;base64,{expected_b64}"
        # Content-Length must match the body the iterator produced — Mistral
        # rejects mismatched lengths and chunked transfer encoding.
        assert call_args.kwargs["headers"]["Content-Length"] == str(body_len)

    @pytest.mark.asyncio
    async def test_extract_text_image(self):
        """Should send image bytes as base64 data URL."""
        image_bytes = b"\x89PNG\r\n\x1a\n"
        response = _mock_ocr_response([
            {"index": 0, "markdown": "Image text"},
        ])

        svc = _make_service()

        with _patched_httpx(response) as mock_client:
            result = await svc.extract_text(file_bytes=image_bytes, mime_type="image/png")

        assert result.text == "Image text"
        assert result.page_count == 1

        payload, _ = await _consume_post_body(mock_client)
        expected_b64 = base64.b64encode(image_bytes).decode("ascii")
        assert payload["document"]["document_url"] == f"data:image/png;base64,{expected_b64}"

    @pytest.mark.asyncio
    async def test_unsupported_mime_type_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="does not support mime type"):
            await svc.extract_text(file_bytes=b"data", mime_type="text/plain")

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        """API errors must raise, not be swallowed."""
        svc = _make_service()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response
        )

        with _patched_httpx(mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                await svc.extract_text(file_bytes=b"%PDF-content", mime_type="application/pdf")

    @pytest.mark.asyncio
    async def test_empty_pages_response(self):
        """Should handle response with no pages gracefully."""
        response = _mock_ocr_response([])
        svc = _make_service()

        with _patched_httpx(response):
            result = await svc.extract_text(file_bytes=b"%PDF-content", mime_type="application/pdf")

        assert result.text == ""
        assert result.page_count == 0

    @pytest.mark.asyncio
    async def test_auth_header(self):
        """Should send Bearer token in Authorization header."""
        response = _mock_ocr_response([{"markdown": "text"}])
        svc = _make_service(api_key="sk-secret-key")

        with _patched_httpx(response) as mock_client:
            await svc.extract_text(file_bytes=b"data", mime_type="image/jpeg")

        headers = mock_client.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-secret-key"

    @pytest.mark.asyncio
    async def test_extract_text_pdf_via_file_path_streams_from_disk(self, tmp_path):
        """SHU-738: file_path branch streams base64-encoded chunks from disk.

        The body the iterator emits must be byte-for-byte equivalent to the
        buffered build for the same content — Mistral can't tell whether the
        request was streamed; correctness depends on parity.
        """
        pdf_bytes = b"%PDF-fake-content-and-then-some-more-bytes-to-cross-chunks" * 17
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(pdf_bytes)

        response = _mock_ocr_response([{"index": 0, "markdown": "page text"}])
        svc = _make_service()

        with _patched_httpx(response) as mock_client:
            result = await svc.extract_text(file_path=str(pdf_path), mime_type="application/pdf")

        assert result.text == "page text"
        payload, body_len = await _consume_post_body(mock_client)
        # Must produce the same data URL as the bytes branch would have produced.
        expected_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        assert payload["document"]["document_url"] == f"data:application/pdf;base64,{expected_b64}"
        # Content-Length advertised on the wire must match the bytes the iterator emitted.
        assert mock_client.post.call_args.kwargs["headers"]["Content-Length"] == str(body_len)

    @pytest.mark.asyncio
    async def test_streaming_body_byte_parity_with_bytes_branch(self, tmp_path):
        """The path branch and the bytes branch must produce identical request bodies.

        Same content, two entry points: regression test for SHU-738's claim
        that the streaming change is byte-for-byte equivalent to the
        pre-change buffered build.
        """
        pdf_bytes = b"some pdf-ish bytes" * 200  # not a multiple of 3, exercises padding
        pdf_path = tmp_path / "parity.pdf"
        pdf_path.write_bytes(pdf_bytes)

        response = _mock_ocr_response([])
        svc = _make_service()

        with _patched_httpx(response) as mock_client:
            await svc.extract_text(file_bytes=pdf_bytes, mime_type="application/pdf")
        bytes_payload, bytes_len = await _consume_post_body(mock_client)

        with _patched_httpx(response) as mock_client:
            await svc.extract_text(file_path=str(pdf_path), mime_type="application/pdf")
        path_payload, path_len = await _consume_post_body(mock_client)

        assert bytes_payload == path_payload
        assert bytes_len == path_len

    @pytest.mark.asyncio
    async def test_rejects_both_or_neither_input(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="exactly one"):
            await svc.extract_text(
                file_bytes=b"x", file_path="/tmp/y", mime_type="application/pdf"
            )
        with pytest.raises(ValueError, match="exactly one"):
            await svc.extract_text(mime_type="application/pdf")

    def test_base_url_trailing_slash_stripped(self):
        svc = ExternalOCRService(
            api_key="k", api_base_url="https://api.mistral.ai/v1/", model_name="m"
        )
        assert svc._api_base_url == "https://api.mistral.ai/v1"

    @pytest.mark.asyncio
    async def test_confidence_score_aggregation(self):
        """Should compute average confidence across pages with scores."""
        response = _mock_ocr_response([
            {
                "markdown": "Page 1",
                "confidence_scores": {"average_page_confidence_score": 0.90},
            },
            {
                "markdown": "Page 2",
                "confidence_scores": {"average_page_confidence_score": 0.80},
            },
            {
                "markdown": "Page 3",
                "confidence_scores": {},
            },
        ])

        svc = _make_service()

        with _patched_httpx(response):
            result = await svc.extract_text(file_bytes=b"%PDF-content", mime_type="application/pdf")

        assert result.confidence == pytest.approx(0.85)
        assert result.page_count == 3


class TestUsageRecording:
    """Test that OCR usage is recorded with correct cost."""

    @pytest.mark.asyncio
    async def test_record_usage_inserts_correct_cost(self):
        """_record_usage should record with per-page cost sourced from the DB model row.

        After SHU-715, the per-page math lives in record_llm_usage's DB-rate
        fallback rather than inline in ExternalOCRService. This test still
        exercises the full path: OCR caller passes total_cost=Decimal(0),
        the helper resolves the model via session.get and computes
        input_cost = page_count * cost_per_input_unit.
        """
        svc = _make_service()
        svc._provider_id = "provider-123"
        svc._model_id = "model-456"

        # Fake LLMModel row that session.get(LLMModel, ...) will return.
        # cost_per_output_unit must be None (not a default MagicMock) so the
        # two-tier fallback leaves output_cost at Decimal(0) instead of
        # multiplying by a MagicMock. model_name must be an explicit string
        # so the snapshot capture assertion below compares scalars, not a
        # MagicMock auto-attribute.
        fake_model = MagicMock()
        fake_model.cost_per_input_unit = _OCR_PER_PAGE_RATE
        fake_model.cost_per_output_unit = None
        fake_model.model_name = svc._model_name

        # Provider row for snapshot-name capture (SHU-727). The helper calls
        # session.get(LLMProvider, ...) and session.get(LLMModel, ...); dispatch
        # by the class argument so each lookup returns the right mock.
        fake_provider = MagicMock()
        fake_provider.name = "Shu Curated: Mistral"

        from shu.models.llm_provider import LLMModel, LLMProvider

        async def _get(cls, obj_id):  # noqa: ARG001
            if cls is LLMProvider:
                return fake_provider
            if cls is LLMModel:
                return fake_model
            raise AssertionError(f"Unexpected session.get lookup for {cls!r}")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=_get)
        # begin_nested() is sync, returns an async context manager (AsyncSessionTransaction)
        mock_savepoint = MagicMock()
        mock_savepoint.__aenter__ = AsyncMock()
        mock_savepoint.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin_nested = MagicMock(return_value=mock_savepoint)
        # session.add() is SYNC in SQLAlchemy. AsyncMock's default would return
        # a coroutine that record_llm_usage never awaits, producing a
        # RuntimeWarning that masks real unawaited-coroutine regressions.
        mock_session.add = MagicMock()

        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "shu.services.external_ocr_service.get_async_session_local",
            return_value=mock_session_factory,
        ), patch.object(svc, "_resolve_provider_and_model", new_callable=AsyncMock, return_value=True):
            await svc._record_usage(page_count=5, user_id="user-789")

        mock_session.add.assert_called_once()
        record = mock_session.add.call_args[0][0]
        assert record.provider_id == "provider-123"
        assert record.model_id == "model-456"
        assert record.user_id == "user-789"
        assert record.request_type == "ocr"
        assert record.total_cost == _OCR_PER_PAGE_RATE * 5
        assert record.input_cost == _OCR_PER_PAGE_RATE * 5
        assert record.output_cost == Decimal("0")
        assert record.request_metadata == {"page_count": 5}
        assert record.success is True
        # Snapshot columns captured at insert (SHU-727) — regression guard
        # for the OCR path specifically.
        assert record.provider_name == "Shu Curated: Mistral"
        assert record.model_name == svc._model_name
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_usage_calls_ensure_provider(self):
        """_record_usage should call _resolve_provider_and_model on first use.

        Resolver is stubbed to return False so the method returns early via the
        "provider/model not seeded" branch. Without that, the downstream
        `async with session.begin_nested()` call would fire against AsyncMock's
        default — begin_nested would return a coroutine instead of an async
        context manager, raising AttributeError inside the try/except and
        producing a "coroutine never awaited" RuntimeWarning. The test's only
        contract is "resolver gets called on first use"; exercising the DB
        write path isn't part of that contract.
        """
        svc = _make_service()
        assert svc._provider_id is None

        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Patch the import binding inside external_ocr_service, not the
        # origin module — `from ..core.database import get_async_session_local`
        # creates a separate name in this module's namespace that
        # `shu.core.database.get_async_session_local` patches can't see.
        # Without this, the real session factory runs in CI (no asyncpg driver
        # loaded), raises, the bare `except` in `_record_usage` swallows it,
        # and resolver never gets called.
        with patch(
            "shu.services.external_ocr_service.get_async_session_local",
            return_value=mock_session_factory,
        ), patch.object(
            svc, "_resolve_provider_and_model", new_callable=AsyncMock, return_value=False
        ) as mock_ensure:
            await svc._record_usage(page_count=1)

        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_usage_failure_is_logged_not_raised(self):
        """Usage recording failures should not propagate to the caller."""
        svc = _make_service()
        svc._provider_id = "p"
        svc._model_id = "m"

        with patch(
            "shu.services.external_ocr_service.get_async_session_local",
            side_effect=RuntimeError("DB down"),
        ):
            await svc._record_usage(page_count=3)

    @pytest.mark.asyncio
    async def test_extract_text_calls_record_usage(self):
        """extract_text should call _record_usage with the page count."""
        response = _mock_ocr_response([
            {"markdown": "Page 1"},
            {"markdown": "Page 2"},
            {"markdown": "Page 3"},
        ])
        svc = _make_service()

        with _patched_httpx(response):
            with patch.object(svc, "_record_usage", new_callable=AsyncMock) as mock_record:
                await svc.extract_text(file_bytes=b"%PDF-content", mime_type="application/pdf")

        mock_record.assert_called_once_with(3, usage_info={}, observed_page_count=3, user_id=None)


class TestResolveProviderAndModel:
    """Test _resolve_provider_and_model lookup-only behavior."""

    @pytest.mark.asyncio
    async def test_returns_false_when_provider_missing(self):
        """Should return False and not cache IDs when provider not seeded."""
        svc = _make_service()

        mock_llm_service = MagicMock()
        mock_llm_service.get_provider_by_name = AsyncMock(return_value=None)

        mock_session = AsyncMock()

        with patch("shu.services.external_ocr_service.LLMService", return_value=mock_llm_service):
            result = await svc._resolve_provider_and_model(mock_session)

        assert result is False
        assert svc._provider_id is None

    @pytest.mark.asyncio
    async def test_returns_false_when_model_missing(self):
        """Should return False when provider exists but model not seeded."""
        svc = _make_service()

        mock_provider = MagicMock()
        mock_provider.id = "provider-id"
        mock_provider.models = []

        mock_llm_service = MagicMock()
        mock_llm_service.get_provider_by_name = AsyncMock(return_value=mock_provider)

        mock_session = AsyncMock()

        with patch("shu.services.external_ocr_service.LLMService", return_value=mock_llm_service):
            result = await svc._resolve_provider_and_model(mock_session)

        assert result is False
        assert svc._provider_id == "provider-id"
        assert svc._model_id is None

    @pytest.mark.asyncio
    async def test_finds_existing_provider_and_model(self):
        """Should cache IDs and return True when both are seeded."""
        svc = _make_service()

        mock_model = MagicMock()
        mock_model.id = "existing-model-id"
        mock_model.model_name = "mistral-ocr-latest"
        mock_model.model_type = MagicMock()
        mock_model.model_type.value = "ocr"

        # Make model_type comparison work
        from shu.models.llm_provider import ModelType
        mock_model.model_type = ModelType.OCR

        mock_provider = MagicMock()
        mock_provider.id = "existing-provider-id"
        mock_provider.models = [mock_model]

        mock_llm_service = MagicMock()
        mock_llm_service.get_provider_by_name = AsyncMock(return_value=mock_provider)

        mock_session = AsyncMock()

        with patch("shu.services.external_ocr_service.LLMService", return_value=mock_llm_service):
            await svc._resolve_provider_and_model(mock_session)

        assert svc._provider_id == "existing-provider-id"
        assert svc._model_id == "existing-model-id"
        mock_llm_service.create_provider.assert_not_called()
        mock_llm_service.create_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_already_cached(self):
        """Should return immediately if provider_id is already set."""
        svc = _make_service()
        svc._provider_id = "cached-p"
        svc._model_id = "cached-m"

        mock_session = AsyncMock()

        with patch("shu.services.external_ocr_service.LLMService") as mock_cls:
            await svc._resolve_provider_and_model(mock_session)

        mock_cls.assert_not_called()
        assert svc._provider_id == "cached-p"


class TestExtractTextInactiveGuard:
    """extract_text must delegate the is_active check to
    ``ensure_provider_and_model_active`` and propagate its raise.

    The active-check logic (what counts as inactive, warning content) is
    owned by and tested against ``external_model_resolver``; here we only
    verify the OCR service wires it correctly.
    """

    @pytest.fixture(autouse=True)
    def _stub_active_guard(self):
        """Shadow the module-level fixture (same name) so _ensure_active runs for real."""
        yield

    @pytest.mark.asyncio
    async def test_calls_resolver_with_ocr_call_type_and_propagates_raise(self):
        provider_id = "provider-123"
        model_id = "model-456"

        svc = _make_service()
        svc._provider_id = provider_id
        svc._model_id = model_id

        # `_ensure_active` opens a real session before delegating to the guard,
        # so we also stub the session factory to avoid CI hitting Postgres.
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "shu.services.external_ocr_service.get_async_session_local",
                return_value=mock_session_factory,
            ),
            patch.object(
                ExternalOCRService,
                "_resolve_provider_and_model",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "shu.services.external_ocr_service.ensure_provider_and_model_active",
                new_callable=AsyncMock,
                side_effect=InactiveProviderError("provider inactive: " + provider_id),
            ) as mock_guard,
            patch("shu.services.external_ocr_service.httpx.AsyncClient") as mock_client_cls,
            patch.object(ExternalOCRService, "_record_usage", new_callable=AsyncMock),
        ):
            with pytest.raises(InactiveProviderError):
                await svc.extract_text(file_bytes=b"%PDF-content", mime_type="application/pdf")

            mock_client_cls.assert_not_called()
            mock_guard.assert_awaited_once()
            args, kwargs = mock_guard.await_args
            assert args == (provider_id, model_id)
            assert kwargs["call_type"] == "OCR"
            assert kwargs["session"] is not None


class TestExtractTextSubscriptionGate:
    """The subscription gate must fire before any billable work.

    Pins the chokepoint invariant: regardless of caller (worker, plugin
    host, or direct test invocation), `extract_text` must raise
    `SubscriptionInactiveError` and never reach httpx.
    """

    @pytest.mark.asyncio
    async def test_inactive_subscription_raises_before_any_work(self, install_stub_cache):
        """Disabled cache → `extract_text` raises before any httpx call.

        No httpx patching: a regression that lets the gate fall through would
        attempt a real network call, which surfaces as a louder failure
        (connection error) than a subtle missed assertion.
        """
        install_stub_cache(disabled_billing_state())
        svc = _make_service()

        with pytest.raises(SubscriptionInactiveError):
            await svc.extract_text(file_bytes=b"image-bytes", mime_type="image/png")
