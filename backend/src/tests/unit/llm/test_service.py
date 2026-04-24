"""Unit tests for LLM model type filtering and configuration validation.

Tests cover:
- Model type validation in model configuration creation (rejects ocr/embedding)
- Model type filtering query construction
- Managed-provider lockdown guards on create/update/delete

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


class TestRecordUsage:
    """Cover the two-tier cost-resolution contract of LLMService.record_usage.

    SHU-700: provider-reported cost is authoritative when non-zero; DB-rate math
    is the fallback when the caller passes Decimal(0). user_id is always threaded
    through to the written llm_usage row.
    """

    @staticmethod
    def _make_service_with_model(model):
        from shu.llm.service import LLMService

        db = AsyncMock(spec=AsyncSession)
        db.get = AsyncMock(return_value=model)
        db.add = MagicMock()
        db.commit = AsyncMock()
        service = LLMService(db)
        return db, service

    @staticmethod
    def _model_with_rates(input_rate: str, output_rate: str) -> MagicMock:
        from decimal import Decimal

        m = MagicMock()
        m.cost_per_input_unit = Decimal(input_rate)
        m.cost_per_output_unit = Decimal(output_rate)
        return m

    @pytest.mark.asyncio
    async def test_provider_reported_cost_recorded_verbatim(self):
        """Non-zero caller-supplied total_cost is authoritative; input/output = 0."""
        from decimal import Decimal
        db, service = self._make_service_with_model(
            self._model_with_rates("0.00001", "0.00003")  # DB rates present but should be IGNORED
        )

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=1000,
            output_tokens=500,
            total_cost=Decimal("0.042"),  # provider-reported wire value
            user_id="user-1",
        )

        db.add.assert_called_once()
        usage = db.add.call_args[0][0]
        assert usage.total_cost == Decimal("0.042")
        assert usage.input_cost == Decimal("0")
        assert usage.output_cost == Decimal("0")
        assert usage.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_db_rate_fallback_when_total_cost_is_zero(self):
        """total_cost=0 triggers DB-rate math; summation invariant holds."""
        from decimal import Decimal
        db, service = self._make_service_with_model(
            self._model_with_rates("0.00001", "0.00003")
        )

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=1000,
            output_tokens=500,
            total_cost=Decimal("0"),  # sentinel for "caller has no provider cost"
            user_id="user-2",
        )

        usage = db.add.call_args[0][0]
        assert usage.input_cost == Decimal("0.01")   # 1000 * 0.00001
        assert usage.output_cost == Decimal("0.015") # 500 * 0.00003
        assert usage.total_cost == Decimal("0.025")
        assert usage.input_cost + usage.output_cost == usage.total_cost
        assert usage.user_id == "user-2"

    @pytest.mark.asyncio
    async def test_no_rates_no_provider_cost_records_all_zero(self):
        """Local/self-hosted models with NULL rates and no wire cost record all zeros."""
        from decimal import Decimal
        local_model = MagicMock()
        local_model.cost_per_input_unit = None
        local_model.cost_per_output_unit = None
        db, service = self._make_service_with_model(local_model)

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=1000,
            output_tokens=500,
            total_cost=Decimal("0"),
            user_id="user-3",
        )

        usage = db.add.call_args[0][0]
        assert usage.input_cost == Decimal("0")
        assert usage.output_cost == Decimal("0")
        assert usage.total_cost == Decimal("0")
        assert usage.user_id == "user-3"

    @pytest.mark.asyncio
    async def test_user_id_nullable_on_write(self):
        """user_id=None is valid; the row still writes cleanly."""
        from decimal import Decimal
        db, service = self._make_service_with_model(
            self._model_with_rates("0.00001", "0.00003")
        )

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=10,
            output_tokens=5,
            total_cost=Decimal("0"),
            # user_id omitted
        )

        usage = db.add.call_args[0][0]
        assert usage.user_id is None

    @pytest.mark.asyncio
    async def test_one_sided_rate_still_computes_input_cost(self):
        """Regression: Decimal(0) output rate must not collapse the DB-rate fallback.

        An earlier guard used truthiness (`model.cost_per_input_unit and
        model.cost_per_output_unit`), which treated a legitimate Decimal(0)
        output rate as "no pricing" and silently recorded $0 for a chat row
        with real input-token cost. Using `is not None` preserves each side
        independently.
        """
        from decimal import Decimal
        db, service = self._make_service_with_model(
            self._model_with_rates("0.00001", "0")  # free output, billed input
        )

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=1000,
            output_tokens=500,
            total_cost=Decimal("0"),
        )

        usage = db.add.call_args[0][0]
        assert usage.input_cost == Decimal("0.01")  # 1000 * 0.00001
        assert usage.output_cost == Decimal("0")   # 500 * 0
        assert usage.total_cost == Decimal("0.01")
        assert usage.input_cost + usage.output_cost == usage.total_cost

    @pytest.mark.asyncio
    async def test_only_input_rate_set_still_computes(self):
        """Only cost_per_input_unit set (output rate NULL) falls back to Decimal(0) for output."""
        from decimal import Decimal
        model = MagicMock()
        model.cost_per_input_unit = Decimal("0.00002")
        model.cost_per_output_unit = None
        db, service = self._make_service_with_model(model)

        await service.record_usage(
            provider_id="p1",
            model_id="m1",
            request_type="chat",
            input_tokens=500,
            output_tokens=100,
            total_cost=Decimal("0"),
        )

        usage = db.add.call_args[0][0]
        assert usage.input_cost == Decimal("0.01")
        assert usage.output_cost == Decimal("0")
        assert usage.total_cost == Decimal("0.01")


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


class TestManagedProviderLockdown:
    """Service-level guards for managed-provider lockdown (SHU-705).

    Each test exercises exactly ONE guard path. The positive-path tests assert the
    guard does NOT fire by letting the method progress to a later, unrelated failure
    (same pattern used by ``test_accepts_chat_model``). This keeps the tests tight
    without having to stand up the full downstream commit/adapter stack.
    """

    @staticmethod
    def _build_service(lock_provider_creations: bool = False):
        from shu.llm.service import LLMService

        db = AsyncMock(spec=AsyncSession)
        # Bypass __init__ to avoid requiring a real llm_encryption_key / settings wiring.
        service = LLMService.__new__(LLMService)
        service.db = db
        service.settings = MagicMock()
        service.settings.lock_provider_creations = lock_provider_creations
        service.encryption_key = "test-key"
        service.provider_type = MagicMock()
        service.provider_type.get = AsyncMock(return_value=None)
        return service

    @pytest.mark.asyncio
    async def test_create_provider_raises_when_creation_locked(self):
        from shu.core.exceptions import ProviderCreationDisabledError

        service = self._build_service(lock_provider_creations=True)

        with pytest.raises(ProviderCreationDisabledError) as exc_info:
            await service.create_provider(
                name="Test", provider_type="openai", api_endpoint="https://x"
            )
        assert str(exc_info.value) == "Provider creation is disabled on this deployment."

    @pytest.mark.asyncio
    async def test_create_provider_proceeds_past_guard_when_unlocked(self):
        """Guard does not fire; method progresses to the next validation step."""
        from shu.core.exceptions import LLMProviderError

        service = self._build_service(lock_provider_creations=False)

        # provider_type.get returns None, so the next step raises LLMProviderError.
        # Reaching that error proves the creation guard did NOT fire.
        with pytest.raises(LLMProviderError):
            await service.create_provider(
                name="Test", provider_type="openai", api_endpoint="https://x"
            )

    @pytest.mark.asyncio
    async def test_update_provider_raises_when_target_is_system_managed(self):
        from shu.core.exceptions import ProviderLockedError

        service = self._build_service()
        managed_provider = MagicMock()
        managed_provider.is_system_managed = True
        service.get_provider_by_id = AsyncMock(return_value=managed_provider)

        with pytest.raises(ProviderLockedError) as exc_info:
            await service.update_provider("provider-1", name="new")
        assert str(exc_info.value) == "Provider is managed by Shu and cannot be modified."

    @pytest.mark.asyncio
    async def test_update_provider_proceeds_past_guard_when_unmanaged(self):
        """Guard does not fire; method progresses past the lockdown check."""
        from shu.core.exceptions import ProviderLockedError

        service = self._build_service()
        unmanaged_provider = MagicMock()
        unmanaged_provider.is_system_managed = False
        unmanaged_provider.provider_type = "openai"
        unmanaged_provider.api_endpoint = "https://x"
        service.get_provider_by_id = AsyncMock(return_value=unmanaged_provider)

        # provider_type.get returns None, so adapter resolution fails downstream.
        # Any exception that is NOT ProviderLockedError proves the guard did NOT fire.
        with pytest.raises(Exception) as exc_info:
            await service.update_provider("provider-1", name="new")
        assert not isinstance(exc_info.value, ProviderLockedError)

    @pytest.mark.asyncio
    async def test_delete_provider_raises_when_target_is_system_managed(self):
        from shu.core.exceptions import ProviderLockedError

        service = self._build_service()
        managed_provider = MagicMock()
        managed_provider.is_system_managed = True
        service.get_provider_by_id = AsyncMock(return_value=managed_provider)

        with pytest.raises(ProviderLockedError) as exc_info:
            await service.delete_provider("provider-1")
        assert str(exc_info.value) == "Provider is managed by Shu and cannot be modified."

    @pytest.mark.asyncio
    async def test_delete_provider_returns_true_when_unmanaged(self):
        service = self._build_service()
        unmanaged_provider = MagicMock()
        unmanaged_provider.is_system_managed = False
        unmanaged_provider.name = "Test"
        service.get_provider_by_id = AsyncMock(return_value=unmanaged_provider)
        service.db.delete = AsyncMock()
        service.db.commit = AsyncMock()

        result = await service.delete_provider("provider-1")

        assert result is True
        service.db.delete.assert_awaited_once_with(unmanaged_provider)

    @pytest.mark.asyncio
    async def test_delete_provider_model_raises_when_parent_is_system_managed(self):
        from shu.core.exceptions import ModelLockedError

        service = self._build_service()
        model = MagicMock()
        model.provider_id = "provider-1"
        model.provider = MagicMock()
        model.provider.is_system_managed = True
        service.get_model_by_id = AsyncMock(return_value=model)

        with pytest.raises(ModelLockedError) as exc_info:
            await service.delete_provider_model("provider-1", "model-1")
        assert str(exc_info.value) == "Model is managed by Shu and cannot be modified."

    @pytest.mark.asyncio
    async def test_delete_provider_model_soft_deletes_when_parent_unmanaged(self):
        service = self._build_service()
        model = MagicMock()
        model.provider_id = "provider-1"
        model.model_name = "gpt-4"
        model.is_active = True
        model.provider = MagicMock()
        model.provider.is_system_managed = False
        service.get_model_by_id = AsyncMock(return_value=model)
        service.db.commit = AsyncMock()

        result = await service.delete_provider_model("provider-1", "model-1")

        assert result is model
        assert model.is_active is False
        service.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_provider_models_is_add_only_on_system_managed_parent(self):
        """Design decision #4: sync is allowed on system-managed providers BECAUSE
        the contract is strictly add-only. If sync ever started mutating or
        deactivating existing rows, admins could use it as a back-door to edit
        Shu-managed models. Seed an existing row, discover the same name plus
        a new one, and assert: (a) the existing row is unchanged and still
        active, (b) only the new name is persisted via db.add.
        """
        service = self._build_service()
        provider = MagicMock()
        provider.id = "provider-sys"
        provider.name = "Shu OpenAI"
        provider.is_system_managed = True

        existing = MagicMock()
        existing.id = "existing-model-id"
        existing.model_name = "gpt-4"
        existing.is_active = True
        existing.display_name = "GPT-4"

        service.get_provider_by_id = AsyncMock(return_value=provider)
        service.discover_provider_models = AsyncMock(
            return_value=[{"id": "gpt-4"}, {"id": "gpt-5"}]
        )
        service.get_available_models = AsyncMock(return_value=[existing])
        service.db.add = MagicMock()
        service.db.commit = AsyncMock()
        service.db.refresh = AsyncMock()

        created = await service.sync_provider_models("provider-sys")

        # Only the new model gets persisted; the existing row is never touched.
        assert [m.model_name for m in created] == ["gpt-5"]
        assert service.db.add.call_count == 1
        added_model = service.db.add.call_args.args[0]
        assert added_model.model_name == "gpt-5"

        # Existing row is unchanged — same id, still active, fields not mutated.
        assert existing.id == "existing-model-id"
        assert existing.is_active is True
        assert existing.display_name == "GPT-4"
