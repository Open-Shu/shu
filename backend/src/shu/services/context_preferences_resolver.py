from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..core.config import ConfigurationManager
from ..models.model_configuration import ModelConfiguration
from ..models.user_preferences import UserPreferences


class ContextPreferencesResolver:
    """
    Shared helper that resolves context-window sizing and user preference bundles.

    ChatService previously owned this logic directly; moving it here allows other
    services (agents, workflows, etc.) to reuse the same resolution rules.
    """

    def __init__(self, db_session: AsyncSession, config_manager: ConfigurationManager) -> None:
        self.db_session = db_session
        self.config_manager = config_manager

    def resolve_max_context_tokens(
        self, *, active_model_config: Optional[ModelConfiguration]
    ) -> int:
        """
        Determine the max context window allowed for the current execution.

        Order of precedence:
        1. Model configuration parameter overrides (max_context_window)
        2. Global LLM default from settings
        """
        model_overrides = {}
        if active_model_config is not None:
            try:
                model_overrides = getattr(active_model_config, "parameter_overrides", None) or {}
            except Exception:
                model_overrides = {}

        if model_overrides.get("max_context_window") is not None:
            try:
                return int(model_overrides["max_context_window"])
            except (TypeError, ValueError):
                pass

        return int(getattr(self.config_manager.settings, "llm_max_tokens_default", 50_000))

    async def resolve_user_context_preferences(
        self,
        *,
        user_id: str,
        current_user: Optional[User],
    ) -> Dict[str, Any]:
        """
        Resolve user-controlled context preferences (currently memory depth).

        Pulls from the in-flight user object when available to avoid extra queries,
        otherwise falls back to loading preferences from the database.
        """
        prefs = None
        if current_user and getattr(current_user, "id", None) == user_id:
            prefs = getattr(current_user, "preferences", None)

        if prefs is None:
            stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
            result = await self.db_session.execute(stmt)
            prefs = result.scalar_one_or_none()

        memory_depth = getattr(prefs, "memory_depth", None) if prefs else None
        if not isinstance(memory_depth, int) or memory_depth <= 0:
            memory_depth = getattr(self.config_manager.settings, "user_memory_depth_default", 5)

        return {
            "memory_depth": memory_depth,
        }
