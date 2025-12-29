"""Configuration API endpoints for Shu"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

from ..core.config import get_settings_instance

from ..schemas.envelope import SuccessResponse

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
