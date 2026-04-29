"""Unit tests for extract_text_with_ocr_fallback and _run_ocr_service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.ocr_routing import RoutingDecision
from shu.core.ocr_service import (
    OCRResult,
    _run_ocr_service,
    extract_text_with_ocr_fallback,
    reset_ocr_service,
)


def _mock_text_extractor(text: str = "", metadata: dict | None = None):
    """Return a patched TextExtractor class whose extract_text returns given text."""
    mock_instance = MagicMock()
    mock_instance.extract_text = AsyncMock(
        return_value={
            "text": text,
            "metadata": metadata or {"method": "pdf_text", "engine": "pymupdf", "duration": 0.1},
        }
    )
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


def _mock_ocr_service(text: str = "OCR result", engine: str = "mistral-ocr"):
    svc = MagicMock()
    svc.extract_text = AsyncMock(
        return_value=OCRResult(text=text, engine=engine, page_count=1, confidence=0.95)
    )
    return svc


def _classifier_says(use_ocr: bool, *, fraction: float = 0.0, page_count: int = 1) -> RoutingDecision:
    """Build a `RoutingDecision` stub with the given outcome.

    Tests don't need real per-page geometry; they only care which branch the
    orchestrator takes. The classifier itself has its own dedicated tests in
    test_ocr_routing.py.
    """
    return RoutingDecision(
        use_ocr=use_ocr,
        real_text_fraction=fraction,
        page_count=page_count,
        pages=[],
        reason="stub",
    )


def _stub_classifier(use_ocr: bool) -> AsyncMock:
    """Return an AsyncMock that mimics `_classify_pdf_for_routing`.

    `_classify_pdf_for_routing` returns ``(decision, open_doc_or_None)``.
    Tests pass ``None`` for the doc — telling the orchestrator there's no
    handle to hand off, so TextExtractor falls back to opening its own.
    The AsyncMock interface lets tests await the patched seam without
    needing a real fitz handle.
    """
    return AsyncMock(return_value=(_classifier_says(use_ocr=use_ocr), None))


class TestModeValidation:
    """SHU-728: legacy 'fallback' / 'text_only' values raise ValueError at the orchestrator boundary."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("legacy_mode", ["fallback", "text_only", "FALLBACK", "Text_Only"])
    async def test_legacy_mode_strings_raise_value_error(self, legacy_mode):
        with pytest.raises(ValueError, match="Invalid ocr_mode"):
            await extract_text_with_ocr_fallback(
                file_bytes=b"x",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode=legacy_mode,
            )

    @pytest.mark.asyncio
    async def test_unknown_mode_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid ocr_mode"):
            await extract_text_with_ocr_fallback(
                file_bytes=b"x",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="not_a_real_mode",
            )


class TestExtractTextNeverMode:
    """`never` mode must never call the OCR service or the classifier."""

    @pytest.mark.asyncio
    async def test_never_calls_ocr(self):
        mock_cls, _ = _mock_text_extractor("Some extracted text")
        config_manager = MagicMock()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
            patch("shu.core.ocr_service._classify_pdf_for_routing") as mock_classify,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=config_manager,
                ocr_mode="never",
            )

        assert result["text"] == "Some extracted text"
        mock_get_ocr.assert_not_called()
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_mode_returns_empty_when_no_text(self):
        mock_cls, _ = _mock_text_extractor("")
        config_manager = MagicMock()

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=config_manager,
                ocr_mode="never",
            )

        assert result["text"] == ""
        mock_get_ocr.assert_not_called()


class TestExtractTextAlwaysMode:
    """`always` mode skips the classifier and routes straight to OCR."""

    @pytest.mark.asyncio
    async def test_always_skips_text_extraction_and_classifier(self):
        ocr_svc = _mock_ocr_service("OCR text")

        with (
            patch("shu.core.ocr_service.TextExtractor") as mock_cls,
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing") as mock_classify,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="always",
            )

        mock_cls.assert_not_called()
        mock_classify.assert_not_called()
        assert result["text"] == "OCR text"
        assert result["metadata"]["method"] == "ocr"


