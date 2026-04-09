"""ExternalOCRService — Mistral OCR via OpenRouter."""

from shu.core.ocr_service import OCRResult


class ExternalOCRService:
    """Calls Mistral OCR via OpenRouter's chat completions API."""

    def __init__(self, api_key: str, api_base_url: str, model_name: str) -> None:
        self._api_key = api_key
        self._api_base_url = api_base_url
        self._model_name = model_name

    async def extract_text(self, file_bytes: bytes, mime_type: str) -> OCRResult:
        raise NotImplementedError("ExternalOCRService not yet implemented (task 11)")
