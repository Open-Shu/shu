"""
Unit tests for SideCallService.

Tests the side-call service functionality including model designation,
configuration management, and query proposal methods.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from shu.services.side_call_service import SideCallService, CallerResult, SIDE_CALL_MODEL_SETTING_KEY


class TestSideCallServiceInit:
    """Tests for SideCallService initialization."""

    def test_init_sets_attributes(self):
        """SideCallService should initialize with db and config_manager."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService'), \
             patch('shu.services.side_call_service.ModelConfigurationService'):
            service = SideCallService(mock_db, mock_config)
        
        assert service.db == mock_db
        assert service.config_manager == mock_config

    def test_setting_key_defined(self):
        """SideCallService should have correct SETTING_KEY."""
        assert SideCallService.SETTING_KEY == SIDE_CALL_MODEL_SETTING_KEY

    def test_request_type_defined(self):
        """SideCallService should have REQUEST_TYPE as side_call."""
        assert SideCallService.REQUEST_TYPE == "side_call"


class TestCallerResult:
    """Tests for CallerResult dataclass."""

    def test_success_result(self):
        """CallerResult should correctly store success result."""
        result = CallerResult(
            content="test response",
            success=True,
            tokens_used=100,
            response_time_ms=150,
        )
        
        assert result.content == "test response"
        assert result.success is True
        assert result.tokens_used == 100
        assert result.response_time_ms == 150
        assert result.error_message is None
        assert result.metadata == {}

    def test_error_result(self):
        """CallerResult should correctly store error result."""
        result = CallerResult(
            content="",
            success=False,
            error_message="Connection failed",
        )
        
        assert result.content == ""
        assert result.success is False
        assert result.error_message == "Connection failed"

    def test_with_metadata(self):
        """CallerResult should store metadata correctly."""
        result = CallerResult(
            content="response",
            metadata={"model_config_id": "abc123"},
        )
        
        assert result.metadata["model_config_id"] == "abc123"


class TestGetModel:
    """Tests for get_model method."""

    @pytest.fixture
    def service(self):
        """Create a SideCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.side_call_service.ModelConfigurationService') as MockModelConfig:
            svc = SideCallService(mock_db, mock_config)
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
    async def test_returns_none_when_setting_has_no_model_id(self, service):
        """get_model returns None when setting has no model_config_id."""
        mock_setting = MagicMock()
        mock_setting.value = {}
        service.system_settings_service.get_setting = AsyncMock(return_value=mock_setting)
        
        result = await service.get_model()
        
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_model_config_when_found(self, service):
        """get_model returns model config when properly configured."""
        mock_setting = MagicMock()
        mock_setting.value = {"model_config_id": "test-id-123"}
        service.system_settings_service.get_setting = AsyncMock(return_value=mock_setting)
        
        mock_model_config = MagicMock()
        mock_model_config.is_active = True
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model_config)
        
        result = await service.get_model()
        
        assert result == mock_model_config
        service.model_config_service.get_model_configuration.assert_awaited_once_with(
            "test-id-123", include_relationships=True
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_model_inactive(self, service):
        """get_model returns None when model is inactive."""
        mock_setting = MagicMock()
        mock_setting.value = {"model_config_id": "test-id-123"}
        service.system_settings_service.get_setting = AsyncMock(return_value=mock_setting)
        
        mock_model_config = MagicMock()
        mock_model_config.is_active = False
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model_config)
        
        result = await service.get_model()
        
        assert result is None


class TestSetModel:
    """Tests for set_model method."""

    @pytest.fixture
    def service(self):
        """Create a SideCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.side_call_service.ModelConfigurationService') as MockModelConfig:
            svc = SideCallService(mock_db, mock_config)
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
    async def test_returns_false_when_model_inactive(self, service):
        """set_model returns False when model is inactive."""
        mock_model = MagicMock()
        mock_model.is_active = False
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model)
        
        result = await service.set_model("inactive-model-id", "user-123")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_stores_setting_when_model_valid(self, service):
        """set_model stores system setting when model is valid."""
        mock_model = MagicMock()
        mock_model.is_active = True
        service.model_config_service.get_model_configuration = AsyncMock(return_value=mock_model)
        service.system_settings_service.upsert = AsyncMock()
        
        result = await service.set_model("valid-model-id", "user-123")
        
        assert result is True
        service.system_settings_service.upsert.assert_awaited_once()
        call_args = service.system_settings_service.upsert.call_args
        assert call_args[0][0] == SIDE_CALL_MODEL_SETTING_KEY
        assert call_args[0][1]["model_config_id"] == "valid-model-id"
        assert call_args[0][1]["updated_by"] == "user-123"


