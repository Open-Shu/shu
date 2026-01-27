"""
User Service Module

This module contains the UserService class and related helper functions for user
management operations. It is separated from the API layer (api/auth.py) to follow
the separation of concerns principle:

- API Layer (api/auth.py): HTTP wiring, request/response handling, RBAC dependencies
- Service Layer (this module): Business logic for user authentication and management

The UserService handles:
- SSO authentication (Google, Microsoft) via unified authenticate_or_create_sso_user()
- User role determination and activation logic
- ProviderIdentity management for multi-provider support
- User CRUD operations (get, update, delete)

Usage:
    from shu.services.user_service import UserService, get_user_service, create_token_response
    
    # In endpoint with dependency injection:
    async def endpoint(
        user_service: UserService = Depends(get_user_service),
        db: AsyncSession = Depends(get_db)
    ):
        user = await user_service.authenticate_or_create_sso_user(provider_info, db)
        return create_token_response(user, user_service.jwt_manager)
"""

from datetime import datetime, timezone
import logging
from typing import Dict, Any, List

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import User, UserRole, JWTManager
from ..core.config import get_settings_instance
from ..models.provider_identity import ProviderIdentity

logger = logging.getLogger(__name__)


class UserService:
    """Service for user management operations"""

    def __init__(self):
        self.jwt_manager = JWTManager()
        self.settings = get_settings_instance()

    async def get_user_by_id(self, user_id: str, db: AsyncSession) -> User:
        """Get user by ID"""
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_users(self, db: AsyncSession) -> List[User]:
        """Get all users (admin only)"""
        stmt = select(User).order_by(User.is_active.desc(), User.name.asc())
        result = await db.execute(stmt)
        return result.scalars().all()

    async def update_user_role(self, user_id: str, new_role: str, db: AsyncSession) -> User:
        """Update user role (admin only)"""
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        # Validate role
        try:
            UserRole(new_role)
        except ValueError as e:
            raise ValueError("Invalid role") from e

        user.role = new_role
        await db.commit()
        await db.refresh(user)
        return user

    async def delete_user(self, user_id: str, current_user_id: str, db: AsyncSession) -> bool:
        """Delete user (admin only)"""
        # Prevent self-deletion
        if user_id == current_user_id:
            raise ValueError("Cannot delete your own account")

        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        # Delete the user
        await db.delete(user)
        await db.commit()

        logger.info(f"User deleted: {user.email} (ID: {user_id})")
        return True

    def determine_user_role(self, email: str, is_first_user: bool) -> UserRole:
        # Determine user role
        is_admin_email = email.lower() in [
            admin_email.lower()
            for admin_email in self.settings.admin_emails
        ]

        if is_first_user or is_admin_email:
            return UserRole.ADMIN
        else:
            return UserRole.REGULAR_USER

    async def is_first_user(self, db: AsyncSession) -> bool:
        stmt = select(User.id).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none() is None

    def is_active(self, user_role: UserRole, is_first_user: bool) -> bool:
        return is_first_user or user_role == UserRole.ADMIN

    async def get_user_auth_method(self, db: AsyncSession, email: str) -> str:
        auth_method_result = await db.execute(select(User.auth_method).where(User.email == email))
        return auth_method_result.scalar_one_or_none()

    async def authenticate_or_create_sso_user(
        self,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> User:
        """
        Authenticate or create a user from SSO provider info.
        
        This method is provider-agnostic. Any provider-specific logic (like backward
        compatibility for legacy storage) should be handled in the adapter's 
        get_user_info() method before calling this.
        
        Args:
            provider_info: Normalized provider info with keys:
                - provider_id: Provider's unique user identifier
                - provider_key: Provider name ("google" or "microsoft")
                - email: User's email address
                - name: User's display name
                - picture: Avatar URL (optional)
                - existing_user: Optional[User] - Pre-looked-up user from adapter (for backward compat)
            db: Database session
            
        Returns:
            Authenticated User object
            
        Raises:
            HTTPException: 409 if user exists with password auth
            HTTPException: 400 if user account is inactive
            HTTPException: 201 if new user created but requires activation
        """
        email = provider_info["email"]
        provider_id = provider_info["provider_id"]
        provider_key = provider_info["provider_key"]
        
        # Check for password auth conflict
        auth_method = await self.get_user_auth_method(db, email)
        if auth_method == "password":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This account uses password authentication. Please use the username & password login flow."
            )
        
        # Check if adapter already found an existing user (e.g., via legacy google_id lookup)
        existing_user_from_adapter = provider_info.get("existing_user")
        if existing_user_from_adapter:
            if not existing_user_from_adapter.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            # Ensure ProviderIdentity exists (migration on login)
            await self._ensure_provider_identity(existing_user_from_adapter, provider_info, db)
            existing_user_from_adapter.last_login = datetime.now(timezone.utc)
            # Update avatar if provided and different
            new_picture = provider_info.get("picture")
            if new_picture and new_picture != existing_user_from_adapter.picture_url:
                existing_user_from_adapter.picture_url = new_picture
            await db.commit()
            return existing_user_from_adapter
        
        # Look up existing identity in ProviderIdentity table
        user = await self._get_user_by_identity(provider_key, provider_id, db)
        if user:
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            user.last_login = datetime.now(timezone.utc)
            # Update avatar if provided and different
            new_picture = provider_info.get("picture")
            if new_picture and new_picture != user.picture_url:
                user.picture_url = new_picture
            await db.commit()
            return user
        
        # Check if user exists by email (link identity to existing user)
        email_stmt = select(User).where(User.email == email)
        email_result = await db.execute(email_stmt)
        existing_user = email_result.scalar_one_or_none()
        
        if existing_user:
            if not existing_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User account is inactive. Please contact an administrator for activation."
                )
            await self._create_provider_identity(existing_user, provider_info, db)
            existing_user.last_login = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"Linked {provider_key} identity to existing user", extra={"email": email})
            return existing_user
        
        # Create new user
        return await self._create_new_sso_user(provider_info, db)

    async def _get_user_by_identity(
        self,
        provider_key: str,
        provider_id: str,
        db: AsyncSession
    ) -> User | None:
        """Get user from ProviderIdentity table using a single JOIN query.
        
        Args:
            provider_key: Provider name ("google" or "microsoft")
            provider_id: Provider's unique user identifier
            db: Database session
            
        Returns:
            User if found via ProviderIdentity, None otherwise
        """
        from sqlalchemy.orm import joinedload
        
        stmt = (
            select(User)
            .join(ProviderIdentity, ProviderIdentity.user_id == User.id)
            .where(
                ProviderIdentity.provider_key == provider_key,
                ProviderIdentity.account_id == provider_id
            )
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
        return user

    async def _ensure_provider_identity(
        self,
        user: User,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> None:
        """Ensure ProviderIdentity exists for user, create if missing (migration on login).
        
        Args:
            user: The user to ensure identity for
            provider_info: Normalized provider info dict
            db: Database session
        """
        stmt = select(ProviderIdentity).where(
            ProviderIdentity.user_id == user.id,
            ProviderIdentity.provider_key == provider_info["provider_key"],
            ProviderIdentity.account_id == provider_info["provider_id"]
        )
        result = await db.execute(stmt)
        if not result.scalar_one_or_none():
            await self._create_provider_identity(user, provider_info, db)

    async def _create_provider_identity(
        self,
        user: User,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> ProviderIdentity:
        """Create ProviderIdentity linking user to provider.
        
        Args:
            user: The user to link
            provider_info: Normalized provider info dict
            db: Database session
            
        Returns:
            The created ProviderIdentity
        """
        identity = ProviderIdentity(
            user_id=user.id,
            provider_key=provider_info["provider_key"],
            account_id=provider_info["provider_id"],
            primary_email=provider_info["email"],
            display_name=provider_info["name"],
            avatar_url=provider_info.get("picture"),
        )
        db.add(identity)
        await db.flush()
        return identity

    async def _create_new_sso_user(
        self,
        provider_info: Dict[str, Any],
        db: AsyncSession
    ) -> User:
        """Create new user from SSO provider info.
        
        Args:
            provider_info: Normalized provider info dict
            db: Database session
            
        Returns:
            The created User
            
        Raises:
            HTTPException: 201 if user requires activation
        """
        email = provider_info["email"]
        provider_key = provider_info["provider_key"]
        
        is_first_user = await self.is_first_user(db)
        user_role = self.determine_user_role(email, is_first_user)
        is_active = self.is_active(user_role, is_first_user)
        
        user = User(
            email=email,
            name=provider_info["name"],
            picture_url=provider_info.get("picture"),
            role=user_role.value,
            auth_method=provider_key,
            is_active=is_active,
            last_login=datetime.now(timezone.utc) if is_active else None
        )
        
        if user_role == UserRole.ADMIN:
            if is_first_user:
                logger.info(f"Creating first user as admin via {provider_key}", extra={"email": email})
            else:
                logger.info(f"Creating admin user from configured list via {provider_key}", extra={"email": email})
        
        db.add(user)
        await db.flush()  # Get user.id without committing
        
        # Create ProviderIdentity
        await self._create_provider_identity(user, provider_info, db)
        await db.commit()
        await db.refresh(user)
        
        if not is_active:
            raise HTTPException(
                status_code=status.HTTP_201_CREATED,
                detail="Account was created, but will need to be activated. Please contact an administrator for activation."
            )
        
        return user


# Dependency provider for UserService
def get_user_service() -> UserService:
    """Dependency provider for UserService.
    
    Use with FastAPI's Depends() for proper dependency injection:
    
        async def endpoint(
            user_service: UserService = Depends(get_user_service)
        ):
            ...
    """
    return UserService()


def create_token_response(user: User, jwt_manager: JWTManager):
    """Create JWT token response for authenticated user.
    
    Args:
        user: The authenticated user
        jwt_manager: JWTManager instance for token creation
        
    Returns:
        Dict with access_token, refresh_token, token_type, and user data
        
    Note:
        Returns a dict rather than TokenResponse to avoid circular import with
        api/auth.py where TokenResponse is defined. The endpoint will construct
        the TokenResponse from this dict.
    """
    access_token = jwt_manager.create_access_token(user.to_dict())
    refresh_token = jwt_manager.create_refresh_token(user.id)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user.to_dict()
    }
