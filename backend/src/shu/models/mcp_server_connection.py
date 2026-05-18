"""MCP server connection persistence."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP

from .base import BaseModel, TenantScopedMixin


class McpServerConnection(TenantScopedMixin, BaseModel):
    __tablename__ = "mcp_server_connections"

    # Per-tenant uniqueness, not global. Tenant A picking a connection name
    # like "github" must not block tenant B from using the same. The composite
    # UniqueConstraint below enforces the per-tenant scope.
    name = Column(String(96), nullable=False)
    __table_args__ = UniqueConstraint("tenant_id", "name", name="uq_mcp_server_connections_tenant_name")
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