class TestClearModel:
    """Tests for clear_model method."""

    @pytest.fixture
    def service(self):
        """Create a SideCallService with mocked dependencies."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService') as MockSettings, \
             patch('shu.services.side_call_service.ModelConfigurationService'):
            svc = SideCallService(mock_db, mock_config)
            svc.system_settings_service = MockSettings.return_value
        return svc

    @pytest.mark.asyncio
    async def test_deletes_setting(self, service):
        """clear_model deletes the system setting."""
        service.system_settings_service.delete = AsyncMock()
        
        result = await service.clear_model("user-123")
        
        assert result is True
        service.system_settings_service.delete.assert_awaited_once_with(SIDE_CALL_MODEL_SETTING_KEY)


class TestProposeRagQuery:
    """Tests for propose_rag_query method."""

    @pytest.fixture
    def service(self):
        """Create a SideCallService with mocked call method."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService'), \
             patch('shu.services.side_call_service.ModelConfigurationService'):
            svc = SideCallService(mock_db, mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_calls_with_correct_structure(self, service):
        """propose_rag_query builds message sequence correctly."""
        service.call = AsyncMock(return_value=CallerResult(content="enhanced query"))
        
        result = await service.propose_rag_query("What is the API rate limit?")
        
        assert result.content == "enhanced query"
        service.call.assert_awaited_once()
        call_args = service.call.call_args
        assert "message_sequence" in call_args.kwargs
        assert "system_prompt" in call_args.kwargs
        assert "USER_MESSAGE" in call_args.kwargs["message_sequence"][0]["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_includes_prior_messages(self, service):
        """propose_rag_query includes prior messages in context."""
        service.call = AsyncMock(return_value=CallerResult(content="enhanced"))
        
        prior = [
            MagicMock(role="user", content="First message"),
            MagicMock(role="assistant", content="First response"),
        ]
        
        await service.propose_rag_query("Follow up question", prior_messages=prior)
        
        call_args = service.call.call_args
        message_history_text = call_args.kwargs["message_sequence"][0]["content"][1]["text"]
        assert "MESSAGE_HISTORY" in message_history_text


class TestDistillRagQuery:
    """Tests for distill_rag_query method."""

    @pytest.fixture
    def service(self):
        """Create a SideCallService with mocked call method."""
        mock_db = MagicMock()
        mock_config = MagicMock()
        
        with patch('shu.services.side_call_service.LLMService'), \
             patch('shu.services.side_call_service.SystemSettingsService'), \
             patch('shu.services.side_call_service.ModelConfigurationService'):
            svc = SideCallService(mock_db, mock_config)
        return svc

    @pytest.mark.asyncio
    async def test_calls_with_correct_structure(self, service):
        """distill_rag_query builds message sequence correctly."""
        service.call = AsyncMock(return_value=CallerResult(content="distilled terms"))
        
        result = await service.distill_rag_query("Please explain the API rate limits in detail")
        
        assert result.content == "distilled terms"
        service.call.assert_awaited_once()
        call_args = service.call.call_args
        assert "message_sequence" in call_args.kwargs
        assert "DISTILLED_QUERY" in call_args.kwargs["message_sequence"][0]["content"][1]["text"]
