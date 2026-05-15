"""Role-Based Access Control for Shu API.

After SHU-761, the authenticated-user lookup lives in :mod:`shu.auth.dependencies`
where it sits behind the tenant-resolution dependency chain. This module
re-exports ``get_current_user`` for backward compatibility with the existing
``from ..auth.rbac import get_current_user`` imports across the api/ package,
and provides the higher-level role gates (``require_admin``, etc.) on top of it.
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_db
from .dependencies import fetch_user, get_current_user  # noqa: F401 - public re-export
from .models import User, UserRole


async def require_admin(current_user: User = Depends(fetch_user)) -> User:
    """Require admin role for endpoint access."""
    if not current_user.can_manage_users():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def require_power_user(current_user: User = Depends(fetch_user)) -> User:
    """Require power user or admin role."""
    if not current_user.has_role(UserRole.POWER_USER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Power user access required")
    return current_user


async def require_regular_user(current_user: User = Depends(fetch_user)) -> User:
    """Require regular user or higher role."""
    if not current_user.has_role(UserRole.REGULAR_USER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Regular user access required")
    return current_user


async def require_kb_write_access(
    kb_id: str,
    current_user: User = Depends(fetch_user),
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
    from ..models.knowledge_base import KnowledgeBase
    from ..services.policy_engine import POLICY_CACHE

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    result = await db.execute(stmt)
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

    if current_user.has_role(UserRole.POWER_USER):
        return current_user
    if kb.owner_id is not None and str(kb.owner_id) == str(current_user.id):
        return current_user
    if await POLICY_CACHE.check(str(current_user.id), "kb.write", f"kb:{kb.slug}", db):
        return current_user

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
