"""Unit tests for OCRService protocol and DI wiring.

Tests cover:
- OCRService protocol conformance
- OCRResult dataclass
- DI wiring (get_ocr_service, reset_ocr_service)
- Service resolution logic (API key set → external, absent → local)
"""

from unittest.mock import MagicMock, patch

from shu.core.ocr_service import (
    OCRResult,
    OCRService,
    get_ocr_service,
    reset_ocr_service,
)


class _ConformingOCRService:
    """Minimal class that satisfies the OCRService protocol."""

    async def extract_text(
        self,
        *,
        file_bytes: bytes | None = None,
        file_path: str | None = None,
        mime_type: str,
        user_id: str | None = None,
    ) -> OCRResult:
        return OCRResult(text="hello", engine="test")


class _NonConformingService:
    """Class that does NOT satisfy the OCRService protocol."""

    async def do_something(self) -> None:
        pass


class TestOCRServiceProtocol:
    """Test OCRService protocol conformance checks."""

    def test_conforming_class_passes_isinstance(self):
        assert isinstance(_ConformingOCRService(), OCRService)

    def test_non_conforming_class_fails_isinstance(self):
        assert not isinstance(_NonConformingService(), OCRService)

    def test_protocol_is_runtime_checkable(self):
        assert hasattr(OCRService, "__protocol_attrs__") or hasattr(
            OCRService, "_is_runtime_protocol"
        )


class TestOCRResult:
    """Test OCRResult dataclass."""

    def test_required_fields(self):
        result = OCRResult(text="extracted", engine="easyocr")
        assert result.text == "extracted"
        assert result.engine == "easyocr"
        assert result.page_count is None
        assert result.confidence is None

    def test_all_fields(self):
        result = OCRResult(
            text="extracted", engine="mistral-ocr", page_count=3, confidence=0.95
        )
        assert result.page_count == 3
        assert result.confidence == 0.95


class TestGetOCRService:
    """Test get_ocr_service() singleton and resolution logic."""

    def setup_method(self):
        reset_ocr_service()

    def teardown_method(self):
        reset_ocr_service()

    @patch("shu.core.ocr_service.get_settings_instance")
    def test_returns_singleton(self, mock_settings):
        """Two calls should return the same instance."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = None
        mock_settings.return_value = settings

        svc1 = get_ocr_service()
        svc2 = get_ocr_service()

        assert svc1 is svc2

    def test_reset_clears_singleton(self):
        import shu.core.ocr_service as mod

        mod._ocr_service = MagicMock(spec=OCRService)
        assert mod._ocr_service is not None

        reset_ocr_service()
        assert mod._ocr_service is None

    @patch("shu.core.ocr_service.get_settings_instance")
    def test_api_key_set_uses_external(self, mock_settings):
        """When SHU_MISTRAL_OCR_API_KEY is set, ExternalOCRService is used."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = "sk-test-key"
        settings.mistral_ocr_base_url = "https://openrouter.ai/api/v1"
        settings.mistral_ocr_model = "mistralai/mistral-ocr-latest"
        mock_settings.return_value = settings

        svc = get_ocr_service()

        from shu.services.external_ocr_service import ExternalOCRService

        assert isinstance(svc, ExternalOCRService)
        assert svc._api_key == "sk-test-key"
        assert svc._model_name == "mistralai/mistral-ocr-latest"

    @patch("shu.core.ocr_service.get_settings_instance")
    def test_no_api_key_uses_local(self, mock_settings):
        """When SHU_MISTRAL_OCR_API_KEY is not set, LocalOCRService is used."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = None
        mock_settings.return_value = settings

        svc = get_ocr_service()

        from shu.services.local_ocr_service import LocalOCRService

        assert isinstance(svc, LocalOCRService)

    @patch("shu.core.ocr_service.get_settings_instance")
    def test_empty_string_api_key_uses_local(self, mock_settings):
        """An empty string API key should fall back to local."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = ""
        mock_settings.return_value = settings

        svc = get_ocr_service()

        from shu.services.local_ocr_service import LocalOCRService

        assert isinstance(svc, LocalOCRService)

    @patch("shu.core.ocr_service.get_settings_instance")
    def test_external_singleton_returns_same_instance(self, mock_settings):
        """External service should be cached as a singleton across calls."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = "sk-test"
        settings.mistral_ocr_base_url = "https://openrouter.ai/api/v1"
        settings.mistral_ocr_model = "mistralai/mistral-ocr-latest"
        mock_settings.return_value = settings

        svc1 = get_ocr_service()
        svc2 = get_ocr_service()

        assert svc1 is svc2


class TestSelectInitialWorkloadType:
    """SHU-739: routing decision at upload time picks the right queue.

    Truth table:

        | MIME                         | NEVER | ALWAYS | AUTO     |
        | ---------------------------- | ----- | ------ | -------- |
        | non-OCR-eligible (DOCX, txt) | TEXT  | TEXT   | TEXT     |
        | OCR-eligible non-PDF (image) | TEXT  | OCR    | OCR      |
        | PDF                          | TEXT  | OCR    | CLASSIFY |
    """

    def test_pdf_auto_routes_to_classify(self):
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        assert select_initial_workload_type("application/pdf", "auto") == WorkloadType.INGESTION_CLASSIFY

    def test_pdf_always_routes_to_ocr(self):
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        assert select_initial_workload_type("application/pdf", "always") == WorkloadType.INGESTION_OCR

    def test_pdf_never_routes_to_text(self):
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        assert select_initial_workload_type("application/pdf", "never") == WorkloadType.INGESTION_TEXT

    def test_image_auto_routes_to_ocr_directly(self):
        """Images have no per-page geometry to classify, so AUTO skips CLASSIFY."""
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        assert select_initial_workload_type("image/png", "auto") == WorkloadType.INGESTION_OCR
        assert select_initial_workload_type("image/jpeg", "always") == WorkloadType.INGESTION_OCR

    def test_image_never_routes_to_text(self):
        """NEVER + image is unusual but specified: text path."""
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        assert select_initial_workload_type("image/png", "never") == WorkloadType.INGESTION_TEXT

    def test_non_ocr_eligible_always_routes_to_text(self):
        """DOCX, plain text, etc. never go to OCR regardless of mode."""
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        for mode in ("auto", "always", "never"):
            assert (
                select_initial_workload_type(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document", mode
                )
                == WorkloadType.INGESTION_TEXT
            )
            assert select_initial_workload_type("text/plain", mode) == WorkloadType.INGESTION_TEXT

    def test_empty_or_unknown_mode_falls_back_to_auto(self):
        """Untrusted upload-path inputs use lenient coercion (matches plugin host)."""
        from shu.core.ocr_service import select_initial_workload_type
        from shu.core.workload_routing import WorkloadType

        # Empty string → AUTO → PDF gets classifier
        assert select_initial_workload_type("application/pdf", "") == WorkloadType.INGESTION_CLASSIFY
        # None → AUTO
        assert select_initial_workload_type("application/pdf", None) == WorkloadType.INGESTION_CLASSIFY
        # Unknown value → AUTO
        assert select_initial_workload_type("application/pdf", "bogus") == WorkloadType.INGESTION_CLASSIFY
