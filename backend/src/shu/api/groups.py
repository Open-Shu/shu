"""User Group Management API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing user groups,
including CRUD operations and membership management.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_admin
from ..core.response import ShuResponse
from ..schemas.rbac import (
    UserGroupCreate,
    UserGroupListResponse,
    UserGroupMembershipCreate,
    UserGroupMembershipListResponse,
    UserGroupMembershipResponse,
    UserGroupResponse,
    UserGroupUpdate,
)
from ..services.rbac_service import RBACService, RBACServiceError
from .dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["groups"])


@router.post(
    "",
    summary="Create user group",
    description="Create a new user group for organizing users into teams.",
)
async def create_user_group(
    group_data: UserGroupCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user group.

    Only administrators can create user groups.

    Args:
        group_data: Group creation data
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with created group information

    Raises:
        HTTPException: If group creation fails or duplicate name

    """
    logger.info("Creating user group", extra={"group_name": group_data.name, "created_by": current_user.id})

    try:
        rbac_service = RBACService(db)
        group = await rbac_service.create_user_group(group_data, current_user.id)

        response_data = UserGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            is_active=group.is_active,
            created_by=group.created_by,
            created_at=group.created_at,
            updated_at=group.updated_at,
            member_count=0,  # New group has no members
        )

        return ShuResponse.created(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error creating group: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error creating group: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create user group")


@router.get("", summary="List user groups", description="List all user groups with pagination.")
async def list_user_groups(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    active_only: bool = Query(True, description="Show only active groups"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List user groups with pagination.

    Only administrators can list user groups.

    Args:
        page: Page number (1-based)
        page_size: Number of items per page
        active_only: Whether to show only active groups
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with paginated list of groups

    """
    logger.info(
        "Listing user groups",
        extra={
            "page": page,
            "page_size": page_size,
            "active_only": active_only,
            "requested_by": current_user.id,
        },
    )

    try:
        rbac_service = RBACService(db)
        groups, total_count = await rbac_service.list_user_groups(
            page=page, page_size=page_size, active_only=active_only
        )

        # Convert to response format
        group_responses = []
        for group in groups:
            # Get member count for each group
            members = await rbac_service.list_group_members(group.id)
            group_responses.append(
                UserGroupResponse(
                    id=group.id,
                    name=group.name,
                    description=group.description,
                    is_active=group.is_active,
                    created_by=group.created_by,
                    created_at=group.created_at,
                    updated_at=group.updated_at,
                    member_count=len(members),
                )
            )

        response_data = UserGroupListResponse(
            groups=group_responses, total_count=total_count, page=page, page_size=page_size
        )

        return ShuResponse.success(data=response_data.model_dump())

    except Exception as e:
        logger.error(f"Error listing user groups: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list user groups")


@router.get(
    "/{group_id}",
    summary="Get user group",
    description="Get detailed information about a specific user group.",
)
async def get_user_group(
    group_id: str = Path(..., description="Group ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a user group.

    Only administrators can view group details.

    Args:
        group_id: ID of the group to retrieve
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with group information

    Raises:
        HTTPException: If group not found

    """
    logger.info("Getting user group", extra={"group_id": group_id, "requested_by": current_user.id})

    try:
        rbac_service = RBACService(db)
        group = await rbac_service.get_user_group(group_id)

        # Get member count
        members = await rbac_service.list_group_members(group_id)

        response_data = UserGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            is_active=group.is_active,
            created_by=group.created_by,
            created_at=group.created_at,
            updated_at=group.updated_at,
            member_count=len(members),
        )

        return ShuResponse.success(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error getting group: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User group '{group_id}' not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting user group: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get user group")


@router.put("/{group_id}", summary="Update user group", description="Update an existing user group.")
async def update_user_group(
    group_id: str = Path(..., description="Group ID"),
    update_data: UserGroupUpdate = ...,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing user group.

    Only administrators can update user groups.

    Args:
        group_id: ID of the group to update
        update_data: Group update data
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with updated group information

    Raises:
        HTTPException: If group not found or update fails

    """
    logger.info("Updating user group", extra={"group_id": group_id, "updated_by": current_user.id})

    try:
        rbac_service = RBACService(db)
        group = await rbac_service.update_user_group(group_id, update_data)

        # Get member count
        members = await rbac_service.list_group_members(group_id)

        response_data = UserGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            is_active=group.is_active,
            created_by=group.created_by,
            created_at=group.created_at,
            updated_at=group.updated_at,
            member_count=len(members),
        )

        return ShuResponse.success(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error updating group: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User group '{group_id}' not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating user group: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user group")


@router.delete(
    "/{group_id}",
    summary="Delete user group",
    description="Delete a user group and all its memberships.",
)
async def delete_user_group(
    group_id: str = Path(..., description="Group ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user group and all its memberships.

    Only administrators can delete user groups.
    This will also remove all group memberships and group-based permissions.

    Args:
        group_id: ID of the group to delete
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse confirming deletion

    Raises:
        HTTPException: If group not found or deletion fails

    """
    logger.info("Deleting user group", extra={"group_id": group_id, "deleted_by": current_user.id})

    try:
        rbac_service = RBACService(db)

        # Get group name for response message
        group = await rbac_service.get_user_group(group_id)
        group_name = group.name

        await rbac_service.delete_user_group(group_id)

        return ShuResponse.success(data={"deleted_group_id": group_id})

    except RBACServiceError as e:
        logger.error(f"RBAC service error deleting group: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User group '{group_id}' not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting user group: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user group")


# Group Membership Management
@router.post(
    "/{group_id}/members",
    summary="Add user to group",
    description="Add a user to a group with specified role.",
)
async def add_user_to_group(
    group_id: str = Path(..., description="Group ID"),
    membership_data: UserGroupMembershipCreate = ...,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a group.

    Only administrators can manage group memberships.

    Args:
        group_id: ID of the group
        membership_data: Membership creation data
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with membership information

    Raises:
        HTTPException: If group not found or membership creation fails

    """
    logger.info(
        "Adding user to group",
        extra={
            "group_id": group_id,
            "user_id": membership_data.user_id,
            "role": membership_data.role,
            "granted_by": current_user.id,
        },
    )

    try:
        rbac_service = RBACService(db)
        membership = await rbac_service.add_user_to_group(group_id, membership_data, current_user.id)

        # Get user details for response
        user = await rbac_service._get_user(membership_data.user_id)

        response_data = UserGroupMembershipResponse(
            id=membership.id,
            user_id=membership.user_id,
            group_id=membership.group_id,
            role=membership.role,
            is_active=membership.is_active,
            granted_by=membership.granted_by,
            granted_at=membership.granted_at,
            user_email=user.email,
            user_name=user.email,  # Using email as display name for now
        )

        return ShuResponse.success(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error adding user to group: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding user to group: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add user to group")


@router.delete(
    "/{group_id}/members/{user_id}",
    summary="Remove user from group",
    description="Remove a user from a group.",
)
async def remove_user_from_group(
    group_id: str = Path(..., description="Group ID"),
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a group.

    Only administrators can manage group memberships.

    Args:
        group_id: ID of the group
        user_id: ID of the user to remove
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse confirming removal

    Raises:
        HTTPException: If group/user not found or removal fails

    """
    logger.info(
        "Removing user from group",
        extra={"group_id": group_id, "user_id": user_id, "removed_by": current_user.id},
    )

    try:
        rbac_service = RBACService(db)
        await rbac_service.remove_user_from_group(group_id, user_id)

        return ShuResponse.success(data={"removed_user_id": user_id, "group_id": group_id})

    except RBACServiceError as e:
        logger.error(f"RBAC service error removing user from group: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error removing user from group: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove user from group",
        )


@router.get("/{group_id}/members", summary="List group members", description="List all members of a group.")
async def list_group_members(
    group_id: str = Path(..., description="Group ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a group.

    Only administrators can view group memberships.

    Args:
        group_id: ID of the group
        current_user: Current authenticated user (must be admin)
        db: Database session

    Returns:
        JSONResponse with list of group members

    Raises:
        HTTPException: If group not found

    """
    logger.info("Listing group members", extra={"group_id": group_id, "requested_by": current_user.id})

    try:
        rbac_service = RBACService(db)
        memberships = await rbac_service.list_group_members(group_id)

        # Convert to response format
        membership_responses = []
        for membership in memberships:
            membership_responses.append(
                UserGroupMembershipResponse(
                    id=membership.id,
                    user_id=membership.user_id,
                    group_id=membership.group_id,
                    role=membership.role,
                    is_active=membership.is_active,
                    granted_by=membership.granted_by,
                    granted_at=membership.granted_at,
                    user_email=membership.user.email if membership.user else None,
                    user_name=membership.user.email if membership.user else None,
                )
            )

        response_data = UserGroupMembershipListResponse(
            memberships=membership_responses, total_count=len(membership_responses)
        )

        return ShuResponse.success(data=response_data.model_dump())

    except RBACServiceError as e:
        logger.error(f"RBAC service error listing group members: {e}")
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User group '{group_id}' not found")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error listing group members: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list group members")
