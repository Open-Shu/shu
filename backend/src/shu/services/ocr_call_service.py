"""
OCR Call Service for Shu RAG Backend.

This service provides LLM-based OCR capabilities for PDF and image ingestion,
using vision-capable models to extract text from documents.
"""

import base64
import logging
from typing import Optional, Dict, Any

from ..models.model_configuration import ModelConfiguration
from .base_caller_service import BaseCallerService, CallerResult

logger = logging.getLogger(__name__)

# Constants for OCR call configuration
OCR_CALL_MODEL_SETTING_KEY = "ocr_call_model_config_id"

# Default OCR prompt
DEFAULT_OCR_PROMPT = """You are an OCR assistant. Extract all text content from the provided image accurately.

Rules:
- Preserve the original text structure and formatting as much as possible
- Include headers, paragraphs, lists, and tables
- For tables, use markdown table format
- Preserve any code blocks with proper formatting
- Do not add any commentary or interpretation
- If the image contains no text, respond with "[NO TEXT DETECTED]"
- Output only the extracted text content
"""


class OcrCallService(BaseCallerService):
    """Service for vision-based OCR operations using designated LLM models."""

    SETTING_KEY = OCR_CALL_MODEL_SETTING_KEY
    REQUEST_TYPE = "ocr_call"

    async def get_model(self) -> Optional[ModelConfiguration]:
        """Get the currently designated model configuration for this caller."""
        return await self._get_designated_model()

    async def set_model(self, model_config_id: str, user_id: str) -> bool:
        """
        Set the designated model configuration for this caller.
        
        Validates that the model supports vision capabilities.
        """
        return await self._set_designated_model(model_config_id, user_id)

    async def clear_model(self, user_id: str) -> bool:
        """Clear the designated model configuration for this caller."""
        return await self._clear_designated_model(user_id)

    # Backward-compatible aliases
    get_ocr_call_model = get_model
    set_ocr_call_model = set_model
    clear_ocr_call_model = clear_model

    async def _validate_model_for_designation(self, model_config: ModelConfiguration) -> Optional[str]:
        """
        Validate that the model configuration supports vision capabilities.
        
        Returns:
            Error message if validation fails, None if valid
        """
        # Check functionalities for vision support
        functionalities = model_config.functionalities or {}
        if not functionalities.get("vision"):
            # Also check provider capabilities
            provider = model_config.llm_provider
            if provider:
                provider_caps = getattr(provider, "provider_capabilities", {}) or {}
                if not provider_caps.get("supports_vision"):
                    return f"Model configuration {model_config.id} does not support vision capabilities"
        
        return None

    async def ocr_image(
        self,
        image_data: bytes,
        image_type: str = "image/png",
        prompt: Optional[str] = None,
        user_id: str = "system",
        timeout_ms: int = 30000,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> CallerResult:
        """
        Extract text from an image using the designated OCR model.

        Args:
            image_data: Raw image bytes
            image_type: MIME type of the image (e.g., "image/png", "image/jpeg")
            prompt: Optional custom prompt (uses default OCR prompt if not provided)
            user_id: ID of the user making the request
            timeout_ms: Timeout in milliseconds (default 30s for larger images)
            config_overrides: Optional configuration overrides

        Returns:
            CallerResult with the extracted text or error
        """
        try:
            # Encode image to base64
            image_base64 = base64.b64encode(image_data).decode("utf-8")
            
            return await self.ocr_image_base64(
                image_base64=image_base64,
                image_type=image_type,
                prompt=prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
                config_overrides=config_overrides,
            )
        except Exception as e:
            logger.error(f"OCR image failed: {e}")
            return CallerResult(
                content="",
                success=False,
                error_message=str(e),
            )

    async def ocr_image_base64(
        self,
        image_base64: str,
        image_type: str = "image/png",
        prompt: Optional[str] = None,
        user_id: str = "system",
        timeout_ms: int = 30000,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> CallerResult:
        """
        Extract text from a base64-encoded image using the designated OCR model.

        Args:
            image_base64: Base64-encoded image data
            image_type: MIME type of the image
            prompt: Optional custom prompt
            user_id: ID of the user making the request
            timeout_ms: Timeout in milliseconds
            config_overrides: Optional configuration overrides

        Returns:
            CallerResult with the extracted text or error
        """
        try:
            system_prompt = prompt or DEFAULT_OCR_PROMPT

            # Build vision message with image content
            message_sequence = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_type};base64,{image_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this image."
                        }
                    ]
                }
            ]

            return await self._call(
                message_sequence=message_sequence,
                system_prompt=system_prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
                config_overrides=config_overrides,
            )
        except Exception as e:
            logger.error(f"OCR image base64 failed: {e}")
            return CallerResult(
                content="",
                success=False,
                error_message=str(e),
            )

    async def ocr_pdf_pages(
        self,
        page_image_data: bytes,
        page_number: int,
        image_type: str = "image/png",
        user_id: str = "system",
        timeout_ms: int = 30000,
    ) -> CallerResult:
        """
        Extract text from a single PDF page rendered as an image.

        Args:
            page_image_data: Raw image bytes of the rendered PDF page
            page_number: Page number (for logging/metadata)
            image_type: MIME type of the rendered image
            user_id: ID of the user making the request
            timeout_ms: Timeout in milliseconds

        Returns:
            CallerResult with the extracted text or error
        """

        # TODO: We'll have to distinguish here. Local models don't support file uploads, which means that we will have to convert those to images first. Remote models often support it natively.
        #       To do this correctly, we'll have to check the adapter for the configured model, and potentially convert the PDF into images.

        prompt = f"""You are an OCR assistant processing page {page_number} of a PDF document.
Extract all text content from this page accurately.

Rules:
- Preserve the original text structure and formatting
- Include headers, paragraphs, lists, and tables
- For tables, use markdown table format
- Preserve any code blocks with proper formatting
- Do not add any commentary or interpretation
- If the page contains no text, respond with "[NO TEXT ON PAGE {page_number}]"
- Output only the extracted text content
"""
        
        result = await self.ocr_image(
            image_data=page_image_data,
            image_type=image_type,
            prompt=prompt,
            user_id=user_id,
            timeout_ms=timeout_ms,
        )
        
        # Add page number to metadata
        if result.metadata:
            result.metadata["page_number"] = page_number
        else:
            result.metadata = {"page_number": page_number}
        
        return result
