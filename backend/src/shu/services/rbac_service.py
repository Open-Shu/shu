"""
RBAC Service Layer

This module provides service layer functionality for managing RBAC operations
including user groups, permissions, and access control.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, delete
from sqlalchemy.orm import selectinload
import logging

from ..core.exceptions import ShuException
from ..models.rbac import (
    UserGroup, UserGroupMembership, KnowledgeBasePermission, 
    PermissionLevel, GroupRole
)
from ..models.knowledge_base import KnowledgeBase
from ..auth.models import User
from ..schemas.rbac import (
    UserGroupCreate, UserGroupUpdate, UserGroupMembershipCreate,
    KnowledgeBasePermissionCreate, KnowledgeBasePermissionUpdate,
    EffectivePermissionResponse
)

logger = logging.getLogger(__name__)


class RBACServiceError(ShuException):
    """Base exception for RBAC service errors."""
    pass


class GroupNotFoundError(RBACServiceError):
    """Raised when a user group is not found."""
    def __init__(self, group_id: str):
        super().__init__(f"User group '{group_id}' not found", "GROUP_NOT_FOUND")


class PermissionNotFoundError(RBACServiceError):
    """Raised when a permission is not found."""
    def __init__(self, permission_id: str):
        super().__init__(f"Permission '{permission_id}' not found", "PERMISSION_NOT_FOUND")


class DuplicateGroupError(RBACServiceError):
    """Raised when trying to create a group with duplicate name."""
    def __init__(self, group_name: str):
        super().__init__(f"Group with name '{group_name}' already exists", "DUPLICATE_GROUP")


class DuplicatePermissionError(RBACServiceError):
    """Raised when trying to create duplicate permission."""
    def __init__(self, target_type: str, target_id: str, kb_id: str):
        super().__init__(
            f"Permission already exists for {target_type} '{target_id}' on KB '{kb_id}'", 
            "DUPLICATE_PERMISSION"
        )


class RBACService:
    """Service for managing RBAC operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # User Group Management
    async def create_user_group(self, group_data: UserGroupCreate, created_by: str) -> UserGroup:
        """Create a new user group."""
        try:
            # Check for duplicate name
            existing_result = await self.db.execute(
                select(UserGroup).where(UserGroup.name == group_data.name)
            )
            if existing_result.scalar_one_or_none():
                raise DuplicateGroupError(group_data.name)

            # Create new group
            group = UserGroup(
                **group_data.model_dump(),
                created_by=created_by
            )
            self.db.add(group)
            await self.db.commit()
            await self.db.refresh(group)

            logger.info(f"Created user group: {group.name} by user {created_by}")
            return group

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to create user group: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to create user group: {str(e)}", "GROUP_CREATE_ERROR")

    async def get_user_group(self, group_id: str) -> UserGroup:
        """Get a user group by ID."""
        result = await self.db.execute(
            select(UserGroup).where(UserGroup.id == group_id)
        )
        group = result.scalar_one_or_none()
        if not group:
            raise GroupNotFoundError(group_id)
        return group

    async def list_user_groups(
        self,
        page: int = 1,
        page_size: int = 50,
        active_only: bool = True
    ) -> Tuple[List[UserGroup], int]:
        """List user groups with pagination."""
        # Build base query
        base_query = select(UserGroup)
        if active_only:
            base_query = base_query.where(UserGroup.is_active == True)

        # Get total count with a separate, simpler query
        count_query = select(func.count(UserGroup.id))
        if active_only:
            count_query = count_query.where(UserGroup.is_active == True)

        count_result = await self.db.execute(count_query)
        total_count = count_result.scalar()

        # Get paginated results
        paginated_query = (
            base_query
                .order_by(UserGroup.is_active.desc(), UserGroup.name)
                .offset((page - 1) * page_size)
                .limit(page_size)
        )
        result = await self.db.execute(paginated_query)
        groups = result.scalars().all()

        return list(groups), total_count

    async def update_user_group(self, group_id: str, update_data: UserGroupUpdate) -> UserGroup:
        """Update a user group."""
        try:
            group = await self.get_user_group(group_id)
            
            # Check for duplicate name if name is being updated
            if update_data.name and update_data.name != group.name:
                existing_result = await self.db.execute(
                    select(UserGroup).where(
                        and_(
                            UserGroup.name == update_data.name,
                            UserGroup.id != group_id
                        )
                    )
                )
                if existing_result.scalar_one_or_none():
                    raise DuplicateGroupError(update_data.name)

            # Update fields
            for field, value in update_data.model_dump(exclude_unset=True).items():
                setattr(group, field, value)

            await self.db.commit()
            await self.db.refresh(group)

            logger.info(f"Updated user group: {group.name}")
            return group

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to update user group: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to update user group: {str(e)}", "GROUP_UPDATE_ERROR")

    async def delete_user_group(self, group_id: str) -> None:
        """Delete a user group and all its memberships/permissions."""
        try:
            group = await self.get_user_group(group_id)
            
            # Delete all memberships and permissions (cascade will handle this)
            await self.db.delete(group)
            await self.db.commit()

            logger.info(f"Deleted user group: {group.name}")

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to delete user group: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to delete user group: {str(e)}", "GROUP_DELETE_ERROR")

    # Group Membership Management
    async def add_user_to_group(
        self, 
        group_id: str, 
        membership_data: UserGroupMembershipCreate,
        granted_by: str
    ) -> UserGroupMembership:
        """Add a user to a group."""
        try:
            # Verify group exists
            await self.get_user_group(group_id)
            
            # Check if membership already exists
            existing_result = await self.db.execute(
                select(UserGroupMembership).where(
                    and_(
                        UserGroupMembership.user_id == membership_data.user_id,
                        UserGroupMembership.group_id == group_id
                    )
                )
            )
            existing_membership = existing_result.scalar_one_or_none()
            
            if existing_membership:
                # Reactivate if inactive
                if not existing_membership.is_active:
                    existing_membership.is_active = True
                    existing_membership.role = membership_data.role
                    existing_membership.granted_by = granted_by
                    existing_membership.granted_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    await self.db.refresh(existing_membership)
                    return existing_membership
                else:
                    raise RBACServiceError(
                        f"User is already a member of group '{group_id}'", 
                        "DUPLICATE_MEMBERSHIP"
                    )

            # Create new membership
            membership = UserGroupMembership(
                user_id=membership_data.user_id,
                group_id=group_id,
                role=membership_data.role,
                granted_by=granted_by
            )
            self.db.add(membership)
            await self.db.commit()
            await self.db.refresh(membership)

            logger.info(f"Added user {membership_data.user_id} to group {group_id}")
            return membership

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to add user to group: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to add user to group: {str(e)}", "MEMBERSHIP_CREATE_ERROR")

    async def remove_user_from_group(self, group_id: str, user_id: str) -> None:
        """Remove a user from a group."""
        try:
            result = await self.db.execute(
                select(UserGroupMembership).where(
                    and_(
                        UserGroupMembership.user_id == user_id,
                        UserGroupMembership.group_id == group_id,
                        UserGroupMembership.is_active == True
                    )
                )
            )
            membership = result.scalar_one_or_none()

            if not membership:
                raise RBACServiceError(
                    f"User '{user_id}' is not a member of group '{group_id}'",
                    "MEMBERSHIP_NOT_FOUND"
                )

            # Deactivate membership instead of deleting for audit trail
            membership.is_active = False
            await self.db.commit()

            logger.info(f"Removed user {user_id} from group {group_id}")

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to remove user from group: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to remove user from group: {str(e)}", "MEMBERSHIP_REMOVE_ERROR")

    async def list_group_members(self, group_id: str) -> List[UserGroupMembership]:
        """List all members of a group."""
        await self.get_user_group(group_id)  # Verify group exists

        result = await self.db.execute(
            select(UserGroupMembership)
            .options(selectinload(UserGroupMembership.user))
            .where(
                and_(
                    UserGroupMembership.group_id == group_id,
                    UserGroupMembership.is_active == True
                )
            )
        )
        return list(result.scalars().all())

    # Knowledge Base Permission Management
    async def grant_kb_permission(
        self,
        kb_id: str,
        permission_data: KnowledgeBasePermissionCreate,
        granted_by: str
    ) -> KnowledgeBasePermission:
        """Grant a knowledge base permission."""
        try:
            # Verify KB exists
            kb_result = await self.db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            if not kb_result.scalar_one_or_none():
                raise RBACServiceError(f"Knowledge base '{kb_id}' not found", "KB_NOT_FOUND")

            # Check for duplicate permission
            existing_result = await self.db.execute(
                select(KnowledgeBasePermission).where(
                    and_(
                        KnowledgeBasePermission.knowledge_base_id == kb_id,
                        KnowledgeBasePermission.user_id == permission_data.user_id,
                        KnowledgeBasePermission.group_id == permission_data.group_id
                    )
                )
            )
            existing_permission = existing_result.scalar_one_or_none()

            if existing_permission:
                if existing_permission.is_active:
                    target_type = "user" if permission_data.user_id else "group"
                    target_id = permission_data.user_id or permission_data.group_id
                    raise DuplicatePermissionError(target_type, target_id, kb_id)
                else:
                    # Reactivate existing permission
                    existing_permission.is_active = True
                    existing_permission.permission_level = permission_data.permission_level
                    existing_permission.expires_at = permission_data.expires_at
                    existing_permission.granted_by = granted_by
                    existing_permission.granted_at = datetime.now(timezone.utc)
                    await self.db.commit()
                    await self.db.refresh(existing_permission)
                    return existing_permission

            # Create new permission
            permission = KnowledgeBasePermission(
                knowledge_base_id=kb_id,
                user_id=permission_data.user_id,
                group_id=permission_data.group_id,
                permission_level=permission_data.permission_level,
                expires_at=permission_data.expires_at,
                granted_by=granted_by
            )
            self.db.add(permission)
            await self.db.commit()
            await self.db.refresh(permission)

            target_type = "user" if permission_data.user_id else "group"
            target_id = permission_data.user_id or permission_data.group_id
            logger.info(f"Granted {permission_data.permission_level} permission to {target_type} {target_id} for KB {kb_id}")
            return permission

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to grant KB permission: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to grant KB permission: {str(e)}", "PERMISSION_GRANT_ERROR")

    async def revoke_kb_permission(self, permission_id: str) -> None:
        """Revoke a knowledge base permission."""
        try:
            result = await self.db.execute(
                select(KnowledgeBasePermission).where(KnowledgeBasePermission.id == permission_id)
            )
            permission = result.scalar_one_or_none()

            if not permission:
                raise PermissionNotFoundError(permission_id)

            # Deactivate permission instead of deleting for audit trail
            permission.is_active = False
            await self.db.commit()

            logger.info(f"Revoked permission {permission_id}")

        except Exception as e:
            await self.db.rollback()
            if isinstance(e, RBACServiceError):
                raise
            logger.error(f"Failed to revoke KB permission: {e}", exc_info=True)
            raise RBACServiceError(f"Failed to revoke KB permission: {str(e)}", "PERMISSION_REVOKE_ERROR")

    async def list_kb_permissions(self, kb_id: str) -> List[KnowledgeBasePermission]:
        """List all permissions for a knowledge base."""
        result = await self.db.execute(
            select(KnowledgeBasePermission)
            .options(
                selectinload(KnowledgeBasePermission.user),
                selectinload(KnowledgeBasePermission.group),
                selectinload(KnowledgeBasePermission.granter)
            )
            .where(
                and_(
                    KnowledgeBasePermission.knowledge_base_id == kb_id,
                    KnowledgeBasePermission.is_active == True
                )
            )
        )
        return list(result.scalars().all())

    async def get_effective_permission(self, user_id: str, kb_id: str) -> Optional[EffectivePermissionResponse]:
        """Get the effective permission level for a user on a knowledge base."""
        from ..auth.rbac import rbac

        # Use the existing RBAC service to get permission level
        # We need a mock db session for this call
        permission_level = await rbac.get_kb_permission_level(
            user=await self._get_user(user_id),
            kb_id=kb_id,
            db=self.db
        )

        if not permission_level:
            return None

        # Determine source of permission
        # This is a simplified version - in practice you'd want to track the exact source
        return EffectivePermissionResponse(
            user_id=user_id,
            knowledge_base_id=kb_id,
            effective_level=PermissionLevel(permission_level),
            source="computed",  # Would need more logic to determine exact source
            source_id=None,
            expires_at=None
        )

    async def _get_user(self, user_id: str) -> User:
        """Helper method to get a user by ID."""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise RBACServiceError(f"User '{user_id}' not found", "USER_NOT_FOUND")
        return user
