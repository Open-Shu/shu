"""LLM service layer for Shu RAG Backend.

This module provides the service layer for managing LLM providers,
models, and handling LLM operations with database integration.
"""

import logging
from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shu.schemas.llm_provider_type import ProviderTypeDefinitionSchema
from shu.services.providers.adapter_base import (
    ProviderAdapterContext,
    get_adapter,
)

from ..core.config import get_settings_instance
from ..core.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    ModelLockedError,
    ProviderCreationDisabledError,
    ProviderLockedError,
)
from ..models.llm_provider import LLMModel, LLMProvider, LLMUsage, ModelType
from ..services.provider_type_definition_service import ProviderTypeDefinitionsService
from .client import UnifiedLLMClient

logger = logging.getLogger(__name__)


class LLMService:
    """Service for managing LLM providers and operations."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session
        self.settings = get_settings_instance()
        self.encryption_key = self.settings.llm_encryption_key
        self.provider_type = ProviderTypeDefinitionsService(db_session)

        if not self.encryption_key:
            raise LLMConfigurationError("LLM encryption key not configured")

    async def get_active_providers(self) -> list[LLMProvider]:
        """Get all active LLM providers."""
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_active)
            .options(selectinload(LLMProvider.models), selectinload(LLMProvider.provider_definition))
            .order_by(LLMProvider.name)
        )

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_provider_by_id(self, provider_id: str) -> LLMProvider | None:
        """Get LLM provider by ID."""
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.id == provider_id)
            .options(selectinload(LLMProvider.models), selectinload(LLMProvider.provider_definition))
        )

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_provider_by_name(self, name: str) -> LLMProvider | None:
        """Get LLM provider by name."""
        stmt = select(LLMProvider).where(LLMProvider.name == name).options(selectinload(LLMProvider.models))

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_provider(
        self,
        name: str,
        provider_type: str,
        api_endpoint: str,
        api_key: str | None = None,
        organization_id: str | None = None,
        **kwargs: Any,
    ) -> LLMProvider:
        """Create a new LLM provider."""
        if self.settings.lock_provider_creations:
            raise ProviderCreationDisabledError("Provider creation is disabled on this deployment.")

        provider_type_definition = await self.provider_type.get(provider_type)
        if not provider_type_definition:
            raise LLMProviderError(f"Provider type '{provider_type}' is invalid")

        # Check if provider with same name already exists
        existing = await self.get_provider_by_name(name)
        if existing:
            raise LLMProviderError(f"Provider with name '{name}' already exists")

        provider_type_record = await self.provider_type.get(provider_type)

        adapter = get_adapter(provider_type_record.provider_adapter_name, ProviderAdapterContext(db_session=self.db))
        provider_settings = adapter.normalize_request_dict(api_endpoint, kwargs)
        config = ProviderTypeDefinitionSchema.to_config_settings(
            provider_settings,
            default_capabilities=adapter.get_capabilities(),
            **kwargs,
        )

        # Encrypt API key if provided
        api_key_encrypted = None
        if api_key:
            api_key_encrypted = self._encrypt_api_key(api_key)

        provider = LLMProvider(
            name=name,
            provider_type=provider_type,
            api_key_encrypted=api_key_encrypted,
            organization_id=organization_id,
            config=config,
        )

        self.db.add(provider)
        await self.db.commit()

        # explicitely refresh the object to include dependencies
        provider = await self.get_provider_by_id(provider.id)

        logger.info(f"Created LLM provider: {name} ({provider_type})")
        return provider

    async def update_provider(self, provider_id: str, **updates: Any) -> LLMProvider:
        """Update an existing LLM provider."""
        provider = await self.get_provider_by_id(provider_id)
        if not provider:
            raise LLMProviderError(f"Provider with ID '{provider_id}' not found")

        if provider.is_system_managed:
            raise ProviderLockedError("Provider is managed by Shu and cannot be modified.")

        provider_type = updates.get("provider_type", provider.provider_type)
        api_endpoint = updates.pop("api_endpoint", None) or provider.api_endpoint
        provider_type_record = await self.provider_type.get(provider_type)

        adapter = get_adapter(provider_type_record.provider_adapter_name, ProviderAdapterContext(db_session=self.db))
        provider_settings = adapter.normalize_request_dict(api_endpoint, updates)
        config = ProviderTypeDefinitionSchema.to_config_settings(
            provider_settings,
            default_capabilities=adapter.get_capabilities(),
            **updates,
        )

        # Handle API key encryption if being updated
        if "api_key" in updates:
            api_key = updates.pop("api_key")
            if api_key:
                updates["api_key_encrypted"] = self._encrypt_api_key(api_key)
            else:
                updates["api_key_encrypted"] = None

        # Update provider attributes
        for key, value in updates.items():
            if hasattr(provider, key):
                setattr(provider, key, value)

        existing_config = provider.config if isinstance(provider.config, dict) else {}
        merged_config = {**existing_config, **(config or {})}
        provider.config = merged_config

        await self.db.commit()

        # explicitely refresh the object to include dependencies
        provider = await self.get_provider_by_id(provider.id)

        logger.info(f"Updated LLM provider: {provider.name}")
        return provider

    async def delete_provider(self, provider_id: str) -> bool:
        """Delete an LLM provider."""
        provider = await self.get_provider_by_id(provider_id)
        if not provider:
            return False

        if provider.is_system_managed:
            raise ProviderLockedError("Provider is managed by Shu and cannot be modified.")

        await self.db.delete(provider)
        await self.db.commit()

        logger.info(f"Deleted LLM provider: {provider.name}")
        return True

    async def get_available_models(
        self,
        provider_id: str | None = None,
        model_types: list[str] | None = None,
        *,
        active_providers_only: bool = False,
    ) -> list[LLMModel]:
        """Get available LLM models, optionally filtered by provider and type.

        Args:
            provider_id: Filter to models from this provider.
            model_types: Filter to these model types (e.g. ["chat"]).
                Pass None to return all types (for admin model management).
                Defaults to None (no type filter) for backward compatibility;
                callers that serve chat contexts should pass ["chat"].
            active_providers_only: When True, exclude models whose parent provider
                is deactivated. End-user pickers must pass True; admin flows leave
                it False so inactive providers remain visible for management.

        """
        stmt = select(LLMModel).where(LLMModel.is_active)

        if active_providers_only:
            stmt = stmt.join(LLMProvider, LLMModel.provider_id == LLMProvider.id).where(LLMProvider.is_active)

        if provider_id:
            stmt = stmt.where(LLMModel.provider_id == provider_id)

        if model_types is not None:
            stmt = stmt.where(LLMModel.model_type.in_(model_types))

        stmt = stmt.options(selectinload(LLMModel.provider))

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_model_by_name(self, model_name: str, provider_id: str | None = None) -> LLMModel | None:
        """Get LLM model by name, optionally filtered by provider."""
        stmt = select(LLMModel).where(and_(LLMModel.model_name == model_name, LLMModel.is_active))

        if provider_id:
            stmt = stmt.where(LLMModel.provider_id == provider_id)

        stmt = stmt.options(selectinload(LLMModel.provider))

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_model(
        self, provider_id: str, model_name: str, display_name: str | None = None, **kwargs: Any
    ) -> LLMModel:
        """Create a new LLM model configuration."""
        provider = await self.get_provider_by_id(provider_id)
        if not provider:
            raise LLMProviderError(f"Provider with ID '{provider_id}' not found")

        model = LLMModel(
            provider_id=provider_id,
            model_name=model_name,
            display_name=display_name or model_name,
            **kwargs,
        )

        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)

        logger.info(f"Created LLM model: {model_name} for provider {provider.name}")
        return model

    async def delete_provider_model(self, provider_id: str, model_id: str) -> LLMModel | None:
        """Soft-delete a model, enforcing lockdown on a system-managed parent provider.

        Returns the soft-deleted model, or ``None`` if the model is not found or
        does not belong to ``provider_id``. Raises :class:`ModelLockedError` when
        the parent provider is system-managed.
        """
        model = await self.get_model_by_id(model_id)
        if not model or model.provider_id != provider_id:
            return None

        if model.provider.is_system_managed:
            raise ModelLockedError("Model is managed by Shu and cannot be modified.")

        model.is_active = False
        await self.db.commit()

        logger.info(f"Disabled LLM model: {model.model_name} for provider {provider_id}")
        return model

    async def get_model_by_id(self, model_id: str) -> LLMModel | None:
        """Get LLM model by ID."""
        stmt = select(LLMModel).where(LLMModel.id == model_id).options(selectinload(LLMModel.provider))

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_client(
        self,
        provider_id: str,
        conversation_owner_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> UnifiedLLMClient:
        """Get LLM client for a specific provider.

        Args:
            provider_id: ID of the provider to create a client for.
            conversation_owner_id: Optional user ID of the conversation owner,
                forwarded to the adapter for per-user plugin authorisation.
            knowledge_base_ids: Optional list of knowledge base IDs to scope
                plugin tool calls to this conversation's knowledge bases.

        Returns:
            Configured ``UnifiedLLMClient`` ready to make chat completion calls.

        Raises:
            LLMProviderError: If the provider is not found or not active.

        """
        provider = await self.get_provider_by_id(provider_id)
        if not provider:
            raise LLMProviderError(f"Provider with ID '{provider_id}' not found")

        if not provider.is_active:
            raise LLMProviderError(f"Provider '{provider.name}' is not active")

        return UnifiedLLMClient(self.db, provider, conversation_owner_id, knowledge_base_ids=knowledge_base_ids)

    async def test_provider_connection(self, provider_id: str) -> bool:
        """Test connection to an LLM provider."""
        try:
            client = await self.get_client(provider_id)
            result = await client.validate_connection()
            await client.close()
            return result
        except Exception as e:
            logger.error(f"Provider connection test failed: {e}")
            return False

    async def discover_provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        """Discover available models from a provider's API.

        Args:
            provider_id: ID of the provider to query

        Returns:
            List of model dictionaries with model information

        """
        try:
            client = await self.get_client(provider_id)
            models = await client.discover_available_models()
            await client.close()
            return models
        except LLMProviderError:
            # Preserve structured provider errors (status_code, details)
            raise
        except Exception as e:
            logger.error(f"Model discovery failed for provider {provider_id}: {e}")
            raise LLMProviderError(f"Failed to discover models: {e!s}")

    async def sync_provider_models(self, provider_id: str, selected_models: list[str] | None = None) -> list[LLMModel]:
        """Sync discovered models with database, enabling only selected models.

        Args:
            provider_id: ID of the provider
            selected_models: List of model IDs to enable (None = enable all discovered)

        Returns:
            List of created/updated LLMModel objects

        """
        try:
            # Get provider
            provider = await self.get_provider_by_id(provider_id)
            if not provider:
                raise LLMProviderError(f"Provider {provider_id} not found")

            # Discover available models
            discovered_models = await self.discover_provider_models(provider_id)

            # Get existing models for this provider
            existing_models = await self.get_available_models(provider_id)
            existing_model_names = {model.model_name for model in existing_models}

            created_models = []

            for model_info in discovered_models:
                model_name = model_info.get("id", "")
                if not model_name:
                    continue

                # Skip if model already exists
                if model_name in existing_model_names:
                    continue

                # Only create if selected (or if no selection provided, create all)
                if selected_models is None or model_name in selected_models:
                    # Create model with discovered information; token-related limits must be set via model configurations
                    model = LLMModel(
                        provider_id=provider_id,
                        model_name=model_name,
                        display_name=self._generate_display_name(model_name),
                        model_type=ModelType.CHAT,
                        supports_streaming=True,  # Most modern models support streaming
                        supports_functions=self._supports_functions(model_name),
                        supports_vision=self._supports_vision(model_name),
                        is_active=True,
                    )

                    self.db.add(model)
                    created_models.append(model)

            if created_models:
                await self.db.commit()
                for model in created_models:
                    await self.db.refresh(model)

                logger.info(f"Created {len(created_models)} models for provider {provider.name}")

            return created_models

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to sync models for provider {provider_id}: {e}")
            raise LLMProviderError(f"Model sync failed: {e!s}")

    def _generate_display_name(self, model_name: str) -> str:
        """Generate a user-friendly display name for a model."""
        # Common model name mappings
        display_names = {
            "gpt-4": "GPT-4",
            "gpt-4-turbo": "GPT-4 Turbo",
            "gpt-4-turbo-preview": "GPT-4 Turbo Preview",
            "gpt-3.5-turbo": "GPT-3.5 Turbo",
            "gpt-3.5-turbo-16k": "GPT-3.5 Turbo 16K",
            "claude-3-opus-20240229": "Claude 3 Opus",
            "claude-3-sonnet-20240229": "Claude 3 Sonnet",
            "claude-3-haiku-20240307": "Claude 3 Haiku",
        }

        return display_names.get(model_name, model_name.replace("-", " ").title())

    def _supports_functions(self, model_name: str) -> bool:
        """Determine if model supports function calling."""
        function_models = ["gpt-4", "gpt-3.5-turbo"]
        return any(model in model_name.lower() for model in function_models)

    def _supports_vision(self, model_name: str) -> bool:
        """Determine if model supports vision/image inputs."""
        vision_models = ["gpt-4-vision", "gpt-4-turbo", "claude-3"]
        return any(model in model_name.lower() for model in vision_models)

    async def record_usage(
        self,
        provider_id: str,
        model_id: str,
        request_type: str,
        input_tokens: int,
        output_tokens: int,
        total_cost: Decimal,
        user_id: str | None = None,
        response_time_ms: int | None = None,
        success: bool = True,
        error_message: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> LLMUsage:
        """Record LLM usage for analytics and cost tracking.

        Cost resolution is two-tiered:

        1. **Provider-authoritative** — if the caller passes ``total_cost > 0``,
           the value is recorded verbatim. ``input_cost`` and ``output_cost``
           stay at ``Decimal(0)`` because providers return a single total, not
           a split. This is the hot path for OpenRouter, which returns
           ``usage.cost`` on the wire.

        2. **DB-rate fallback** — if the caller passes ``total_cost = Decimal(0)``
           (the sentinel meaning "no wire-reported cost"), cost is computed as
           ``input_tokens * cost_per_input_unit + output_tokens * cost_per_output_unit``
           using the resolved ``LLMModel`` row. ``input_cost + output_cost == total_cost``
           holds on this path.

        When neither path produces a cost (no wire cost, no DB rates — e.g. a
        local/self-hosted model), all three cost columns are recorded as
        ``Decimal(0)``.
        """
        model = await self.db.get(LLMModel, model_id)
        input_cost = Decimal("0")
        output_cost = Decimal("0")

        if total_cost > Decimal("0"):
            # Provider-authoritative: leave input_cost/output_cost at 0.
            pass
        elif model and (model.cost_per_input_unit is not None or model.cost_per_output_unit is not None):
            # Use `is not None` (not truthiness) so a legitimate Decimal(0) rate on one
            # side — e.g. a model with free output tokens — doesn't collapse the entire
            # fallback and silently lose the other side's cost.
            input_rate = model.cost_per_input_unit if model.cost_per_input_unit is not None else Decimal(0)
            output_rate = model.cost_per_output_unit if model.cost_per_output_unit is not None else Decimal(0)
            input_cost = Decimal(str(input_tokens)) * input_rate
            output_cost = Decimal(str(output_tokens)) * output_rate
            total_cost = input_cost + output_cost

        usage = LLMUsage(
            provider_id=provider_id,
            model_id=model_id,
            user_id=user_id,
            request_type=request_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            response_time_ms=response_time_ms,
            success=success,
            error_message=error_message,
            request_metadata=request_metadata,
        )

        self.db.add(usage)
        await self.db.commit()

        return usage

    def _encrypt_api_key(self, api_key: str) -> str:
        """Encrypt API key for secure storage."""
        fernet = Fernet(self.encryption_key.encode())
        return fernet.encrypt(api_key.encode()).decode()
