"""FastAPI dependencies for Shu RAG Backend.

This module provides reusable dependencies for database sessions,
authentication, and other common requirements.
"""

import logging
from collections.abc import AsyncGenerator

from fastapi import HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings_instance
from ..core.database import get_db as core_get_db

logger = logging.getLogger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Database session dependency.

    Yields an async database session and ensures it's closed after use.
    """
    # Use the core database session management which has proper error handling
    async for session in core_get_db():
        yield session


def get_request_id(request) -> str:
    """Extract request ID from request state."""
    return getattr(request.state, "request_id", "unknown")


def paginate(skip: int = 0, limit: int = Query(100, ge=1, description="Number of items to return")) -> dict:
    """Validate and return pagination parameters for endpoints.

    Raises HTTP 400 if `skip` is negative or if `limit` is not between 1 and the configured maximum.

    Parameters
    ----------
        skip (int): Number of items to skip; must be greater than or equal to 0.
        limit (int): Maximum number of items to return; must be between 1 and the configured max pagination limit.

    Returns
    -------
        dict: A dictionary with keys `"skip"` and `"limit"` containing the validated pagination values.

    """
    settings = get_settings_instance()

    if skip < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skip parameter must be non-negative")

    if limit <= 0 or limit > settings.max_pagination_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Limit parameter must be between 1 and {settings.max_pagination_limit}",
        )

    return {"skip": skip, "limit": limit}
