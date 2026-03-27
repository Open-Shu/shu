"""MCP server connection admin API.

CRUD, sync, and per-tool configuration for external MCP server connections.
All paths are mounted under ``/plugins/mcp`` by the plugins aggregator router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..schemas.mcp_admin import (
    McpConnectionCreate,
    McpConnectionListResponse,
    McpConnectionResponse,
    McpConnectionStatus,
    McpConnectionUpdate,
    McpSyncResult,
    McpToolConfigUpdate,
)
from ..services.mcp_service import DEGRADED_THRESHOLD, McpService
from .dependencies import get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-admin"])


def _derive_status(conn) -> McpConnectionStatus:
    """Derive connection status from health-tracking fields."""
    if not conn.enabled:
        return McpConnectionStatus.DISCONNECTED
    if (conn.consecutive_failures or 0) >= DEGRADED_THRESHOLD:
        return McpConnectionStatus.DEGRADED
    if conn.last_error and not conn.last_connected_at:
        return McpConnectionStatus.ERROR
    if conn.last_connected_at:
        return McpConnectionStatus.CONNECTED
    return McpConnectionStatus.DISCONNECTED


def _to_response(conn) -> McpConnectionResponse:
    """Convert an McpServerConnection model instance to an API response."""
    discovered = conn.discovered_tools or []
    return McpConnectionResponse(
        id=str(conn.id),
        name=conn.name,
        url=conn.url,
        tool_configs=conn.tool_configs,
        discovered_tools=discovered,
        timeouts=conn.timeouts,
        response_size_limit_bytes=conn.response_size_limit_bytes,
        enabled=conn.enabled,
        status=_derive_status(conn),
        tool_count=len(discovered),
        last_synced_at=conn.last_synced_at,
        last_connected_at=conn.last_connected_at,
        last_error=conn.last_error,
        consecutive_failures=conn.consecutive_failures or 0,
        server_info=conn.server_info,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


@router.post("/connections", response_model=SuccessResponse[McpConnectionResponse])
async def create_connection(
    body: McpConnectionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Create a new MCP server connection."""
    try:
        service = McpService(db)
        connection = await service.create_connection(body, str(user.id))
        return ShuResponse.created(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to create MCP connection: %s", e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error creating MCP connection: %s", e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/connections", response_model=SuccessResponse[McpConnectionListResponse])
async def list_connections(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """List all MCP server connections."""
    try:
        service = McpService(db)
        connections = await service.list_connections(str(user.id))
        return ShuResponse.success(
            McpConnectionListResponse(
                items=[_to_response(c) for c in connections],
                total=len(connections),
            )
        )
    except ShuException as e:
        logger.error("Failed to list MCP connections: %s", e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error listing MCP connections: %s", e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/connections/{connection_id}", response_model=SuccessResponse[McpConnectionResponse])
async def get_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Get a single MCP server connection."""
    try:
        service = McpService(db)
        connection = await service.get_connection(connection_id, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to get MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error getting MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.patch("/connections/{connection_id}", response_model=SuccessResponse[McpConnectionResponse])
async def update_connection(
    connection_id: str,
    body: McpConnectionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Update an MCP server connection."""
    try:
        service = McpService(db)
        connection = await service.update_connection(connection_id, body, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to update MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error updating MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Delete an MCP server connection.

    Returns 409 if active feeds reference this connection.
    """
    try:
        service = McpService(db)
        await service.delete_connection(connection_id, str(user.id))
        return ShuResponse.no_content()
    except ShuException as e:
        logger.error("Failed to delete MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error deleting MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("/connections/{connection_id}/sync", response_model=SuccessResponse[McpSyncResult])
async def sync_connection(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Trigger tool discovery sync on an MCP server connection."""
    try:
        service = McpService(db)
        result = await service.sync_connection(connection_id, str(user.id))
        return ShuResponse.success(result)
    except ShuException as e:
        logger.error("Failed to sync MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error syncing MCP connection %s: %s", connection_id, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.patch(
    "/connections/{connection_id}/tools/{tool_name}",
    response_model=SuccessResponse[McpConnectionResponse],
)
async def update_tool_config(
    connection_id: str,
    tool_name: str,
    body: McpToolConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_power_user),
):
    """Update per-tool configuration (type, ingest mapping) on an MCP connection."""
    try:
        service = McpService(db)
        connection = await service.update_tool_config(connection_id, tool_name, body, str(user.id))
        return ShuResponse.success(_to_response(connection))
    except ShuException as e:
        logger.error("Failed to update MCP tool config %s/%s: %s", connection_id, tool_name, e)
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error updating MCP tool config %s/%s: %s", connection_id, tool_name, e)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
