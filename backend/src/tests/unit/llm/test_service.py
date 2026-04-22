"""Unit tests for LLM model type filtering and configuration validation.

Tests cover:
- Model type validation in model configuration creation (rejects ocr/embedding)
- Model type filtering query construction

The LLMService.get_available_models() filtering is 3 lines of SQLAlchemy
(.where model_type.in_) and is tested via the API integration path. The
configuration validation is the critical behavioral test.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


def _make_model(model_name: str, model_type: str = "chat") -> MagicMock:
    """Create a mock LLMModel."""
    model = MagicMock()
    model.model_name = model_name
    model.model_type = model_type
    model.is_active = True
    model.provider = MagicMock()
    return model


class TestModelConfigurationTypeValidation:
    @pytest.mark.asyncio
    async def test_rejects_ocr_model(self):
        """Creating a model configuration with an OCR model should fail with INVALID_MODEL_TYPE."""
        from shu.core.exceptions import ShuException
        from shu.services.model_configuration_service import ModelConfigurationService

        db = AsyncMock(spec=AsyncSession)
        service = ModelConfigurationService(db)

        mock_provider_result = MagicMock()
        mock_provider_result.scalar_one_or_none.return_value = MagicMock(is_active=True)

        mock_model_result = MagicMock()
        mock_model_result.scalar_one_or_none.return_value = _make_model("mistral-ocr", "ocr")

        db.execute = AsyncMock(side_effect=[mock_provider_result, mock_model_result])

        config_data = MagicMock()
        config_data.llm_provider_id = "provider-1"
        config_data.model_name = "mistral-ocr"

        with pytest.raises(ShuException) as exc_info:
            await service.create_model_configuration(config_data, created_by="test-user")
        assert exc_info.value.error_code == "INVALID_MODEL_TYPE"

    @pytest.mark.asyncio
    async def test_rejects_embedding_model(self):
        """Creating a model configuration with an embedding model should fail with INVALID_MODEL_TYPE."""
        from shu.core.exceptions import ShuException
        from shu.services.model_configuration_service import ModelConfigurationService

        db = AsyncMock(spec=AsyncSession)
        service = ModelConfigurationService(db)

        mock_provider_result = MagicMock()
        mock_provider_result.scalar_one_or_none.return_value = MagicMock(is_active=True)

        mock_model_result = MagicMock()
        mock_model_result.scalar_one_or_none.return_value = _make_model("qwen-embed", "embedding")

        db.execute = AsyncMock(side_effect=[mock_provider_result, mock_model_result])

        config_data = MagicMock()
        config_data.llm_provider_id = "provider-1"
        config_data.model_name = "qwen-embed"

        with pytest.raises(ShuException) as exc_info:
            await service.create_model_configuration(config_data, created_by="test-user")
        assert exc_info.value.error_code == "INVALID_MODEL_TYPE"

    @pytest.mark.asyncio
    async def test_accepts_chat_model(self):
        """A chat model should pass the model type validation step (may fail later at prompt validation)."""
        from shu.core.exceptions import ShuException
        from shu.services.model_configuration_service import ModelConfigurationService

        db = AsyncMock(spec=AsyncSession)
        service = ModelConfigurationService(db)

        mock_provider_result = MagicMock()
        mock_provider_result.scalar_one_or_none.return_value = MagicMock(is_active=True)

        mock_model_result = MagicMock()
        mock_model_result.scalar_one_or_none.return_value = _make_model("gpt-4", "chat")

        mock_prompt_result = MagicMock()
        mock_prompt_result.scalar_one_or_none.return_value = None

        db.execute = AsyncMock(side_effect=[mock_provider_result, mock_model_result, mock_prompt_result])

        config_data = MagicMock()
        config_data.llm_provider_id = "provider-1"
        config_data.model_name = "gpt-4"
        config_data.name = "Test Config"
        config_data.description = None
        config_data.prompt_id = "some-prompt"
        config_data.parameter_overrides = None
        config_data.functionalities = None
        config_data.knowledge_base_ids = None
        config_data.kb_prompt_assignments = None

        try:
            await service.create_model_configuration(config_data, created_by="test-user")
        except ShuException as e:
            assert e.error_code != "INVALID_MODEL_TYPE", f"Chat model should pass type validation, got: {e.error_code}"


# LLMService.record_usage was removed in SHU-715; the two-tier cost contract
# and all its regression tests now live in
# backend/src/tests/unit/services/test_usage_recording.py::TestCostContract.


class TestSafeDecimal:
    """safe_decimal() coerces untrusted provider values into Decimal defensively."""

    def test_numeric_string_is_coerced(self):
        from decimal import Decimal

        from shu.core.safe_decimal import safe_decimal

        assert safe_decimal("0.042") == Decimal("0.042")
        assert safe_decimal(0.042) == Decimal(str(0.042))
        assert safe_decimal(42) == Decimal("42")

    def test_none_returns_zero(self):
        from decimal import Decimal

        from shu.core.safe_decimal import safe_decimal

        assert safe_decimal(None) == Decimal(0)

    def test_malformed_returns_zero_with_warning(self, caplog):
        import logging
        from decimal import Decimal

        from shu.core.safe_decimal import safe_decimal

        with caplog.at_level(logging.WARNING):
            result = safe_decimal("N/A")

        assert result == Decimal(0)
        assert any("Malformed" in rec.message for rec in caplog.records)
