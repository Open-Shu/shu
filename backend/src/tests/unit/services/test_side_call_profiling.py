"""
Unit tests for SideCallService profiling model selection (SHU-590).

Tests the dedicated profiling model configuration that allows different
model selection for profiling vs interactive side-calls.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.services.side_call_service import (
    PROFILING_MODEL_SETTING_KEY,
    SIDE_CALL_MODEL_SETTING_KEY,
    SideCallService,
)


@pytest.fixture
def mock_db():
    """Mock async database session."""
    return AsyncMock()


@pytest.fixture
def mock_config_manager():
    """Mock ConfigurationManager."""
    return MagicMock()


@pytest.fixture
def side_call_service(mock_db, mock_config_manager):
    """Create SideCallService with mocked dependencies."""
    service = SideCallService(mock_db, mock_config_manager)
    service.system_settings_service = AsyncMock()
    service.model_config_service = AsyncMock()
    service.llm_service = AsyncMock()
    return service


class TestProfilingModelSelection:
    """Tests for get_profiling_model method."""

    @pytest.mark.asyncio
    async def test_returns_profiling_model_when_configured(self, side_call_service):
        """Test that dedicated profiling model is returned when set."""
        profiling_model = MagicMock()
        profiling_model.is_active = True

        # Configure profiling model setting
        side_call_service.system_settings_service.get_setting.return_value = MagicMock(
            value={"model_config_id": "profiling-model-123"}
        )
        side_call_service.model_config_service.get_model_configuration.return_value = profiling_model

        result = await side_call_service.get_profiling_model()

        assert result == profiling_model
        side_call_service.system_settings_service.get_setting.assert_called_once_with(
            PROFILING_MODEL_SETTING_KEY
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_side_call_model(self, side_call_service):
        """Test fallback to side-call model when no profiling model configured."""
        side_call_model = MagicMock()
        side_call_model.is_active = True

        # No profiling model configured
        side_call_service.system_settings_service.get_setting.side_effect = [
            None,  # PROFILING_MODEL_SETTING_KEY returns None
            MagicMock(value={"model_config_id": "side-call-model-456"}),  # SIDE_CALL_MODEL_SETTING_KEY
        ]
        side_call_service.model_config_service.get_model_configuration.return_value = side_call_model

        result = await side_call_service.get_profiling_model()

        assert result == side_call_model
        # Should have called get_setting twice - once for profiling, once for side-call
        assert side_call_service.system_settings_service.get_setting.call_count == 2

    @pytest.mark.asyncio
    async def test_falls_back_when_profiling_model_inactive(self, side_call_service):
        """Test fallback when profiling model exists but is inactive."""
        inactive_profiling_model = MagicMock()
        inactive_profiling_model.is_active = False

        side_call_model = MagicMock()
        side_call_model.is_active = True

        # Profiling model exists but inactive
        side_call_service.system_settings_service.get_setting.side_effect = [
            MagicMock(value={"model_config_id": "profiling-model-123"}),
            MagicMock(value={"model_config_id": "side-call-model-456"}),
        ]
        side_call_service.model_config_service.get_model_configuration.side_effect = [
            inactive_profiling_model,
            side_call_model,
        ]

        result = await side_call_service.get_profiling_model()

        assert result == side_call_model

    @pytest.mark.asyncio
    async def test_returns_none_when_no_models_configured(self, side_call_service):
        """Test returns None when neither profiling nor side-call model configured."""
        side_call_service.system_settings_service.get_setting.return_value = None

        result = await side_call_service.get_profiling_model()

        assert result is None


class TestSetProfilingModel:
    """Tests for set_profiling_model method."""

    @pytest.mark.asyncio
    async def test_sets_profiling_model_successfully(self, side_call_service):
        """Test setting a valid profiling model."""
        model_config = MagicMock()
        model_config.is_active = True

        side_call_service.model_config_service.get_model_configuration.return_value = model_config

        result = await side_call_service.set_profiling_model("model-123", "user-456")

        assert result is True
        side_call_service.system_settings_service.upsert.assert_called_once()
        call_args = side_call_service.system_settings_service.upsert.call_args
        assert call_args[0][0] == PROFILING_MODEL_SETTING_KEY
        assert call_args[0][1]["model_config_id"] == "model-123"

    @pytest.mark.asyncio
    async def test_rejects_inactive_model(self, side_call_service):
        """Test that inactive model is rejected."""
        inactive_model = MagicMock()
        inactive_model.is_active = False

        side_call_service.model_config_service.get_model_configuration.return_value = inactive_model

        result = await side_call_service.set_profiling_model("model-123", "user-456")

        assert result is False
        side_call_service.system_settings_service.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_model(self, side_call_service):
        """Test that nonexistent model is rejected."""
        side_call_service.model_config_service.get_model_configuration.return_value = None

        result = await side_call_service.set_profiling_model("nonexistent", "user-456")

        assert result is False


class TestClearProfilingModel:
    """Tests for clear_profiling_model method."""

    @pytest.mark.asyncio
    async def test_clears_profiling_model(self, side_call_service):
        """Test clearing the profiling model."""
        result = await side_call_service.clear_profiling_model("user-456")

        assert result is True
        side_call_service.system_settings_service.delete.assert_called_once_with(
            PROFILING_MODEL_SETTING_KEY
        )
