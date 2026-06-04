"""Branding configuration API endpoints."""

import mimetypes

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import require_admin
from ..core.config import DeploymentMode, get_settings_instance
from ..core.tenant import tenant_context_for_tenant_id
from ..schemas.branding import BrandingSettings, BrandingSettingsUpdate
from ..schemas.envelope import SuccessResponse
from ..services.branding_service import BrandingService

router = APIRouter(prefix="/settings/branding", tags=["branding"])


_MT_BRANDING_DETAIL = "Branding is not configurable in multi-tenant deployments."


def _is_multi_tenant() -> bool:
    return get_settings_instance().deployment_mode == DeploymentMode.MULTI_TENANT


@router.get("", response_model=SuccessResponse[BrandingSettings])
async def get_branding(db: AsyncSession = Depends(get_db)):
    """Retrieve branding configuration, including defaults when unset.

    Public route — auth middleware skips this path, so there's no
    request-bound tenant_context. Two shapes:

    * Multi-tenant: branding is a deployment-level concept (no per-tenant
      customization), so return the operator-configured defaults without a
      DB read. Reading ``system_settings`` here would hit RLS default-deny
      (now that the table is tenant-scoped) and fall back to defaults
      anyway — just skip the round-trip.
    * Self-hosted / silo: wrap in the deployment's tenant_context so the
      RLS-scoped SELECT on ``system_settings`` returns the operator's
      saved overrides instead of empty.
    """
    if _is_multi_tenant():
        return SuccessResponse(data=BrandingService.defaults())
    async with tenant_context_for_tenant_id(None):  # falls through to deployment tenant
        branding = await BrandingService(db).get_branding()
    return SuccessResponse(data=branding)


@router.patch("", response_model=SuccessResponse[BrandingSettings])
async def patch_branding(
    payload: BrandingSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update branding configuration, partially."""
    if _is_multi_tenant():
        # The public GET ignores per-tenant rows in MT; accepting writes
        # here would surface a silent "Branding saved" / "still default"
        # contradiction in the admin UI. Be explicit.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_MT_BRANDING_DETAIL)
    service = BrandingService(db)
    try:
        branding = await service.update_branding(payload, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.post("/favicon", response_model=SuccessResponse[BrandingSettings])
async def upload_favicon(
    file: UploadFile = File(...),
    theme: str = Query("light", pattern="^(light|dark)$"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload and apply a new favicon asset for the specified theme."""
    if _is_multi_tenant():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_MT_BRANDING_DETAIL)
    service = BrandingService(db)
    file_bytes = await _read_upload(file, service.settings.branding_max_asset_size_bytes)
    asset_type = "dark_favicon" if theme == "dark" else "favicon"

    ext = mimetypes.guess_extension(file.content_type or "") or ".png"
    filename = file.filename or f"favicon_{theme}{ext}"

    try:
        branding = await service.save_asset(
            filename=filename,
            file_bytes=file_bytes,
            asset_type=asset_type,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.post("/assistant-avatar", response_model=SuccessResponse[BrandingSettings])
async def upload_assistant_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom assistant avatar image and switch the avatar mode to "custom"."""
    if _is_multi_tenant():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_MT_BRANDING_DETAIL)
    service = BrandingService(db)
    file_bytes = await _read_upload(file, service.settings.branding_max_asset_size_bytes)

    ext = mimetypes.guess_extension(file.content_type or "") or ".png"
    filename = file.filename or f"assistant_avatar{ext}"

    try:
        branding = await service.save_asset(
            filename=filename,
            file_bytes=file_bytes,
            asset_type="assistant_avatar",
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.get("/assets/{filename}")
async def get_branding_asset(filename: str, db: AsyncSession = Depends(get_db)):
    """Serve stored branding assets like favicons."""
    service = BrandingService(db)
    try:
        path = service.resolve_asset_path(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    media_type = service.guess_mime_type(path)
    return FileResponse(path, media_type=media_type)


async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read upload content enforcing a maximum size."""
    data = await upload.read()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size {len(data)} exceeds limit of {max_bytes} bytes",
        )
    return data
