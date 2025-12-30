"""Configuration API endpoints for Shu"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..schemas.envelope import SuccessResponse
from ..api.dependencies import get_db
from ..auth.rbac import get_current_user
from ..auth.models import User
from ..models import (
    LLMProvider,
    ModelConfiguration,
    KnowledgeBase,
    Document,
    PluginDefinition,
)

router = APIRouter(prefix="/config", tags=["configuration"])


class UploadRestrictions(BaseModel):
    """File upload restrictions"""
    allowed_types: List[str]
    max_size_bytes: int


class PublicConfig(BaseModel):
    """Public configuration that can be safely exposed to frontend"""
    google_client_id: str
    app_name: str
    version: str
    environment: str
    # Chat attachments (supports images via OCR)
    upload_restrictions: UploadRestrictions
    # KB document upload (no standalone image support - text extraction only)
    kb_upload_restrictions: UploadRestrictions


@router.get("/public", response_model=SuccessResponse[PublicConfig])
async def get_public_config():
    """
    Get public configuration for frontend.

    This endpoint exposes only public, non-sensitive configuration values.
    This route is public (no auth), controlled by AuthenticationMiddleware public paths.
    """
    settings = get_settings_instance()

    config = PublicConfig(
        google_client_id=settings.google_client_id or "",
        app_name=settings.app_name,
        version=settings.version,
        environment=settings.environment,
        upload_restrictions=UploadRestrictions(
            allowed_types=settings.chat_attachment_allowed_types,
            max_size_bytes=settings.chat_attachment_max_size,
        ),
        kb_upload_restrictions=UploadRestrictions(
            allowed_types=settings.kb_upload_allowed_types,
            max_size_bytes=settings.kb_upload_max_size,
        ),
    )

    return SuccessResponse(data=config)


class SetupStatus(BaseModel):
    """Setup completion status for QuickStart wizard"""
    llm_provider_configured: bool
    model_configuration_created: bool
    knowledge_base_created: bool
    documents_added: bool
    plugins_enabled: bool


@router.get("/setup-status", response_model=SuccessResponse[SetupStatus])
async def get_setup_status(
    _current_user: User = Depends(get_current_user),  # Auth required, but user not used
    db: AsyncSession = Depends(get_db),
):
    """
    Get setup completion status for QuickStart wizard.

    Returns boolean flags indicating whether each setup step has been completed.
    Used by the frontend to show checkmarks on the QuickStart page.
    """
    # Check if any active LLM provider exists
    provider_result = await db.execute(
        select(func.count()).select_from(LLMProvider).where(LLMProvider.is_active == True)
    )
    llm_provider_configured = provider_result.scalar() > 0

    # Check if any active model configuration exists
    model_config_result = await db.execute(
        select(func.count()).select_from(ModelConfiguration).where(ModelConfiguration.is_active == True)
    )
    model_configuration_created = model_config_result.scalar() > 0

    # Check if any knowledge base exists
    kb_result = await db.execute(
        select(func.count()).select_from(KnowledgeBase)
    )
    knowledge_base_created = kb_result.scalar() > 0

    # Check if any documents exist
    doc_result = await db.execute(
        select(func.count()).select_from(Document)
    )
    documents_added = doc_result.scalar() > 0

    # Check if any plugin is enabled
    plugin_result = await db.execute(
        select(func.count()).select_from(PluginDefinition).where(PluginDefinition.enabled == True)
    )
    plugins_enabled = plugin_result.scalar() > 0

    status = SetupStatus(
        llm_provider_configured=llm_provider_configured,
        model_configuration_created=model_configuration_created,
        knowledge_base_created=knowledge_base_created,
        documents_added=documents_added,
        plugins_enabled=plugins_enabled,
    )

    return SuccessResponse(data=status)
