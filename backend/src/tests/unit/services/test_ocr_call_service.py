"""
Unit tests for OcrCallService.

Tests the OCR call service functionality including model designation,
vision capability validation, and image OCR methods.
"""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shu.services.ocr_call_service import OcrCallService, CallerResult, OCR_CALL_MODEL_SETTING_KEY


class TestOcrCallServiceInit:
    """Tests for OcrCallService initialization."""

    def test_init_sets_attributes(self):
        """OcrCallService should initialize with db and config_manager."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService'), \
             patch('shu.services.ocr_call_service.ModelConfigurationService'):
            service = OcrCallService(mock_db, mock_config)
        
        assert service.db == mock_db
        assert service.config_manager == mock_config

    def test_setting_key_defined(self):
        """OcrCallService should have correct SETTING_KEY."""
        assert OcrCallService.SETTING_KEY == OCR_CALL_MODEL_SETTING_KEY

    def test_request_type_defined(self):
        """OcrCallService should have REQUEST_TYPE as ocr_call."""
        assert OcrCallService.REQUEST_TYPE == "ocr_call"


class TestGetModel:
    """Tests for get_model method."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.ocr_call_service.ModelConfigurationService') as MockModelConfig:
            svc = OcrCallService(mock_db, mock_config)
            svc.system_settings_service = MockSettings.return_value
            svc.model_config_service = MockModelConfig.return_value
        return svc

    @pytest.mark.asyncio
    async def test_returns_none_when_no_setting(self, service):
        """get_model returns None when no setting exists."""
        service.system_settings_service.get_setting = AsyncMock(return_value=None)
        
        result = await service.get_model()
        
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_model_config_when_found(self, service):
        """get_model returns model config when properly configured."""
        mock_setting = MagicMock()
        mock_setting.value = {"model_config_id": "ocr-model-123"}
        service.system_settings_service.get_setting = AsyncMock(return_value=mock_setting)
        
        mock_model_config = MagicMock()
        mock_model_config.is_active = True
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model_config)
        
        result = await service.get_model()
        
        assert result == mock_model_config


