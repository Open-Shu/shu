"""Configuration API endpoints for Shu"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..schemas.envelope import SuccessResponse
from ..schemas.config import UploadRestrictions, PublicConfig, SetupStatus
from ..api.dependencies import get_db
from ..auth.rbac import get_current_user
from ..auth.models import User
from ..models import (
    LLMProvider,
    ModelConfiguration,
    KnowledgeBase,
    Document,
    PluginDefinition,
    PluginFeed,
)

router = APIRouter(prefix="/config", tags=["configuration"])


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


@router.get("/setup-status", response_model=SuccessResponse[SetupStatus])
async def get_setup_status(
    _current_user: User = Depends(get_current_user),  # Auth required, but user not used
    db: AsyncSession = Depends(get_db),
):
    """
    Get setup completion status for QuickStart wizard.

    Returns boolean flags indicating whether each setup step has been completed.
    Used by the frontend to show checkmarks on the QuickStart page.

    Uses a single optimized query with subqueries instead of 6 separate queries.
    """
    # Single query using scalar subqueries for all counts
    result = await db.execute(
        select(
            select(func.count()).select_from(LLMProvider).where(LLMProvider.is_active == True).correlate(None).scalar_subquery().label("llm_providers"),
            select(func.count()).select_from(ModelConfiguration).where(ModelConfiguration.is_active == True).correlate(None).scalar_subquery().label("model_configs"),
            select(func.count()).select_from(KnowledgeBase).correlate(None).scalar_subquery().label("knowledge_bases"),
            select(func.count()).select_from(Document).correlate(None).scalar_subquery().label("documents"),
            select(func.count()).select_from(PluginDefinition).where(PluginDefinition.enabled == True).correlate(None).scalar_subquery().label("plugins"),
            select(func.count()).select_from(PluginFeed).correlate(None).scalar_subquery().label("feeds"),
        )
    )
    row = result.one()

    status = SetupStatus(
        llm_provider_configured=row.llm_providers > 0,
        model_configuration_created=row.model_configs > 0,
        knowledge_base_created=row.knowledge_bases > 0,
        documents_added=row.documents > 0,
        plugins_enabled=row.plugins > 0,
        plugin_feed_created=row.feeds > 0,
    )

    return SuccessResponse(data=status)
