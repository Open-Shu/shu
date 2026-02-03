"""User Preferences model for Shu.

This module defines user-specific settings and preferences for chat behavior,
memory configuration, and other customizable features.
"""

from typing import Any

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import BaseModel


class UserPreferences(BaseModel):
    """User preferences for chat settings and behavior.

    This stores user-specific configuration for:
    - Default search thresholds
    - Chat behavior preferences
    - UI/UX preferences
    """

    __tablename__ = "user_preferences"

    # User reference
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Memory Settings
    memory_depth = Column(Integer, default=5, nullable=False)  # Number of previous conversations to consider
    memory_similarity_threshold = Column(
        Float, default=0.6, nullable=False
    )  # Similarity threshold for memory retrieval

    # NOTE: RAG and LLM settings removed - these should be admin-only configuration
    # Users should not be able to override KB or model configuration settings
    # Removed: default_search_threshold, default_max_results, default_context_format,
    # default_reference_format, default_temperature, default_max_tokens

    # UI/UX Preferences
    theme = Column(String(20), default="light", nullable=False)  # 'light', 'dark', 'auto'
    language = Column(String(10), default="en", nullable=False)  # Language preference
    timezone = Column(String(50), default="UTC", nullable=False)  # User timezone

    # Advanced Settings (JSON for flexibility)
    advanced_settings = Column(JSON, nullable=True)  # Additional custom settings

    # Relationships
    user = relationship("User", back_populates="preferences")

    def __repr__(self) -> str:
        return f"<UserPreferences(user_id={self.user_id}, memory_depth={self.memory_depth})>"

    @property
    def memory_settings(self) -> dict[str, Any]:
        """Get memory-related settings as a dictionary."""
        return {
            "depth": self.memory_depth,
            "similarity_threshold": self.memory_similarity_threshold,
        }

    # NOTE: search_settings and chat_settings properties removed
    # These settings are now admin-only and should not be user-configurable

    @property
    def ui_settings(self) -> dict[str, Any]:
        """Get UI/UX settings as a dictionary."""
        return {"theme": self.theme, "language": self.language, "timezone": self.timezone}

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a specific setting value with fallback to default."""
        if hasattr(self, key):
            return getattr(self, key)

        # Check advanced settings
        if self.advanced_settings and key in self.advanced_settings:
            return self.advanced_settings[key]

        return default

    def set_advanced_setting(self, key: str, value: Any) -> None:
        """Set a value in advanced settings."""
        if self.advanced_settings is None:
            self.advanced_settings = {}

        self.advanced_settings[key] = value

    @classmethod
    def get_default_preferences(cls) -> dict[str, Any]:
        """Get default preference values for new users.

        Only includes legitimate user preferences that users should be able to control.
        RAG and LLM settings are now admin-only configuration.
        """
        return {
            # Memory settings (legitimate user preferences)
            "memory_depth": 5,
            "memory_similarity_threshold": 0.6,
            # UI/UX preferences (legitimate user preferences)
            "theme": "light",
            "language": "en",
            "timezone": "UTC",
            "advanced_settings": {},
        }
