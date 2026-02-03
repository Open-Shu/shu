"""User Preferences API endpoints.

Provides REST API for managing legitimate user preferences including:
- Memory settings (depth, similarity threshold)
- UI/UX preferences (theme, language, timezone)
- Advanced settings (JSON configuration)

NOTE: RAG and LLM settings are now admin-only configuration managed
through Knowledge Base and Model Configuration systems.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.config import get_settings_instance
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..models.user_preferences import UserPreferences
from ..schemas.envelope import SuccessResponse
from ..schemas.user_preferences import (
    UserPreferencesCreate,
    UserPreferencesResponse,
    UserPreferencesUpdate,
)
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter()
settings = get_settings_instance()


def _build_preferences_response(
    preferences: UserPreferences | None = None, *, defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Construct the user preferences response envelope, augmenting with system-level read-only settings.

    Args:
        preferences: Optional ORM model instance to serialize.
        defaults: Optional dict representing default preferences (used when model is absent).

    """
    if preferences is not None:
        base = {
            "memory_depth": preferences.memory_depth,
            "memory_similarity_threshold": preferences.memory_similarity_threshold,
            "theme": preferences.theme,
            "language": preferences.language,
            "timezone": preferences.timezone,
            "advanced_settings": preferences.advanced_settings or {},
        }
    else:
        source = defaults if defaults is not None else UserPreferences.get_default_preferences()
        base = {
            "memory_depth": source.get("memory_depth", 5),
            "memory_similarity_threshold": source.get("memory_similarity_threshold", 0.6),
            "theme": source.get("theme", "light"),
            "language": source.get("language", "en"),
            "timezone": source.get("timezone", "UTC"),
            "advanced_settings": source.get("advanced_settings") or {},
        }

    base.update(
        {
            "summary_search_min_token_length": settings.conversation_summary_search_min_token_length,
            "summary_search_max_tokens": settings.conversation_summary_search_max_tokens,
        }
    )
    return base


@router.get("/user/preferences", response_model=SuccessResponse[UserPreferencesResponse])
async def get_user_preferences(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get current user's preferences."""
    try:
        # Try to get existing preferences
        stmt = select(UserPreferences).where(UserPreferences.user_id == current_user.id)
        result = await db.execute(stmt)
        preferences = result.scalar_one_or_none()

        if not preferences:
            default_prefs = UserPreferences.get_default_preferences()
            logger.info(f"Retrieved default preferences for user {current_user.id}")
            return ShuResponse.success(_build_preferences_response(defaults=default_prefs))

        logger.info(f"Retrieved preferences for user {current_user.id}")
        return ShuResponse.success(_build_preferences_response(preferences))

    except Exception as e:
        logger.error(f"Error retrieving user preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user preferences",
        )


@router.put("/user/preferences", response_model=SuccessResponse[UserPreferencesResponse])
async def create_or_update_user_preferences(
    preferences_data: UserPreferencesCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or completely replace user preferences."""
    try:
        # Check if preferences already exist
        stmt = select(UserPreferences).where(UserPreferences.user_id == current_user.id)
        result = await db.execute(stmt)
        existing_preferences = result.scalar_one_or_none()

        if existing_preferences:
            # Update existing preferences
            for field, value in preferences_data.dict().items():
                setattr(existing_preferences, field, value)
            preferences = existing_preferences
        else:
            # Create new preferences
            preferences = UserPreferences(user_id=current_user.id, **preferences_data.dict())
            db.add(preferences)

        await db.commit()
        await db.refresh(preferences)

        logger.info(f"Created/updated preferences for user {current_user.id}")
        return ShuResponse.success(_build_preferences_response(preferences))

    except Exception as e:
        logger.error(f"Error creating/updating user preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create/update user preferences",
        )


@router.patch("/user/preferences", response_model=SuccessResponse[UserPreferencesResponse])
async def update_user_preferences_partial(
    preferences_data: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partially update user preferences."""
    try:
        # Get existing preferences or create with defaults
        stmt = select(UserPreferences).where(UserPreferences.user_id == current_user.id)
        result = await db.execute(stmt)
        preferences = result.scalar_one_or_none()

        if not preferences:
            # Create new preferences with defaults, then apply updates
            preferences = UserPreferences(user_id=current_user.id, **UserPreferences.get_default_preferences())
            db.add(preferences)

        # Apply partial updates
        update_data = preferences_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(preferences, field, value)

        await db.commit()
        await db.refresh(preferences)

        logger.info(f"Partially updated preferences for user {current_user.id}")
        return ShuResponse.success(_build_preferences_response(preferences))

    except Exception as e:
        logger.error(f"Error partially updating user preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user preferences",
        )
