"""Minimal Tool Schedule persistence for Tools v1.

Supports fixed-interval schedules (cron can be added later).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String
from sqlalchemy.dialects.postgresql import TIMESTAMP

from .base import BaseModel


class PluginFeed(BaseModel):
    __tablename__ = "plugin_feeds"

    name = Column(String(120), nullable=False, index=True)
    plugin_name = Column("plugin_name", String(100), nullable=False, index=True)
    agent_key = Column(String(100), nullable=True)

    # Who owns/created the schedule (admin or system)
    owner_user_id = Column(String(36), nullable=True, index=True)

    params = Column(JSON, nullable=True)

    # Minimal scheduling: fixed interval in seconds
    interval_seconds = Column(Integer, nullable=False, default=3600)
    enabled = Column(Boolean, nullable=False, default=True, index=True)

    # Next run calculation and tracking
    next_run_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_run_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (Index("ix_plugin_feed_enabled_next", "enabled", "next_run_at"),)

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)

    def schedule_next(self) -> None:
        self.last_run_at = self.now_utc()
        # If interval invalid, default to 3600s
        interval = self.interval_seconds or 3600
        self.next_run_at = self.last_run_at + timedelta(seconds=max(1, int(interval)))
