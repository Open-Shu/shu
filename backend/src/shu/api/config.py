"""Configuration API endpoints for Shu."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.config import get_settings_instance
from ..models import (
    Document,
    Experience,
    KnowledgeBase,
    LLMProvider,
    ModelConfiguration,
    PluginDefinition,
    PluginFeed,
)
from ..schemas.config import PublicConfig, SetupStatus, UploadRestrictions
from ..schemas.envelope import SuccessResponse

router = APIRouter(prefix="/config", tags=["configuration"])


@router.get("/public", response_model=SuccessResponse[PublicConfig])
async def get_public_config():
    """Get public configuration for frontend.

    This endpoint exposes only public, non-sensitive configuration values.
    This route is public (no auth), controlled by AuthenticationMiddleware public paths.
    """
    settings = get_settings_instance()

    config = PublicConfig(
        google_client_id=settings.google_client_id or "",
        microsoft_client_id=settings.microsoft_client_id or "",
        app_name=settings.app_name,
        version=settings.version,
        environment=settings.environment,
        upload_restrictions=UploadRestrictions(
            allowed_types=[t.lower() for t in settings.chat_attachment_allowed_types],
            max_size_bytes=settings.chat_attachment_max_size,
        ),
        kb_upload_restrictions=UploadRestrictions(
            allowed_types=[t.lower() for t in settings.kb_upload_allowed_types],
            max_size_bytes=settings.kb_upload_max_size,
        ),
    )

    return SuccessResponse(data=config)


@router.get("/setup-status", response_model=SuccessResponse[SetupStatus])
async def get_setup_status(
    _current_user: User = Depends(get_current_user),  # Auth required, but user not used
    db: AsyncSession = Depends(get_db),
):
    """Get setup completion status for QuickStart wizard.

    Returns boolean flags indicating whether each setup step has been completed.
    Used by the frontend to show checkmarks on the QuickStart page.

    Uses a single optimized query with subqueries instead of 6 separate queries.
    """
    # Build subqueries for each count - more readable than nested select()
    llm_providers_count = (
        select(func.count())
        .select_from(LLMProvider)
        .where(LLMProvider.is_active)
        .correlate(None)
        .scalar_subquery()
        .label("llm_providers")
    )

    model_configs_count = (
        select(func.count())
        .select_from(ModelConfiguration)
        .where(ModelConfiguration.is_active)
        .correlate(None)
        .scalar_subquery()
        .label("model_configs")
    )

    knowledge_bases_count = (
        select(func.count()).select_from(KnowledgeBase).correlate(None).scalar_subquery().label("knowledge_bases")
    )

    documents_count = select(func.count()).select_from(Document).correlate(None).scalar_subquery().label("documents")

    plugins_count = (
        select(func.count())
        .select_from(PluginDefinition)
        .where(PluginDefinition.enabled)
        .correlate(None)
        .scalar_subquery()
        .label("plugins")
    )

    feeds_count = select(func.count()).select_from(PluginFeed).correlate(None).scalar_subquery().label("feeds")

    experiences_count = (
        select(func.count()).select_from(Experience).correlate(None).scalar_subquery().label("experiences")
    )

    # Execute single query with all subqueries
    result = await db.execute(
        select(
            llm_providers_count,
            model_configs_count,
            knowledge_bases_count,
            documents_count,
            plugins_count,
            feeds_count,
            experiences_count,
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
        experience_created=row.experiences > 0,
    )

    return SuccessResponse(data=status)
