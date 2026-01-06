"""
LLM API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing LLM providers,
models, and handling LLM operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict
import logging
import json
from datetime import datetime, timezone

from ..api.dependencies import get_db
from ..core.exceptions import LLMProviderError, LLMConfigurationError, LLMModelNotFoundError
from ..auth.rbac import get_current_user, require_admin
from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..auth.models import User
from ..llm.service import LLMService
from ..models.llm_provider import LLMProvider, LLMModel
from ..services.provider_type_definition_service import ProviderTypeDefinitionsService
from ..services import providers as _load_providers  # noqa: F401 ensure adapters register
from ..schemas.llm_provider_type import (
    ProviderTypeDefinitionSchema,
    ProviderTypeDefinitionListItem,
)
from ..services.providers.adapter_base import ProviderAdapterContext, get_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["LLM"])


# Pydantic models for API requests/responses

class EndpointDefUpdate(BaseModel):
    """Schema for a single endpoint override entry."""
    model_config = ConfigDict(protected_namespaces=(), extra='forbid')
    path: str = Field(..., description="Endpoint path (placeholders like {region} allowed)")
    options: Optional[Dict[str, Any]] = Field(None, description="Endpoint-specific options (e.g., discovery paths, message paths)")


class ProviderCapabilitiesUpdate(BaseModel):
    """Schema for a single endpoint override entry."""
    model_config = ConfigDict(protected_namespaces=(), extra='forbid')
    value: bool = Field(False, description="Capability enabled")


class LLMProviderCreate(BaseModel):
    """Schema for creating LLM providers."""
    name: str = Field(..., description="Provider name")
    provider_type: str = Field(..., description="Provider type (openai, anthropic, ollama)")
    api_endpoint: str = Field(..., description="API endpoint URL")
    api_key: Optional[str] = Field(None, description="API key")
    organization_id: Optional[str] = Field(None, description="Organization ID")
    rate_limit_rpm: int = Field(60, description="Rate limit (requests per minute, 0 = disabled)")
    rate_limit_tpm: int = Field(60000, description="Rate limit (tokens per minute, 0 = disabled)")
    budget_limit_monthly: Optional[float] = Field(None, description="Monthly budget limit in dollars")
    endpoints: Optional[Dict[str, EndpointDefUpdate]] = Field(
        None,
        description="Overrides for ProviderTypeDefinition.endpoints stored on provider"
    )
    provider_capabilities: Optional[Dict[str, ProviderCapabilitiesUpdate]] = Field(
        None,
        description="Overrides for ProviderTypeDefinition.endpoints stored on provider"
    )


class LLMProviderUpdate(BaseModel):
    """Schema for updating LLM providers."""
    name: Optional[str] = None
    provider_type: str
    api_endpoint: str
    api_key: Optional[str] = None
    organization_id: Optional[str] = None
    is_active: Optional[bool] = None
    rate_limit_rpm: Optional[int] = None
    rate_limit_tpm: Optional[int] = None
    budget_limit_monthly: Optional[float] = None
    endpoints: Optional[Dict[str, EndpointDefUpdate]] = Field(
        None,
        description="Overrides for ProviderTypeDefinition.endpoints stored on provider"
    )
    provider_capabilities: Optional[Dict[str, ProviderCapabilitiesUpdate]] = Field(
        None,
        description="Overrides for ProviderTypeDefinition.endpoints stored on provider"
    )


class LLMProviderResponse(BaseModel):
    """Schema for LLM provider responses."""
    id: str
    name: str
    provider_type: str
    api_endpoint: str
    organization_id: Optional[str]
    is_active: bool
    rate_limit_rpm: int
    rate_limit_tpm: int
    budget_limit_monthly: Optional[float]
    has_api_key: bool = Field(description="Whether provider has an API key configured")
    endpoints: Optional[Dict[str, Any]] = Field(None, description="Saved endpoint overrides on provider.config")
    provider_capabilities: Optional[Dict[str, Any]] = Field(None, description="Provider capability overrides on provider.config")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMModelCreate(BaseModel):
    """Schema for creating LLM models."""

    model_config = ConfigDict(protected_namespaces=(), extra="ignore")

    model_name: str = Field(..., description="Model name")
    display_name: Optional[str] = Field(None, description="Display name")
    model_type: str = Field("chat", description="Model type (chat, completion, embedding)")
    supports_streaming: bool = Field(True, description="Supports streaming")
    supports_functions: bool = Field(False, description="Supports function calling")
    supports_vision: bool = Field(False, description="Supports vision/image inputs")
    cost_per_input_token: Optional[float] = Field(None, description="Cost per input token")
    cost_per_output_token: Optional[float] = Field(None, description="Cost per output token")


class LLMModelResponse(BaseModel):
    """Schema for LLM model responses."""

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)

    id: str
    provider_id: str
    model_name: str
    display_name: str
    model_type: str
    supports_streaming: bool
    supports_functions: bool
    supports_vision: bool
    cost_per_input_token: Optional[float]
    cost_per_output_token: Optional[float]
    is_active: bool
    created_at: datetime


# Helper function to convert database model to response model
def _provider_to_response(db_session: AsyncSession, provider: LLMProvider) -> LLMProviderResponse:
    """Convert LLMProvider database model to response model."""
    data = ProviderTypeDefinitionSchema.build_from_existing(db_session, provider)
    return LLMProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        api_endpoint=data.base_url_template,
        organization_id=provider.organization_id,
        is_active=provider.is_active,
        rate_limit_rpm=provider.rate_limit_rpm,
        rate_limit_tpm=provider.rate_limit_tpm,
        budget_limit_monthly=float(provider.budget_limit_monthly) if provider.budget_limit_monthly else None,
        has_api_key=bool(provider.api_key_encrypted),
        endpoints=data.endpoints,
        provider_capabilities=data.provider_capabilities,
        created_at=provider.created_at,
        updated_at=provider.updated_at
    )


# Provider management endpoints
@router.get("/providers", response_model=SuccessResponse[List[LLMProviderResponse]])
async def list_providers(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all LLM providers."""
    try:
        llm_service = LLMService(db)
        providers = await llm_service.get_active_providers()
        return ShuResponse.success([
            _provider_to_response(db, provider) for provider in providers
        ])
    except Exception as e:
        logger.error(f"Error listing LLM providers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list LLM providers"
        )


