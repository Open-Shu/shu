"""API integration connection admin API.

CRUD, sync, and per-tool configuration for API integrations imported via YAML.
All paths are mounted under ``/plugins/api`` by the plugins aggregator router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..schemas.api_integration_admin import (
    ApiConnectionCreate,
    ApiConnectionListResponse,
    ApiConnectionResponse,
    ApiConnectionStatus,
    ApiConnectionUpdate,
    ApiSyncResult,
)
from ..schemas.envelope import SuccessResponse
from ..schemas.integration_common import ToolConfigUpdate
from ..services.api_integration_service import DEGRADED_THRESHOLD, ApiIntegrationService
from .dependencies import get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["api-integration-admin"])


def _derive_status(conn) -> ApiConnectionStatus:
    """Derive connection status from health-tracking fields."""
    if not conn.enabled:
        return ApiConnectionStatus.DISCONNECTED
    if (conn.consecutive_failures or 0) >= DEGRADED_THRESHOLD:
        return ApiConnectionStatus.DEGRADED
    if conn.last_error and not conn.last_synced_at:
        return ApiConnectionStatus.ERROR
    if conn.last_synced_at:
        return ApiConnectionStatus.CONNECTED
    return ApiConnectionStatus.DISCONNECTED


def _to_response(conn) -> ApiConnectionResponse:
    """Convert an ApiServerConnection model instance to an API response."""
    discovered = conn.discovered_tools or []
    return ApiConnectionResponse(
        id=str(conn.id),
        name=conn.name,
        url=conn.url,
        spec_type=conn.spec_type or "openapi",
        base_url=conn.base_url,
        tool_configs=conn.tool_configs,
        discovered_tools=discovered,
        timeouts=conn.timeouts,
        response_size_limit_bytes=conn.response_size_limit_bytes,
        enabled=conn.enabled,
        status=_derive_status(conn),
        tool_count=len(discovered),
        last_synced_at=conn.last_synced_at,
        last_error=conn.last_error,
        consecutive_failures=conn.consecutive_failures or 0,
        has_auth=bool(conn.auth_config),
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


@router.post("/connections", response_model=SuccessResponse[ApiConnectionResponse])
async def create_connection(
    body: ApiConnectionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Import a new API integration from YAML content."""
    try:
        service = ApiIntegrationService(db)
        connection = await service.create_connection(body.yaml_content, body.auth_credential, str(user.id))
        return ShuResponse.created(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to create API integration: %s", e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error creating API integration: %s", e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/connections", response_model=SuccessResponse[ApiConnectionListResponse])
async def list_connections(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """List all API integration connections."""
    try:
        service = ApiIntegrationService(db)
        connections = await service.list_connections(str(user.id))
        return ShuResponse.success(
            ApiConnectionListResponse(
                items=[_to_response(c) for c in connections],
                total=len(connections),
            )
        )
    except ShuException as e:
        logger.error("Failed to list API integrations: %s", e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error listing API integrations: %s", e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/connections/{connection_id}", response_model=SuccessResponse[ApiConnectionResponse])
async def get_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Get a single API integration connection."""
    try:
        service = ApiIntegrationService(db)
        connection = await service.get_connection(connection_id, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to get API integration %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error getting API integration %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.patch("/connections/{connection_id}", response_model=SuccessResponse[ApiConnectionResponse])
async def update_connection(
    connection_id: str,
    body: ApiConnectionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Update an API integration connection."""
    try:
        service = ApiIntegrationService(db)
        connection = await service.update_connection(connection_id, body, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to update API integration %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error updating API integration %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Delete an API integration connection.

    Returns 409 if active feeds reference this connection.
    """
    try:
        service = ApiIntegrationService(db)
        await service.delete_connection(connection_id, str(user.id))
        return ShuResponse.no_content()
    except ShuException as e:
        logger.error("Failed to delete API integration %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error deleting API integration %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("/connections/{connection_id}/sync", response_model=SuccessResponse[ApiSyncResult])
async def sync_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Trigger OpenAPI spec fetch and tool discovery sync."""
    try:
        service = ApiIntegrationService(db)
        result = await service.sync_connection(connection_id, str(user.id))
        return ShuResponse.success(result)
    except ShuException as e:
        logger.error("Failed to sync API integration %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error syncing API integration %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.patch(
    "/connections/{connection_id}/tools/{tool_name}",
    response_model=SuccessResponse[ApiConnectionResponse],
)
async def update_tool_config(
    connection_id: str,
    tool_name: str,
    body: ToolConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Update per-tool configuration on an API integration connection."""
    try:
        service = ApiIntegrationService(db)
        connection = await service.update_tool_config(connection_id, tool_name, body, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to update API tool config %s/%s: %s", connection_id, tool_name, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error updating API tool config %s/%s: %s", connection_id, tool_name, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
