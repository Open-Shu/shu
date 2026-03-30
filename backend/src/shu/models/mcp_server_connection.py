"""MCP server connection persistence."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import TIMESTAMP

from .base import BaseModel


class McpServerConnection(BaseModel):
    __tablename__ = "mcp_server_connections"

    name = Column(String(96), nullable=False, unique=True)
    url = Column(String(500), nullable=False)
    tool_configs = Column(JSON, nullable=True)
    discovered_tools = Column(JSON, nullable=True)
    timeouts = Column(JSON, nullable=True)
    response_size_limit_bytes = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    last_synced_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_connected_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_error = Column(String(500), nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    server_info = Column(JSON, nullable=True)
