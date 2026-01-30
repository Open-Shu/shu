"""SystemSetting model for storing configurable application values.

Provides a flexible key/value store where values are persisted as JSON blobs,
making it easy to add new application-wide settings without migrations.
"""

from sqlalchemy import Column, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.types import JSON

from .base import Base, TimestampMixin


class SystemSetting(TimestampMixin, Base):
    """Key/value settings table with JSON-backed values."""

    __tablename__ = "system_settings"

    key = Column(String(128), primary_key=True, index=True)
    value = Column(MutableDict.as_mutable(JSON), nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<SystemSetting(key='{self.key}')>"