class TestExtractTextAutoMode:
    """`auto` mode runs the classifier on PDFs and routes by its decision."""

    @pytest.mark.asyncio
    async def test_classifier_routes_to_text_when_doc_is_text_bearing(self):
        mock_cls, _ = _mock_text_extractor("real text content")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
            patch("shu.core.ocr_service._classify_pdf_for_routing", new=_stub_classifier(use_ocr=False)),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        assert result["text"] == "real text content"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_classifier_routes_to_ocr_when_doc_is_image_only(self):
        ocr_svc = _mock_ocr_service("OCR'd content")

        with (
            patch("shu.core.ocr_service.TextExtractor") as mock_cls,
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing", new=_stub_classifier(use_ocr=True)),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"pdf-bytes",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        # TextExtractor is *not* called when classifier says OCR — that's the
        # whole point of the new flow vs. the old "try fast-path, fall back"
        # logic. Re-running text extraction would just produce more noise.
        mock_cls.assert_not_called()
        assert result["text"] == "OCR'd content"
        assert result["metadata"]["method"] == "ocr"

    @pytest.mark.asyncio
    async def test_image_mime_type_skips_classifier_and_goes_to_ocr(self):
        """Image MIME types (PNG/JPG/etc.) have no per-page geometry to classify."""
        ocr_svc = _mock_ocr_service("OCR from image")

        with (
            patch("shu.core.ocr_service.TextExtractor") as mock_cls,
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing") as mock_classify,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"image-bytes",
                mime_type="image/png",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        mock_classify.assert_not_called()
        mock_cls.assert_not_called()
        assert result["text"] == "OCR from image"


class TestOCRMimeTypeGate:
    """Non-OCR-eligible types (txt, docx, etc.) must never reach the OCR service."""

    @pytest.mark.asyncio
    async def test_txt_file_skips_ocr_in_auto_mode(self):
        mock_cls, _ = _mock_text_extractor("hi")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
            patch("shu.core.ocr_service._classify_pdf_for_routing") as mock_classify,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"hi",
                mime_type="text/plain",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        assert result["text"] == "hi"
        mock_get_ocr.assert_not_called()
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_docx_skips_ocr_in_auto_mode(self):
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        mock_cls, _ = _mock_text_extractor("docx text")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"docx",
                mime_type=docx_mime,
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        assert result["text"] == "docx text"
        mock_get_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_always_mode_non_ocr_type_falls_back_to_text_extraction(self):
        """ocr_mode='always' with a non-OCR type uses text extraction, not crash."""
        mock_cls, _ = _mock_text_extractor("text content")

        with (
            patch("shu.core.ocr_service.TextExtractor", mock_cls),
            patch("shu.core.ocr_service.get_ocr_service") as mock_get_ocr,
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"text",
                mime_type="text/plain",
                config_manager=MagicMock(),
                ocr_mode="always",
            )

        assert result["text"] == "text content"
        mock_get_ocr.assert_not_called()


class TestRunOCRService:
    """_run_ocr_service timing and metadata shape."""

    def setup_method(self):
        reset_ocr_service()

    def teardown_method(self):
        reset_ocr_service()

    @pytest.mark.asyncio
    async def test_returns_duration_in_metadata(self):
        ocr_svc = _mock_ocr_service("text", engine="mistral-ocr-latest")

        with patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc):
            result = await _run_ocr_service("application/pdf", "auto", file_bytes=b"data")

        assert result["metadata"]["method"] == "ocr"
        assert result["metadata"]["engine"] == "mistral-ocr-latest"
        assert result["metadata"]["confidence"] == 0.95
        assert "duration" in result["metadata"]
        assert result["metadata"]["duration"] > 0
        assert result["metadata"]["details"]["page_count"] == 1
        assert result["metadata"]["details"]["ocr_mode"] == "auto"
        assert result["metadata"]["details"]["processing_time"] > 0


class TestUserIdThreading:
    """SHU-700 regression: user_id must reach the OCR service regardless of branch.

    Earlier user_id plumbing edits caught the `always` path but missed the
    auto path's OCR branch — `llm_usage` rows for auto-mode OCR had NULL
    user_id. These tests guard both branches under the new classifier flow.
    """

    @pytest.mark.asyncio
    async def test_user_id_reaches_ocr_service_on_auto_path(self):
        """Auto path: classifier says OCR → user_id must thread through."""
        mock_ocr_svc = _mock_ocr_service()

        with (
            patch("shu.core.ocr_service.TextExtractor") as _mock_cls,
            patch("shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing", new=_stub_classifier(use_ocr=True)),
        ):
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
                user_id="user-abc",
            )

        mock_ocr_svc.extract_text.assert_called_once()
        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") == "user-abc"

    @pytest.mark.asyncio
    async def test_user_id_reaches_ocr_service_on_always_path(self):
        mock_ocr_svc = _mock_ocr_service()

        with patch("shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc):
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="always",
                user_id="user-xyz",
            )

        mock_ocr_svc.extract_text.assert_called_once()
        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") == "user-xyz"

    @pytest.mark.asyncio
    async def test_user_id_default_is_none_when_not_provided(self):
        mock_ocr_svc = _mock_ocr_service()

        with (
            patch("shu.core.ocr_service.TextExtractor"),
            patch("shu.core.ocr_service.get_ocr_service", return_value=mock_ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing", new=_stub_classifier(use_ocr=True)),
        ):
            await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        assert mock_ocr_svc.extract_text.call_args.kwargs.get("user_id") is None


class TestTextExtractionFailureFallback:
    """SHU-728 concern (1) safety net: classifier-routed text extraction
    failures must fall back to OCR with a visible WARNING.

    Pre-SHU-728, the orchestrator's bare `except Exception` silently rerouted
    a TextExtractor crash to OCR (over-billing risk, but recovered the data).
    The strict post-SHU-728 path lost that recovery. This class re-establishes
    it with explicit logging so a sustained uptick is detectable.
    """

    @pytest.mark.asyncio
    async def test_text_extraction_failure_falls_back_to_ocr(self, caplog):
        import logging

        broken_extractor = MagicMock()
        broken_extractor.extract_text = AsyncMock(side_effect=RuntimeError("corrupt text layer"))
        ocr_svc = _mock_ocr_service("OCR rescued")

        with (
            patch("shu.core.ocr_service.TextExtractor", return_value=broken_extractor),
            patch("shu.core.ocr_service.get_ocr_service", return_value=ocr_svc),
            patch("shu.core.ocr_service._classify_pdf_for_routing", new=_stub_classifier(use_ocr=False)),
            caplog.at_level(logging.WARNING, logger="shu.core.ocr_service"),
        ):
            result = await extract_text_with_ocr_fallback(
                file_bytes=b"%PDF-dummy",
                mime_type="application/pdf",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )

        assert result["text"] == "OCR rescued"
        assert result["metadata"]["method"] == "ocr"
        # The fallback must announce itself: a sustained uptick of these
        # warnings is the operator's signal that something is producing
        # PDFs that classify as text-bearing but can't actually be extracted.
        assert any(
            "ocr_routing.text_extraction_failed_falling_back_to_ocr" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        ), f"Expected fallback WARNING, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_non_ocr_eligible_extraction_failure_propagates(self):
        """For non-OCR-eligible types (DOCX, txt, ...), there's no OCR fallback —
        a parser failure must raise rather than be silently swallowed."""
        broken_extractor = MagicMock()
        broken_extractor.extract_text = AsyncMock(side_effect=RuntimeError("docx parser broke"))

        with (
            patch("shu.core.ocr_service.TextExtractor", return_value=broken_extractor),
            pytest.raises(RuntimeError, match="docx parser broke"),
        ):
            await extract_text_with_ocr_fallback(
                file_bytes=b"docx-bytes",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                config_manager=MagicMock(),
                ocr_mode="auto",
            )
