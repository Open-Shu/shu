"""FastAPI auth dependency chain for SHU-761 tenant isolation.

Three dependencies chained in this order at request time:

1. ``decode_credential`` — parses ``Authorization``, returns a credential
   identifier. No DB.
2. ``resolve_tenant`` — branches per deployment_mode; sets ``tenant_context``
   via a yield dependency that resets on response.
3. ``fetch_user`` — reads ``users`` through the now-RLS-satisfied request
   session. Aliased as ``get_current_user`` for the existing route imports
   so endpoint handlers don't change.

The split exists so the ``users`` read in step 3 happens *after* tenant
resolution. Reading ``users`` before context is set would return zero rows
under RLS (default-deny), and the previous monolithic ``get_current_user``
read the row at the wrong moment for the new ordering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import get_settings_instance
from ..core.database import get_db
from ..core.tenant import tenant_context_for_email, tenant_context_for_user_id
from .jwt_manager import JWTManager
from .models import User

CredentialSource = Literal["jwt", "api_key"]


@dataclass(frozen=True)
class CredentialResolution:
    """Output of ``decode_credential``: how the request authenticated + identifier.

    Exactly one of ``user_id`` or ``email`` is populated. JWT carries the
    user_id directly. The global API-key path (the only API-key mode in this
    codebase) maps to a configured email — Shu has no per-user ``api_keys``
    table, so the user_id is unknown until the ``users`` row is read.
    """

    source: CredentialSource
    user_id: str | None = None
    email: str | None = None


def _decode_jwt(token: str) -> str:
    """Return user_id from a valid JWT, or raise 401."""
    user_data = JWTManager().extract_user_from_token(token)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_data["user_id"]


def _validate_api_key(provided_key: str) -> str:
    """Return the configured email a valid global API key maps to, or raise 401."""
    settings = get_settings_instance()
    if not settings.api_key or provided_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not settings.api_key_user_email:
        # Misconfiguration, not user-supplied — surface as 401 to avoid leaking
        # the distinction between "bad key" and "key mapped to nothing".
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key user mapping not configured",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return settings.api_key_user_email


async def decode_credential(request: Request) -> CredentialResolution:
    """Parse ``Authorization`` and return a verified credential identifier.

    No DB reads — that's the whole point. Tenant resolution and user fetch
    are downstream dependencies.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )

    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        return CredentialResolution(source="jwt", user_id=_decode_jwt(token))

    if auth_header.startswith("ApiKey "):
        provided_key = auth_header.split(" ", 1)[1]
        return CredentialResolution(source="api_key", email=_validate_api_key(provided_key))

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unsupported Authorization scheme",
    )


async def resolve_tenant(
    cred: CredentialResolution = Depends(decode_credential),
) -> AsyncIterator[str]:
    """Yield-dependency: set ``tenant_context`` for the request, reset on response.

    Branches per deployment_mode inside the contextmanager helpers — self-hosted
    and silo short-circuit to a config constant; multi-tenant calls the
    appropriate SECURITY DEFINER lookup.
    """
    cm = (
        tenant_context_for_user_id(cred.user_id) if cred.user_id is not None else tenant_context_for_email(cred.email)  # type: ignore[arg-type]
    )
    async with cm as tid:
        yield tid


async def fetch_user(
    cred: CredentialResolution = Depends(decode_credential),
    _tid: str = Depends(resolve_tenant),  # ordering: tenant_context set before this runs
    db: AsyncSession = Depends(get_db),
) -> User:
    """Return the authenticated user row, read under RLS-active tenant context."""
    if cred.user_id is not None:
        stmt = select(User).where(User.id == cred.user_id).options(selectinload(User.preferences))
    else:
        # API key path — look up by configured email under the now-active tenant.
        stmt = select(User).where(User.email == cred.email).options(selectinload(User.preferences))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None or not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer" if cred.source == "jwt" else "ApiKey"},
        )
    return user


# Alias kept so existing `from ..auth.rbac import get_current_user` imports
# continue to work via re-export from rbac.py.
get_current_user = fetch_user
