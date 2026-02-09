"""Knowledge Base Permission Management API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing knowledge base permissions,
including granting, revoking, and querying permissions.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user, require_kb_manage_access
from ..core.response import ShuResponse
from ..schemas.rbac import (
    KnowledgeBasePermissionCreate,
    KnowledgeBasePermissionListResponse,
    KnowledgeBasePermissionResponse,
)
from ..services.rbac_service import RBACService, RBACServiceError
from .dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["permissions"])


@router.post(
    "/{kb_id}/permissions",
    summary="Grant KB permission",
    description="Grant a permission to a user or group for a knowledge base.",
)
# RBAC: require_kb_manage_access expects path param 'kb_id'
async def grant_kb_permission(
    kb_id: str = Path(..., description="Knowledge base ID"),
    permission_data: KnowledgeBasePermissionCreate = ...,
    current_user: User = Depends(require_kb_manage_access("kb_id")),
    db: AsyncSession = Depends(get_db),
):
    """Grant a permission to a user or group for a knowledge base.

    Only KB owners and admins can grant permissions.

    Args:
        kb_id: ID of the knowledge base
        permission_data: Permission creation data
        current_user: Current authenticated user (must have manage access)
        db: Database session

    Returns:
        JSONResponse with created permission information

    Raises:
        HTTPException: If permission creation fails or duplicate permission

    """
    logger.info(
        "Granting KB permission",
        extra={
            "kb_id": kb_id,
            "user_id": permission_data.user_id,
            "group_id": permission_data.group_id,
            "permission_level": permission_data.permission_level,
            "granted_by": current_user.id,
        },
    )

    try:
        rbac_service = RBACService(db)
        permission = await rbac_service.grant_kb_permission(kb_id, permission_data, current_user.id)

        # Get additional details for response
        target_name = None
        granter_name = None

        if permission.user_id:
            user = await rbac_service._get_user(permission.user_id)
            target_name = user.email
        elif permission.group_id:
            group = await rbac_service.get_user_group(permission.group_id)
            target_name = group.name

        # Get granter name
        granter = await rbac_service._get_user(permission.granted_by)
        granter_name = granter.email if granter else None

        response_data = KnowledgeBasePermissionResponse(
            id=permission.id,
            knowledge_base_id=permission.knowledge_base_id,
            user_id=permission.user_id,
            group_id=permission.group_id,
            permission_level=permission.permission_level,
            is_active=permission.is_active,
            expires_at=permission.expires_at,
            granted_by=permission.granted_by,
            granted_at=permission.granted_at,
            user_email=target_name if permission.user_id else None,
            group_name=target_name if permission.group_id else None,
            granter_name=granter_name,
        )

        return ShuResponse.created(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error granting permission: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error granting KB permission: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to grant KB permission",
        )


@router.get(
    "/{kb_id}/permissions",
    summary="List KB permissions",
    description="List all permissions for a knowledge base.",
)
# RBAC: require_kb_manage_access expects path param 'kb_id'
async def list_kb_permissions(
    kb_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_kb_manage_access("kb_id")),
    db: AsyncSession = Depends(get_db),
):
    """List all permissions for a knowledge base.

    Only KB owners and admins can view permissions.

    Args:
        kb_id: ID of the knowledge base
        current_user: Current authenticated user (must have manage access)
        db: Database session

    Returns:
        JSONResponse with list of permissions

    Raises:
        HTTPException: If KB not found or access denied

    """
    logger.info("Listing KB permissions", extra={"kb_id": kb_id, "requested_by": current_user.id})

    try:
        rbac_service = RBACService(db)
        permissions = await rbac_service.list_kb_permissions(kb_id)

        # Convert to response format
        permission_responses = []
        for permission in permissions:
            target_name = None
            granter_name = None

            if permission.user_id and permission.user:
                target_name = permission.user.email
            elif permission.group_id and permission.group:
                target_name = permission.group.name

            # Get granter name
            if permission.granter:
                granter_name = permission.granter.email

            permission_responses.append(
                KnowledgeBasePermissionResponse(
                    id=permission.id,
                    knowledge_base_id=permission.knowledge_base_id,
                    user_id=permission.user_id,
                    group_id=permission.group_id,
                    permission_level=permission.permission_level,
                    is_active=permission.is_active,
                    expires_at=permission.expires_at,
                    granted_by=permission.granted_by,
                    granted_at=permission.granted_at,
                    user_email=target_name if permission.user_id else None,
                    group_name=target_name if permission.group_id else None,
                    granter_name=granter_name,
                )
            )

        response_data = KnowledgeBasePermissionListResponse(
            permissions=permission_responses, total_count=len(permission_responses)
        )

        return ShuResponse.success(data=response_data.model_dump())

    except Exception as e:
        logger.error(f"Error listing KB permissions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list KB permissions",
        )


@router.delete(
    "/{kb_id}/permissions/{permission_id}",
    summary="Revoke KB permission",
    description="Revoke a permission for a knowledge base.",
)
# RBAC: require_kb_manage_access expects path param 'kb_id'
async def revoke_kb_permission(
    kb_id: str = Path(..., description="Knowledge base ID"),
    permission_id: str = Path(..., description="Permission ID"),
    current_user: User = Depends(require_kb_manage_access("kb_id")),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a permission for a knowledge base.

    Only KB owners and admins can revoke permissions.

    Args:
        kb_id: ID of the knowledge base
        permission_id: ID of the permission to revoke
        current_user: Current authenticated user (must have manage access)
        db: Database session

    Returns:
        JSONResponse confirming revocation

    Raises:
        HTTPException: If permission not found or revocation fails

    """
    logger.info(
        "Revoking KB permission",
        extra={"kb_id": kb_id, "permission_id": permission_id, "revoked_by": current_user.id},
    )

    try:
        rbac_service = RBACService(db)
        await rbac_service.revoke_kb_permission(permission_id)

        return ShuResponse.success(data={"revoked_permission_id": permission_id, "kb_id": kb_id})

    except RBACServiceError as e:
        logger.error(f"RBAC service error revoking permission: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error revoking KB permission: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke KB permission",
        )


@router.get(
    "/{kb_id}/permissions/effective",
    summary="Get effective permissions",
    description="Get effective permissions for current user on a knowledge base.",
)
async def get_effective_permissions(
    kb_id: str = Path(..., description="Knowledge base ID"),
    user_id: str | None = Query(None, description="User ID (defaults to current user)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get effective permissions for a user on a knowledge base.

    Users can check their own permissions. Admins and KB managers can check any user's permissions.

    Args:
        kb_id: ID of the knowledge base
        user_id: ID of the user to check (defaults to current user)
        current_user: Current authenticated user
        db: Database session

    Returns:
        JSONResponse with effective permission information

    Raises:
        HTTPException: If access denied or user not found

    """
    target_user_id = user_id or current_user.id

    # Check if user can query this information
    if target_user_id != current_user.id:
        # Only admins or KB managers can check other users' permissions
        from ..auth.rbac import rbac

        can_manage = await rbac.can_manage_kb(current_user, kb_id, db)
        if not can_manage and current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot check permissions for other users",
            )

    logger.info(
        "Getting effective permissions",
        extra={"kb_id": kb_id, "target_user_id": target_user_id, "requested_by": current_user.id},
    )

    try:
        rbac_service = RBACService(db)
        effective_permission = await rbac_service.get_effective_permission(target_user_id, kb_id)

        if not effective_permission:
            return ShuResponse.success(data=None)

        return ShuResponse.success(data=effective_permission.model_dump())

    except Exception as e:
        logger.error(f"Error getting effective permissions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get effective permissions",
        )
