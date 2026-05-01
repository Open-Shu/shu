"""Role-Based Access Control for Shu API."""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import get_settings_instance
from ..core.database import get_db
from .jwt_manager import JWTManager
from .models import User, UserRole

security = HTTPBearer()


class RBACController:
    """Role-Based Access Control for Shu API endpoints."""

    def __init__(self) -> None:
        self.jwt_manager = JWTManager()

    async def get_current_user(
        self,
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        """Get current authenticated user from JWT token."""
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
        """Require specific role for endpoint access decorator."""

        async def role_checker(
            credentials: HTTPAuthorizationCredentials = Depends(security),
            db: AsyncSession = Depends(get_db),
        ) -> User:
            current_user = await self.get_current_user(credentials, db)
            if not current_user.has_role(required_role):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required: {required_role.value}",
                )
            return current_user

        return role_checker


# Global RBAC instance
rbac = RBACController()


# Standalone dependency functions for FastAPI endpoints
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
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


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Require admin role for endpoint access - standalone dependency function."""
    current_user = await get_current_user(request, db)
    if not current_user.can_manage_users():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def require_power_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Require power user or admin role - standalone dependency function."""
    current_user = await get_current_user(request, db)
    if not current_user.has_role(UserRole.POWER_USER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Power user access required")
    return current_user


async def require_regular_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Require regular user or higher role - standalone dependency function."""
    current_user = await get_current_user(request, db)
    if not current_user.has_role(UserRole.REGULAR_USER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Regular user access required")
    return current_user


async def require_kb_write_access(
    kb_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require write access to a specific knowledge base.

    Grants via three orthogonal paths:

    - User is ``power_user`` or ``admin`` (write any KB), OR
    - User is the KB owner (``kb.owner_id`` matches ``user.id``), OR
    - PBAC policy grants ``kb.write`` on ``kb:{slug}`` for the user.

    Returns 404 (not 403) on denial to avoid leaking KB existence, matching the
    enforce_pbac convention used elsewhere in the codebase.
    """
    from sqlalchemy import select

    from ..models.knowledge_base import KnowledgeBase
    from ..services.policy_engine import POLICY_CACHE

    current_user = await get_current_user(request, db)

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    result = await db.execute(stmt)
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

    # Fast paths: role and ownership (no extra DB calls)
    if current_user.has_role(UserRole.POWER_USER):
        return current_user
    if kb.owner_id is not None and str(kb.owner_id) == str(current_user.id):
        return current_user

    # PBAC fallback: admin-authored policy may grant kb.write on this KB
    if await POLICY_CACHE.check(str(current_user.id), "kb.write", f"kb:{kb.slug}", db):
        return current_user

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