@router.post("/providers", response_model=SuccessResponse[LLMProviderResponse], status_code=status.HTTP_201_CREATED)
async def create_provider(
    provider_data: LLMProviderCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new LLM provider (admin only)."""
    try:
        llm_service = LLMService(db)
        provider = await llm_service.create_provider(**provider_data.model_dump())
        return ShuResponse.created(_provider_to_response(db, provider))
    except LLMProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create LLM provider"
        )


@router.get("/providers/{provider_id}", response_model=SuccessResponse[LLMProviderResponse])
async def get_provider(
    provider_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get LLM provider by ID."""
    try:
        llm_service = LLMService(db)
        provider = await llm_service.get_provider_by_id(provider_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="LLM provider not found"
            )
        return ShuResponse.success(_provider_to_response(db, provider))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get LLM provider"
        )


@router.put("/providers/{provider_id}", response_model=SuccessResponse[LLMProviderResponse])
async def update_provider(
    provider_id: str,
    provider_data: LLMProviderUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update LLM provider (admin only)."""
    try:
        llm_service = LLMService(db)
        provider = await llm_service.update_provider(
            provider_id,
            **provider_data.dict(exclude_unset=True)
        )
        return ShuResponse.success(_provider_to_response(db, provider))
    except LLMProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error updating LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update LLM provider"
        )


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete LLM provider (admin only)."""
    try:
        llm_service = LLMService(db)
        success = await llm_service.delete_provider(provider_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="LLM provider not found"
            )
        return ShuResponse.no_content()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete LLM provider"
        )


