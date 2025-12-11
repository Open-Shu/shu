"""Configuration API endpoints for Shu"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any

from ..core.config import get_settings_instance

from ..schemas.envelope import SuccessResponse

router = APIRouter(prefix="/config", tags=["configuration"])

class PublicConfig(BaseModel):
    """Public configuration that can be safely exposed to frontend"""
    google_client_id: str
    app_name: str
    version: str
    environment: str

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
        environment=settings.environment
    )

    return SuccessResponse(data=config)
