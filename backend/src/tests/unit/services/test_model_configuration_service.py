"""Unit tests for ModelConfigurationService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.model_configuration_service import ModelConfigurationService


class TestGetModelConfigurationLogging:
    """Denied-config logging should avoid PII."""

    @pytest.mark.asyncio
    async def test_denied_config_warning_logs_user_id_not_email(self) -> None:
        db = AsyncMock()
        result = MagicMock()

        config = MagicMock()
        config.id = "cfg-1"
        config.knowledge_bases = [MagicMock(id="kb-1"), MagicMock(id="kb-2")]
        result.scalar_one_or_none.return_value = config
        db.execute.return_value = result

        current_user = MagicMock()
        current_user.id = "user-1"
        current_user.email = "pii@example.com"

        service = ModelConfigurationService(db)

        with patch("shu.services.model_configuration_service.KnowledgeBaseService") as mock_kb_service_class, \
             patch("shu.services.model_configuration_service.logger.warning") as warning_mock:
            mock_kb_service = MagicMock()
            mock_kb_service.filter_accessible_kb_ids = AsyncMock(return_value=["kb-1"])
            mock_kb_service_class.return_value = mock_kb_service

            result = await service.get_model_configuration("cfg-1", current_user=current_user)

        assert result is None
        warning_mock.assert_called_once()
        message = warning_mock.call_args.args[0]
        assert "user-1" in message
        assert "pii@example.com" not in message
