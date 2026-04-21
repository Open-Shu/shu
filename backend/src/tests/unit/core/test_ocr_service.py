"""Unit tests for OCRService protocol and DI wiring.

Tests cover:
- OCRService protocol conformance
- OCRResult dataclass
- Service resolution logic (API key set → external, absent → local)
"""

from unittest.mock import MagicMock, patch

from shu.core.ocr_service import (
    OCRResult,
    OCRService,
    get_ocr_service,
)


class _ConformingOCRService:
    """Minimal class that satisfies the OCRService protocol."""

    async def extract_text(self, file_bytes: bytes, mime_type: str) -> OCRResult:
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
    """Test get_ocr_service() resolution logic."""

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
    def test_resolution_follows_current_settings(self, mock_settings):
        """Each call resolves against current settings — no stale caching."""
        settings = MagicMock()
        settings.mistral_ocr_api_key = None
        mock_settings.return_value = settings

        from shu.services.external_ocr_service import ExternalOCRService
        from shu.services.local_ocr_service import LocalOCRService

        assert isinstance(get_ocr_service(), LocalOCRService)

        settings.mistral_ocr_api_key = "sk-test"
        settings.mistral_ocr_base_url = "https://openrouter.ai/api/v1"
        settings.mistral_ocr_model = "mistralai/mistral-ocr-latest"

        assert isinstance(get_ocr_service(), ExternalOCRService)