@router.post("/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Test LLM provider connection (admin only)."""
    try:
        llm_service = LLMService(db)
        success = await llm_service.test_provider_connection(provider_id)
        return ShuResponse.success({
            "provider_id": provider_id,
            "connection_successful": success,
            "tested_at": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Error testing LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to test LLM provider"
        )


# Model management endpoints
@router.get("/models", response_model=SuccessResponse[List[LLMModelResponse]])
async def list_models(
    provider_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List available LLM models."""
    try:
        llm_service = LLMService(db)
        models = await llm_service.get_available_models(provider_id)
        return ShuResponse.success(models)
    except Exception as e:
        logger.error(f"Error listing LLM models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list LLM models"
        )


@router.get("/providers/{provider_id}/discover-models")
async def discover_provider_models(
    provider_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Discover available models from a provider's API."""
    try:
        llm_service = LLMService(db)
        models = await llm_service.discover_provider_models(provider_id)
        return ShuResponse.success({
            "provider_id": provider_id,
            "discovered_models": models,
            "count": len(models)
        })
    except LLMProviderError as e:
        logger.error(f"Provider error discovering models: {e}")
        # Return structured error with provider details if available
        return ShuResponse.error(
            message=str(e),
            code=e.error_code if hasattr(e, 'error_code') else 'LLM_PROVIDER_ERROR',
            details=getattr(e, 'details', None),
            status_code=getattr(e, 'status_code', status.HTTP_400_BAD_REQUEST)
        )
    except Exception as e:
        logger.error(f"Error discovering models for provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to discover models"
        )


@router.post("/providers/{provider_id}/sync-models")
async def sync_provider_models(
    provider_id: str,
    selected_models: List[str] = None,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Sync discovered models with database, enabling selected models."""
    try:
        llm_service = LLMService(db)

        # Parse request body if provided
        if selected_models is None:
            # Enable all discovered models
            models = await llm_service.sync_provider_models(provider_id)
        else:
            # Enable only selected models
            models = await llm_service.sync_provider_models(provider_id, selected_models)

        return ShuResponse.success({
            "provider_id": provider_id,
            "synced_models": [
                {
                    "id": model.id,
                    "model_name": model.model_name,
                    "display_name": model.display_name,
                    "is_active": model.is_active
                }
                for model in models
            ],
            "count": len(models)
        })
    except LLMProviderError as e:
        logger.error(f"Provider error syncing models: {e}")
        return ShuResponse.error(
            message=str(e),
            code=e.error_code if hasattr(e, 'error_code') else 'LLM_PROVIDER_ERROR',
            details=getattr(e, 'details', None),
            status_code=getattr(e, 'status_code', status.HTTP_400_BAD_REQUEST)
        )
    except Exception as e:
        logger.error(f"Error syncing models for provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync models"
        )


@router.delete("/providers/{provider_id}/models/{model_id}")
async def disable_provider_model(
    provider_id: str,
    model_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Disable/remove a model from a provider."""
    try:
        llm_service = LLMService(db)

        # Get the model
        model = await llm_service.get_model_by_id(model_id)
        if not model or model.provider_id != provider_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model not found"
            )

        # Disable the model
        model.is_active = False
        await db.commit()

        return ShuResponse.success({
            "message": f"Model '{model.model_name}' disabled successfully",
            "model_id": model_id,
            "provider_id": provider_id
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disabling model {model_id} for provider {provider_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable model"
        )


@router.post("/providers/{provider_id}/models", response_model=SuccessResponse[LLMModelResponse])
async def create_model(
    provider_id: str,
    model_data: LLMModelCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new LLM model configuration (admin only)."""
    try:
        llm_service = LLMService(db)
        model = await llm_service.create_model(provider_id, **model_data.dict())
        return ShuResponse.success(model)
    except LLMProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error creating LLM model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create LLM model"
        )


# Provider Type Definitions (read-only)
@router.get(
    "/provider-types",
    response_model=SuccessResponse[List[ProviderTypeDefinitionListItem]],
)
async def list_provider_types(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List provider type definitions (no secrets)."""
    service = ProviderTypeDefinitionsService(db)
    rows = await service.list(include_inactive=False)
    return ShuResponse.success(ProviderTypeDefinitionListItem.from_provider_type_definitions(rows))


@router.get(
    "/provider-types/{key}",
    response_model=SuccessResponse[ProviderTypeDefinitionSchema],
)
async def get_provider_type_definition(
    key: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single provider type definition by key."""
    service = ProviderTypeDefinitionsService(db)

    row = await service.get(key)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider type not found")
    
    try:
        context = ProviderAdapterContext(db)
        adapter = get_adapter(row.provider_adapter_name, context)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Adapter for provider {row.display_name} doesn't exist."
        )

    data = ProviderTypeDefinitionSchema.build_from_default(row, adapter)

    return ShuResponse.success(data)


@router.get("/health")
async def health_check(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check health status of all LLM providers."""
    try:
        llm_service = LLMService(db)
        providers = await llm_service.get_active_providers()

        health_status = []
        for provider in providers:
            is_healthy = await llm_service.test_provider_connection(provider.id)
            health_status.append({
                "provider_id": provider.id,
                "provider_name": provider.name,
                "provider_type": provider.provider_type,
                "is_healthy": is_healthy,
                "checked_at": datetime.now(timezone.utc).isoformat()
            })

        return ShuResponse.success({
            "status": "ok",
            "providers": health_status,
            "checked_at": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        logger.error(f"Error checking LLM health: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check LLM health"
        )