class TestSetModel:
    """Tests for set_model method."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.ocr_call_service.ModelConfigurationService') as MockModelConfig:
            svc = OcrCallService(mock_db, mock_config)
            svc.system_settings_service = MockSettings.return_value
            svc.model_config_service = MockModelConfig.return_value
        return svc

    @pytest.mark.asyncio
    async def test_returns_false_when_model_not_found(self, service):
        """set_model returns False when model config not found."""
        service.model_config_service.get_model_configuration = AsyncMock(return_value=None)
        
        result = await service.set_model("nonexistent-id", "user-123")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_stores_setting_when_model_valid(self, service):
        """set_model stores system setting when model is valid."""
        mock_model = MagicMock()
        mock_model.is_active = True
        mock_model.functionalities = {"vision": True}
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model)
        service.system_settings_service.upsert = AsyncMock()
        
        result = await service.set_model("vision-model-id", "user-123")
        
        assert result is True
        service.system_settings_service.upsert.assert_awaited_once()
        call_args = service.system_settings_service.upsert.call_args
        assert call_args[0][0] == OCR_CALL_MODEL_SETTING_KEY
        assert call_args[0][1]["model_config_id"] == "vision-model-id"


class TestValidateModelForDesignation:
    """Tests for _validate_model_for_designation method."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService'), \
             patch('shu.services.ocr_call_service.ModelConfigurationService'):
            svc = OcrCallService(mock_db, mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_returns_none_when_vision_enabled(self, service):
        """Validation passes when functionalities.vision is True."""
        mock_model = MagicMock()
        mock_model.functionalities = {"vision": True}
        
        result = await service._validate_model_for_designation(mock_model)
        
        assert result is None  # None means valid

    @pytest.mark.asyncio
    async def test_returns_none_when_provider_supports_vision(self, service):
        """Validation passes when provider supports vision."""
        mock_model = MagicMock()
        mock_model.functionalities = {}
        mock_model.llm_provider = MagicMock()
        mock_model.llm_provider.provider_capabilities = {"supports_vision": True}
        
        result = await service._validate_model_for_designation(mock_model)
        
        assert result is None  # None means valid

    @pytest.mark.asyncio
    async def test_returns_error_when_no_vision_support(self, service):
        """Validation fails when model has no vision support."""
        mock_model = MagicMock()
        mock_model.id = "test-model-id"
        mock_model.functionalities = {}
        mock_model.llm_provider = MagicMock()
        mock_model.llm_provider.provider_capabilities = {}
        
        result = await service._validate_model_for_designation(mock_model)
        
        assert result is not None
        assert "vision" in result.lower()


class TestClearModel:
    """Tests for clear_model method."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.ocr_call_service.ModelConfigurationService'):
            svc = OcrCallService(mock_db, mock_config)
            svc.system_settings_service = MockSettings.return_value
        return svc

    @pytest.mark.asyncio
    async def test_deletes_setting(self, service):
        """clear_model deletes the system setting."""
        service.system_settings_service.delete = AsyncMock()
        
        result = await service.clear_model("user-123")
        
        assert result is True
        service.system_settings_service.delete.assert_awaited_once_with(OCR_CALL_MODEL_SETTING_KEY)


class TestOcrImage:
    """Tests for ocr_image and ocr_image_base64 methods."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked _call method."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService'), \
             patch('shu.services.ocr_call_service.ModelConfigurationService'):
            svc = OcrCallService(mock_db, mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_ocr_image_encodes_to_base64(self, service):
        """ocr_image should encode image data to base64 before calling."""
        service._call = AsyncMock(return_value=CallerResult(content="extracted text"))
        
        test_image_data = b"fake image bytes"
        result = await service.ocr_image(test_image_data)
        
        assert result.content == "extracted text"
        service._call.assert_awaited_once()
        
        # Verify the message contains base64-encoded image
        call_args = service._call.call_args
        message_seq = call_args.kwargs["message_sequence"]
        image_content = message_seq[0]["content"][0]
        assert image_content["type"] == "image_url"
        expected_b64 = base64.b64encode(test_image_data).decode("utf-8")
        assert expected_b64 in image_content["image_url"]["url"]

    @pytest.mark.asyncio
    async def test_ocr_image_base64_builds_vision_message(self, service):
        """ocr_image_base64 should build correct vision message structure."""
        service._call = AsyncMock(return_value=CallerResult(content="text from image"))
        
        result = await service.ocr_image_base64(
            image_base64="dGVzdA==",  # "test" in base64
            image_type="image/png",
        )
        
        assert result.content == "text from image"
        call_args = service._call.call_args
        message_seq = call_args.kwargs["message_sequence"]
        
        # Verify structure
        assert len(message_seq) == 1
        assert message_seq[0]["role"] == "user"
        assert len(message_seq[0]["content"]) == 2
        assert message_seq[0]["content"][0]["type"] == "image_url"
        assert message_seq[0]["content"][1]["type"] == "text"


class TestOcrPdfPage:
    """Tests for ocr_pdf_page method."""

    @pytest.fixture
    def service(self):
        """Create an OcrCallService with mocked ocr_image method."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.ocr_call_service.LLMService'), \
             patch('shu.services.ocr_call_service.SystemSettingsService'), \
             patch('shu.services.ocr_call_service.ModelConfigurationService'):
            svc = OcrCallService(mock_db, mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_includes_page_number_in_prompt(self, service):
        """ocr_pdf_page should include page number in the prompt."""
        service.ocr_image = AsyncMock(return_value=CallerResult(content="page text", metadata={}))
        
        result = await service.ocr_pdf_page(
            page_image_data=b"page image",
            page_number=5,
        )
        
        assert result.content == "page text"
        call_args = service.ocr_image.call_args
        prompt = call_args.kwargs["prompt"]
        assert "page 5" in prompt.lower()

    @pytest.mark.asyncio
    async def test_adds_page_number_to_metadata(self, service):
        """ocr_pdf_page should add page_number to result metadata."""
        service.ocr_image = AsyncMock(return_value=CallerResult(content="text", metadata={}))
        
        result = await service.ocr_pdf_page(
            page_image_data=b"page image",
            page_number=3,
        )
        
        assert result.metadata["page_number"] == 3
