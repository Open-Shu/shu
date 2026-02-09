"""Pydantic schemas for Provider Type Definitions (read-only API exposure)."""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.config import get_settings
from shu.models.llm_provider import LLMProvider
from shu.models.provider_type_definition import ProviderTypeDefinition
from shu.services.providers.adapter_base import (
    BaseProviderAdapter,
    ProviderCapabilities,
    get_adapter_from_provider,
)
from shu.services.providers.parameter_definitions import serialize_parameter_mapping


class ProviderTypeDefinitionSchema(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)

    id: str
    key: str
    display_name: str
    provider_adapter_name: str
    is_active: bool
    base_url_template: str
    parameter_mapping: dict[str, Any] | None = None
    endpoints: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    provider_capabilities: dict[str, Any] | None = {}
    # Rate limit defaults (from config, 0 = unlimited)
    rate_limit_rpm_default: int = 0
    rate_limit_tpm_default: int = 0

    @classmethod
    def build_from_default(cls, record: ProviderTypeDefinition, adapter: BaseProviderAdapter) -> Self:
        settings = get_settings()
        return ProviderTypeDefinitionSchema(
            id=record.id,
            key=record.key,
            display_name=record.display_name,
            provider_adapter_name=record.provider_adapter_name,
            is_active=record.is_active,
            base_url_template=adapter.get_api_base_url(),
            parameter_mapping=serialize_parameter_mapping(adapter.get_parameter_mapping()),
            endpoints=adapter.get_endpoint_settings(),
            provider_capabilities=adapter.get_capabilities().to_dict(
                include_disabled=True, supported_mask=adapter.get_capabilities()
            ),
            rate_limit_rpm_default=settings.llm_rate_limit_rpm_default,
            rate_limit_tpm_default=settings.llm_rate_limit_tpm_default,
        )

    @classmethod
    def build_from_existing(cls, db_session: AsyncSession, provider: LLMProvider) -> Self:
        settings = get_settings()
        adapter = get_adapter_from_provider(db_session, provider)
        capabilities = adapter.get_field_with_override("get_capabilities")
        return ProviderTypeDefinitionSchema(
            id=provider.id,
            key="",
            display_name=provider.name,
            provider_adapter_name=provider.provider_definition.provider_adapter_name,
            is_active=provider.is_active,
            base_url_template=adapter.get_field_with_override("get_api_base_url"),
            parameter_mapping=serialize_parameter_mapping(adapter.get_parameter_mapping()),
            endpoints=adapter.get_endpoint_settings(),
            provider_capabilities=capabilities,
            rate_limit_rpm_default=settings.llm_rate_limit_rpm_default,
            rate_limit_tpm_default=settings.llm_rate_limit_tpm_default,
        )

    @classmethod
    def to_config_settings(
        cls,
        provider_settings: dict[str, Any],
        default_capabilities: ProviderCapabilities | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider_capabilities = kwargs.pop("provider_capabilities", {}) or {}
        if isinstance(default_capabilities, ProviderCapabilities) and not provider_capabilities:
            capabilities = default_capabilities.to_dict(include_disabled=True, supported_mask=default_capabilities)
        else:
            cap_obj = ProviderCapabilities.from_request_dict(
                provider_capabilities if isinstance(provider_capabilities, dict) else {}
            )
            capabilities = cap_obj.to_dict(include_disabled=True, supported_mask=default_capabilities)

        config = {"get_capabilities": capabilities}
        config.update(provider_settings)
        return config


class ProviderTypeDefinitionListItem(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)

    key: str
    display_name: str
    provider_adapter_name: str
    is_active: bool

    @classmethod
    def from_provider_type_definition(cls, row: ProviderTypeDefinitionSchema) -> Self:
        return cls(
            key=row.key,
            display_name=row.display_name,
            provider_adapter_name=row.provider_adapter_name,
            is_active=row.is_active,
        )

    @classmethod
    def from_provider_type_definitions(cls, rows: list[ProviderTypeDefinitionSchema]) -> list[Self]:
        return [cls.from_provider_type_definition(row) for row in rows]
