"""User Permission Query API endpoints for Shu RAG Backend.

This module provides REST API endpoints for querying user group
memberships.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.response import ShuResponse
from ..models.rbac import UserGroup, UserGroupMembership
from ..schemas.rbac import (
    UserGroupMembershipListResponse,
    UserGroupMembershipResponse,
)
from .dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["user-permissions"])


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
                and_(UserGroupMembership.user_id == user_id, UserGroupMembership.is_active)
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
