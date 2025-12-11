"""
Branding configuration API endpoints.
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.models import User
from ..auth.rbac import require_admin
from ..schemas.branding import BrandingSettings, BrandingSettingsUpdate
from ..schemas.envelope import SuccessResponse
from ..services.branding_service import BrandingService

router = APIRouter(prefix="/settings/branding", tags=["branding"])


@router.get("", response_model=SuccessResponse[BrandingSettings])
async def get_branding(db: AsyncSession = Depends(get_db)):
    """Retrieve branding configuration, including defaults when unset."""
    service = BrandingService(db)
    branding = await service.get_branding()
    return SuccessResponse(data=branding)


@router.patch("", response_model=SuccessResponse[BrandingSettings])
async def patch_branding(
    payload: BrandingSettingsUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Partially update branding configuration."""
    service = BrandingService(db)
    try:
        branding = await service.update_branding(payload, user_id=current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.post("/logo", response_model=SuccessResponse[BrandingSettings])
async def upload_logo(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload and apply a new logo asset."""
    service = BrandingService(db)
    file_bytes = await _read_upload(file, service.settings.branding_max_asset_size_bytes)
    try:
        branding = await service.save_asset(
            filename=file.filename or "logo",
            file_bytes=file_bytes,
            asset_type="logo",
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.post("/favicon", response_model=SuccessResponse[BrandingSettings])
async def upload_favicon(
    file: UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload and apply a new favicon asset."""
    service = BrandingService(db)
    file_bytes = await _read_upload(file, service.settings.branding_max_asset_size_bytes)
    try:
        branding = await service.save_asset(
            filename=file.filename or "favicon",
            file_bytes=file_bytes,
            asset_type="favicon",
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SuccessResponse(data=branding)


@router.get("/assets/{filename}")
async def get_branding_asset(filename: str, db: AsyncSession = Depends(get_db)):
    """Serve stored branding assets like logos and favicons."""
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
