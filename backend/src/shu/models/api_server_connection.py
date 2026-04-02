"""API server connection persistence."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import TIMESTAMP

from .base import BaseModel


class ApiServerConnection(BaseModel):
    __tablename__ = "api_server_connections"

    name = Column(String(96), nullable=False, unique=True)
    url = Column(String(500), nullable=False)
    spec_type = Column(String(32), nullable=False, default="openapi")
    import_source = Column(JSON, nullable=True)
    tool_configs = Column(JSON, nullable=True)
    discovered_tools = Column(JSON, nullable=True)
    timeouts = Column(JSON, nullable=True)
    response_size_limit_bytes = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    last_synced_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_error = Column(String(500), nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    auth_config = Column(JSON, nullable=True)
    base_url = Column(String(500), nullable=True)
