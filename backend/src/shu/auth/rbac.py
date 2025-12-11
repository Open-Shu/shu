"""Role-Based Access Control for Shu API"""

from fastapi import HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import Optional, List
import logging

from .models import User, UserRole
from .jwt_manager import JWTManager
from ..core.database import get_db
from ..core.config import get_settings_instance

logger = logging.getLogger(__name__)
security = HTTPBearer()

class RBACController:
    """Role-Based Access Control for Shu API endpoints"""

    def __init__(self):
        self.jwt_manager = JWTManager()

    async def get_current_user(
        self,
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        """Get current authenticated user from JWT token"""
        token = credentials.credentials

        # Verify and decode the token
        user_data = self.jwt_manager.extract_user_from_token(token)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Get user from database
        from sqlalchemy import select
        stmt = select(User).where(User.id == user_data["user_id"])
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

    def require_role(self, required_role: UserRole):
        """Decorator factory to require specific role for endpoint access"""
        async def role_checker(
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: AsyncSession = Depends(get_db)
        ) -> User:
            current_user = await self.get_current_user(credentials, db)
            if not current_user.has_role(required_role):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required: {required_role.value}"
                )
            return current_user
        return role_checker
    
    async def can_access_knowledge_base(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """
        Check if user can access specific knowledge base using granular RBAC.

        Uses database-driven granular access control with user groups and permissions
        to prevent RBAC bypass vulnerabilities.

        Args:
            user: User requesting access
            kb_id: Knowledge base ID to check access for
            db: Database session for access validation

        Returns:
            True if user has access, False otherwise
        """
        from sqlalchemy import select, or_, and_
        from ..models.knowledge_base import KnowledgeBase
        from ..models.rbac import KnowledgeBasePermission, UserGroupMembership

        # Admins have access to all knowledge bases
        if user.role_enum == UserRole.ADMIN:
            return True

        # Verify knowledge base exists first
        kb_result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        knowledge_base = kb_result.scalar_one_or_none()

        if not knowledge_base:
            # Knowledge base doesn't exist - deny access
            return False

        # Check if user is the owner of the knowledge base
        if knowledge_base.owner_id == user.id:
            return True

        # Check for direct user permissions
        direct_permission_result = await db.execute(
            select(KnowledgeBasePermission).where(
                and_(
                    KnowledgeBasePermission.knowledge_base_id == kb_id,
                    KnowledgeBasePermission.user_id == user.id,
                    KnowledgeBasePermission.is_active == True
                )
            )
        )
        direct_permission = direct_permission_result.scalar_one_or_none()

        if direct_permission:
            # Check if permission has expired
            if not direct_permission.is_expired():
                return True

        # Check for group-based permissions
        # Get all active groups the user belongs to
        user_groups_result = await db.execute(
            select(UserGroupMembership.group_id).where(
                and_(
                    UserGroupMembership.user_id == user.id,
                    UserGroupMembership.is_active == True
                )
            )
        )
        user_group_ids = [row[0] for row in user_groups_result.fetchall()]

        if user_group_ids:
            # Check if any of the user's groups have permission to this KB
            group_permission_result = await db.execute(
                select(KnowledgeBasePermission).where(
                    and_(
                        KnowledgeBasePermission.knowledge_base_id == kb_id,
                        KnowledgeBasePermission.group_id.in_(user_group_ids),
                        KnowledgeBasePermission.is_active == True
                    )
                )
            )
            group_permissions = group_permission_result.fetchall()

            for permission in group_permissions:
                # Check if permission has expired
                if not permission[0].is_expired():
                    return True

        # No permissions found - deny access
        return False

    async def get_kb_permission_level(self, user: User, kb_id: str, db: AsyncSession) -> Optional[str]:
        """
        Get the highest permission level a user has for a specific knowledge base.

        Args:
            user: User to check permissions for
            kb_id: Knowledge base ID to check
            db: Database session

        Returns:
            Permission level string (owner/admin/member/read_only) or None if no access
        """
        from sqlalchemy import select, and_
        from ..models.knowledge_base import KnowledgeBase
        from ..models.rbac import KnowledgeBasePermission, UserGroupMembership, PermissionLevel

        # Admins have owner-level access to all knowledge bases
        if user.role_enum == UserRole.ADMIN:
            return PermissionLevel.OWNER.value

        # Verify knowledge base exists first
        kb_result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        knowledge_base = kb_result.scalar_one_or_none()

        if not knowledge_base:
            return None

        # Check if user is the owner of the knowledge base
        if knowledge_base.owner_id == user.id:
            return PermissionLevel.OWNER.value

        permission_levels = []

        # Check for direct user permissions
        direct_permission_result = await db.execute(
            select(KnowledgeBasePermission).where(
                and_(
                    KnowledgeBasePermission.knowledge_base_id == kb_id,
                    KnowledgeBasePermission.user_id == user.id,
                    KnowledgeBasePermission.is_active == True
                )
            )
        )
        direct_permission = direct_permission_result.scalar_one_or_none()

        if direct_permission and not direct_permission.is_expired():
            permission_levels.append(direct_permission.permission_level)

        # Check for group-based permissions
        user_groups_result = await db.execute(
            select(UserGroupMembership.group_id).where(
                and_(
                    UserGroupMembership.user_id == user.id,
                    UserGroupMembership.is_active == True
                )
            )
        )
        user_group_ids = [row[0] for row in user_groups_result.fetchall()]

        if user_group_ids:
            group_permission_result = await db.execute(
                select(KnowledgeBasePermission).where(
                    and_(
                        KnowledgeBasePermission.knowledge_base_id == kb_id,
                        KnowledgeBasePermission.group_id.in_(user_group_ids),
                        KnowledgeBasePermission.is_active == True
                    )
                )
            )
            group_permissions = group_permission_result.fetchall()

            for permission in group_permissions:
                if not permission[0].is_expired():
                    permission_levels.append(permission[0].permission_level)

        if not permission_levels:
            return None

        # Return the highest permission level
        # Priority: owner > admin > member > read_only
        if PermissionLevel.OWNER.value in permission_levels:
            return PermissionLevel.OWNER.value
        elif PermissionLevel.ADMIN.value in permission_levels:
            return PermissionLevel.ADMIN.value
        elif PermissionLevel.MEMBER.value in permission_levels:
            return PermissionLevel.MEMBER.value
        elif PermissionLevel.READ_ONLY.value in permission_levels:
            return PermissionLevel.READ_ONLY.value

        return None

    async def can_manage_kb(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """Check if user can manage knowledge base (owner/admin level access)."""
        permission_level = await self.get_kb_permission_level(user, kb_id, db)
        from ..models.rbac import PermissionLevel
        return permission_level in [PermissionLevel.OWNER.value, PermissionLevel.ADMIN.value]

    async def can_modify_kb(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """Check if user can modify knowledge base content (owner/admin/member level access)."""
        permission_level = await self.get_kb_permission_level(user, kb_id, db)
        from ..models.rbac import PermissionLevel
        return permission_level in [PermissionLevel.OWNER.value, PermissionLevel.ADMIN.value, PermissionLevel.MEMBER.value]

    async def can_query_kb(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """Check if user can query knowledge base (any level access)."""
        permission_level = await self.get_kb_permission_level(user, kb_id, db)
        return permission_level is not None

    async def can_delete_kb(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """Check if user can delete knowledge base (owner level access only)."""
        permission_level = await self.get_kb_permission_level(user, kb_id, db)
        from ..models.rbac import PermissionLevel
        return permission_level == PermissionLevel.OWNER.value

    async def can_manage_kb_permissions(self, user: User, kb_id: str, db: AsyncSession) -> bool:
        """Check if user can manage knowledge base permissions (owner/admin level access)."""
        permission_level = await self.get_kb_permission_level(user, kb_id, db)
        from ..models.rbac import PermissionLevel
        return permission_level in [PermissionLevel.OWNER.value, PermissionLevel.ADMIN.value]

# Global RBAC instance
rbac = RBACController()

# Standalone dependency functions for FastAPI endpoints
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user - supports JWT or global API key (Tier 0)."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )

    # Global API key support (Authorization: ApiKey <key>)
    if auth_header.startswith("ApiKey "):
        settings = get_settings_instance()
        provided_key = auth_header.split(" ", 1)[1]
        if not settings.api_key or provided_key != settings.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if not settings.api_key_user_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key user mapping not configured",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        # Load mapped user from database
        from sqlalchemy import select
        stmt = select(User).where(User.email == settings.api_key_user_email).options(selectinload(User.preferences))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user or not getattr(user, "is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key mapped user not found or inactive",
            )
        return user

    # JWT support (Authorization: Bearer <jwt>)
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        jwt_manager = JWTManager()
        user_data = jwt_manager.extract_user_from_token(token)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        from sqlalchemy import select
        stmt = select(User).where(User.id == user_data["user_id"]).options(selectinload(User.preferences))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    # Unsupported scheme
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unsupported Authorization scheme",
    )

async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Require admin role for endpoint access - standalone dependency function"""
    current_user = await get_current_user(request, db)
    if not current_user.can_manage_users():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

async def require_power_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Require power user or admin role - standalone dependency function"""
    current_user = await get_current_user(request, db)
    if not current_user.has_role(UserRole.POWER_USER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Power user access required"
        )
    return current_user

async def require_regular_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Require regular user or higher role - standalone dependency function"""
    current_user = await get_current_user(request, db)
    if not current_user.has_role(UserRole.REGULAR_USER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Regular user access required"
        )
    return current_user


def require_kb_access(kb_id_param: str = "kb_id"):
    """
    Dependency factory to require knowledge base access.

    Creates a dependency that validates user access to a specific knowledge base.
    Prevents RBAC bypass vulnerabilities by enforcing database-driven access control.

    Args:
        kb_id_param: Name of the path parameter containing the KB ID

    Returns:
        FastAPI dependency function that validates KB access
    """
    async def kb_access_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        # Extract KB ID from path parameters
        kb_id = request.path_params.get(kb_id_param)
        if not kb_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Knowledge base ID parameter '{kb_id_param}' is required"
            )

        # Check if user has access to this knowledge base
        has_access = await rbac.can_access_knowledge_base(current_user, kb_id, db)
        if not has_access:
            logger.warning(
                f"Access denied: user {current_user.email} ({current_user.role}) "
                f"attempted to access knowledge base {kb_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to knowledge base '{kb_id}'"
            )

        logger.debug(
            f"KB access granted: user {current_user.email} accessing KB {kb_id}"
        )
        return current_user

    return kb_access_checker


# Granular KB permission dependency factories
def require_kb_manage_access(kb_id_param: str = "kb_id"):
    """Require owner/admin level access to knowledge base."""
    async def kb_manage_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        kb_id = request.path_params.get(kb_id_param)
        if not kb_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Knowledge base ID parameter '{kb_id_param}' is required"
            )

        can_manage = await rbac.can_manage_kb(current_user, kb_id, db)
        if not can_manage:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions to manage knowledge base '{kb_id}'"
            )
        return current_user
    return kb_manage_checker

def require_kb_modify_access(kb_id_param: str = "kb_id"):
    """Require owner/admin/member level access to knowledge base."""
    async def kb_modify_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        kb_id = request.path_params.get(kb_id_param)
        if not kb_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Knowledge base ID parameter '{kb_id_param}' is required"
            )

        can_modify = await rbac.can_modify_kb(current_user, kb_id, db)
        if not can_modify:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions to modify knowledge base '{kb_id}'"
            )
        return current_user
    return kb_modify_checker

def require_kb_query_access(kb_id_param: str = "kb_id"):
    """Require any level access to knowledge base for querying."""
    async def kb_query_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        kb_id = request.path_params.get(kb_id_param)
        if not kb_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Knowledge base ID parameter '{kb_id_param}' is required"
            )

        can_query = await rbac.can_query_kb(current_user, kb_id, db)
        if not can_query:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to knowledge base '{kb_id}'"
            )
        return current_user
    return kb_query_checker

def require_kb_delete_access(kb_id_param: str = "kb_id"):
    """Require owner level access to delete knowledge base."""
    async def kb_delete_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        kb_id = request.path_params.get(kb_id_param)
        if not kb_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Knowledge base ID parameter '{kb_id_param}' is required"
            )

        can_delete = await rbac.can_delete_kb(current_user, kb_id, db)
        if not can_delete:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions to delete knowledge base '{kb_id}'. Owner access required."
            )
        return current_user
    return kb_delete_checker

# Convenience dependencies for common cases
require_kb_access_default = require_kb_access("kb_id")
require_kb_access_knowledge_base_id = require_kb_access("knowledge_base_id")

# Granular permission dependencies
require_kb_manage_default = require_kb_manage_access("kb_id")
require_kb_modify_default = require_kb_modify_access("kb_id")
require_kb_query_default = require_kb_query_access("kb_id")
require_kb_delete_default = require_kb_delete_access("kb_id")
