"""Minimal Plugin Execution persistence for Plugins v1.

Tracks executions triggered via API or schedules. Kept intentionally simple
for Option A Step 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from shu.plugins.base import Plugin

from .base import BaseModel


class PluginExecutionStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PluginExecution(BaseModel):
    __tablename__ = "plugin_executions"

    # Link to schedule if this was enqueued by a schedule
    schedule_id = Column(String, ForeignKey("plugin_feeds.id"), nullable=True, index=True)

    # What to run
    plugin_name = Column("plugin_name", String(100), nullable=False, index=True)
    agent_key = Column(String(100), nullable=True)

    # Who/what initiated the run
    user_id = Column(String(36), nullable=False, index=True)

    # IO payloads
    params = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)

    # Status & timing
    status = Column(String(32), nullable=False, default=PluginExecutionStatus.PENDING, index=True)
    error = Column(Text, nullable=True)
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (Index("ix_plugin_exec_status_plugin", "status", "plugin_name"),)

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)


@dataclass
class CallableTool:
    name: str
    op: str
    plugin: Plugin | None
    schema: dict[str, Any] | None
    enum_labels: dict[str, Any] | None
