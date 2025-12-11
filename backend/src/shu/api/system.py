from fastapi import APIRouter, Depends

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.config import get_settings_instance
from ..core.response import ShuResponse
from ..schemas.system import VersionInfo

router = APIRouter(prefix="/system", tags=["system"])
settings = get_settings_instance()


@router.get("/version", summary="Application version and build metadata")
async def get_version(current_user: User = Depends(get_current_user)):
    info = VersionInfo(
        version=settings.version,
        git_sha=getattr(settings, "git_sha", None),
        build_timestamp=getattr(settings, "build_timestamp", None),
        db_release=getattr(settings, "db_release", None),
        environment=settings.environment,
    )
    return ShuResponse.success(info)

