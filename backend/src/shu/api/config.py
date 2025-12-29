"""Configuration API endpoints for Shu"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

from ..core.config import get_settings_instance

from ..schemas.envelope import SuccessResponse

router = APIRouter(prefix="/config", tags=["configuration"])


class UploadRestrictions(BaseModel):
    """File upload restrictions shared across the application"""
    allowed_types: List[str]
    max_size_bytes: int


class PublicConfig(BaseModel):
    """Public configuration that can be safely exposed to frontend"""
    google_client_id: str
    app_name: str
    version: str
    environment: str
    upload_restrictions: UploadRestrictions


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
    )

    return SuccessResponse(data=config)
