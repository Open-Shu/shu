"""User Permission Query API endpoints for Shu RAG Backend.

This module provides REST API endpoints for querying user permissions
across knowledge bases and groups.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.response import ShuResponse
from ..models.knowledge_base import KnowledgeBase
from ..models.rbac import KnowledgeBasePermission, UserGroup, UserGroupMembership
from ..schemas.rbac import (
    KnowledgeBasePermissionListResponse,
    KnowledgeBasePermissionResponse,
    UserGroupMembershipListResponse,
    UserGroupMembershipResponse,
)
from .dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["user-permissions"])


@router.get(
    "/me/permissions/knowledge-bases",
    summary="Get current user KB permissions",
    description="Get all knowledge base permissions for the current user.",
)
async def get_current_user_kb_permissions(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Get all knowledge base permissions for the current user.

    This is a convenience endpoint that doesn't require specifying the user ID.

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        JSONResponse with list of current user's KB permissions

    """
    logger.info("Getting current user KB permissions", extra={"user_id": current_user.id})

    # Delegate to the main function
    return await get_user_kb_permissions(current_user.id, current_user, db)


@router.get(
    "/me/groups",
    summary="Get current user group memberships",
    description="Get all group memberships for the current user.",
)
async def get_current_user_group_memberships(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Get all group memberships for the current user.

    This is a convenience endpoint that doesn't require specifying the user ID.

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        JSONResponse with list of current user's group memberships

    """
    logger.info("Getting current user group memberships", extra={"user_id": current_user.id})

    # Delegate to the main function
    return await get_user_group_memberships(current_user.id, current_user, db)


@router.get(
    "/{user_id}/permissions/knowledge-bases",
    summary="Get user KB permissions",
    description="Get all knowledge base permissions for a specific user.",
)
async def get_user_kb_permissions(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all knowledge base permissions for a specific user.

    Users can view their own permissions. Admins can view any user's permissions.

    Args:
        user_id: ID of the user to check permissions for
        current_user: Current authenticated user
        db: Database session

    Returns:
        JSONResponse with list of user's KB permissions

    Raises:
        HTTPException: If access denied or user not found

    """
    # Check if user can access this information
    if user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view permissions for other users")

    logger.info("Getting user KB permissions", extra={"user_id": user_id, "requested_by": current_user.id})

    try:
        # Get direct user permissions
        direct_permissions_result = await db.execute(
            select(KnowledgeBasePermission)
            .join(KnowledgeBase, KnowledgeBasePermission.knowledge_base_id == KnowledgeBase.id)
            .where(
                and_(
                    KnowledgeBasePermission.user_id == user_id,
                    KnowledgeBasePermission.is_active == True,
                )
            )
        )
        direct_permissions = direct_permissions_result.scalars().all()

        # Get group-based permissions
        user_groups_result = await db.execute(
            select(UserGroupMembership.group_id).where(
                and_(UserGroupMembership.user_id == user_id, UserGroupMembership.is_active == True)
            )
        )
        user_group_ids = [row[0] for row in user_groups_result.fetchall()]

        group_permissions = []
        if user_group_ids:
            group_permissions_result = await db.execute(
                select(KnowledgeBasePermission)
                .join(KnowledgeBase, KnowledgeBasePermission.knowledge_base_id == KnowledgeBase.id)
                .where(
                    and_(
                        KnowledgeBasePermission.group_id.in_(user_group_ids),
                        KnowledgeBasePermission.is_active == True,
                    )
                )
            )
            group_permissions = group_permissions_result.scalars().all()

        # Get KB details for response
        all_permissions = list(direct_permissions) + list(group_permissions)
        permission_responses = []

        for permission in all_permissions:
            # Get KB name
            kb_result = await db.execute(
                select(KnowledgeBase.name).where(KnowledgeBase.id == permission.knowledge_base_id)
            )
            kb_name = kb_result.scalar_one_or_none()

            # Get group name if it's a group permission
            group_name = None
            if permission.group_id:
                group_result = await db.execute(select(UserGroup.name).where(UserGroup.id == permission.group_id))
                group_name = group_result.scalar_one_or_none()

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
                    kb_name=kb_name,
                    group_name=group_name,
                )
            )

        response_data = KnowledgeBasePermissionListResponse(
            permissions=permission_responses, total_count=len(permission_responses)
        )

        return ShuResponse.success(data=response_data.model_dump())

    except Exception as e:
        logger.error(f"Error getting user KB permissions: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user KB permissions",
        )


@router.get(
    "/{user_id}/groups",
    summary="Get user group memberships",
    description="Get all group memberships for a specific user.",
)
async def get_user_group_memberships(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all group memberships for a specific user.

    Users can view their own group memberships. Admins can view any user's memberships.

    Args:
        user_id: ID of the user to check memberships for
        current_user: Current authenticated user
        db: Database session

    Returns:
        JSONResponse with list of user's group memberships

    Raises:
        HTTPException: If access denied or user not found

    """
    # Check if user can access this information
    if user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view group memberships for other users",
        )

    logger.info(
        "Getting user group memberships",
        extra={"user_id": user_id, "requested_by": current_user.id},
    )

    try:
        # Get user's group memberships
        memberships_result = await db.execute(
            select(UserGroupMembership).where(
                and_(UserGroupMembership.user_id == user_id, UserGroupMembership.is_active == True)
            )
        )
        memberships = memberships_result.scalars().all()

        # Get group details for response
        membership_responses = []
        for membership in memberships:
            # Get group name
            group_result = await db.execute(select(UserGroup.name).where(UserGroup.id == membership.group_id))
            group_name = group_result.scalar_one_or_none()

            membership_responses.append(
                UserGroupMembershipResponse(
                    id=membership.id,
                    user_id=membership.user_id,
                    group_id=membership.group_id,
                    role=membership.role,
                    is_active=membership.is_active,
                    granted_by=membership.granted_by,
                    granted_at=membership.granted_at,
                    user_email=current_user.email,
                    user_name=current_user.email,
                    group_name=group_name,
                )
            )

        response_data = UserGroupMembershipListResponse(
            memberships=membership_responses, total_count=len(membership_responses)
        )

        return ShuResponse.success(data=response_data.model_dump())

    except Exception as e:
        logger.error(f"Error getting user group memberships: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user group memberships",
        )
