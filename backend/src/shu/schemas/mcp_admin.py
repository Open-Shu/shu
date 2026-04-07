"""Pydantic schemas for MCP server connection admin API."""

from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_mcp_url(v: str) -> str:
    """Validate URL scheme: HTTPS required except for localhost."""
    if v.startswith("https://"):
        return v
    if v.startswith("http://"):
        hostname = urlparse(v).hostname or ""
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return v
        raise ValueError("Plain HTTP is only allowed for localhost connections. Use HTTPS for remote servers.")
    raise ValueError("URL must start with http:// or https://")


class McpIngestMethod(str, Enum):
    """Ingest method for MCP tools."""

    TEXT = "text"
    DOCUMENT = "document"


class McpConnectionStatus(str, Enum):
    """Derived connection status based on health tracking."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    ERROR = "error"


class McpTimeoutsConfig(BaseModel):
    """Timeout configuration for an MCP server connection."""

    connect_ms: int = Field(default=5000, ge=1000, le=60000, description="Connection timeout in milliseconds")
    call_ms: int = Field(default=30000, ge=1000, le=600000, description="Tool call timeout in milliseconds")
    read_ms: int = Field(default=30000, ge=1000, le=600000, description="Read timeout in milliseconds")


class McpIngestFieldMapping(BaseModel):
    """Field mapping for ingest-type MCP tools."""

    title: str = Field(..., description="Dot-notation path to title field in response")
    content: str = Field(..., description="Dot-notation path to content field in response")
    source_id: str = Field(..., description="Dot-notation path to source ID field in response")
    source_url: str | None = Field(None, description="Dot-notation path to source URL field in response")


class McpIngestConfig(BaseModel):
    """Ingest configuration for an MCP tool."""

    method: McpIngestMethod = Field(default=McpIngestMethod.TEXT, description="Ingest method")
    field_mapping: McpIngestFieldMapping = Field(..., description="Field mapping from tool response to ingest fields")
    collection_field: str | None = Field(None, description="Dot-notation path to collection array in response")
    attributes: dict[str, str] | None = Field(None, description="Static attributes to attach to ingested items")
    cursor_field: str | None = Field(
        None, description="Dot-notation path to next-page cursor in response (enables pagination loop)"
    )
    cursor_param: str | None = Field(None, description="Tool argument name to pass the cursor value as")


class McpToolConfigUpdate(BaseModel):
    """Schema for updating a single tool's configuration."""

    chat_callable: bool = Field(default=True, description="Tool is callable from chat")
    feed_eligible: bool = Field(default=False, description="Tool is available as a feed source")
    enabled: bool = Field(default=True, description="Whether the tool is enabled")
    ingest: McpIngestConfig | None = Field(
        None, description="Ingest configuration (required when feed_eligible is True)"
    )

    @field_validator("ingest")
    @classmethod
    def validate_ingest_config(cls, v: McpIngestConfig | None, info) -> McpIngestConfig | None:
        """Require ingest config when feed_eligible is True."""
        if info.data.get("feed_eligible") and v is None:
            raise ValueError("ingest configuration is required when feed_eligible is True")
        return v


class McpConnectionCreate(BaseModel):
    """Schema for creating an MCP server connection."""

    name: str = Field(..., min_length=1, max_length=96, description="Display name for the connection")
    url: str = Field(..., min_length=1, max_length=500, description="MCP server Streamable HTTP endpoint URL")
    headers: dict[str, str] | None = Field(None, description="Auth headers (values will be encrypted at rest)")
    timeouts: McpTimeoutsConfig | None = Field(None, description="Timeout overrides")
    response_size_limit_bytes: int | None = Field(
        None, ge=1024, le=104857600, description="Max response size in bytes (default 10MB)"
    )
    enabled: bool = Field(default=True, description="Whether the connection is enabled")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL scheme: HTTPS required except for localhost."""
        return _validate_mcp_url(v)


class McpConnectionUpdate(BaseModel):
    """Schema for updating an MCP server connection."""

    url: str | None = Field(None, min_length=1, max_length=500, description="MCP server endpoint URL")
    headers: dict[str, str] | None = Field(None, description="Auth headers (values will be encrypted at rest)")
    timeouts: McpTimeoutsConfig | None = Field(None, description="Timeout overrides")
    response_size_limit_bytes: int | None = Field(None, ge=1024, le=104857600, description="Max response size")
    enabled: bool | None = Field(None, description="Whether the connection is enabled")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        """Validate URL scheme if provided."""
        if v is None:
            return v
        return _validate_mcp_url(v)


class McpDiscoveredTool(BaseModel):
    """Schema for a discovered MCP tool."""

    name: str = Field(..., description="Tool name")
    description: str | None = Field(None, description="Tool description")
    input_schema: dict[str, Any] | None = Field(None, alias="inputSchema", description="JSON Schema for tool input")

    model_config = ConfigDict(populate_by_name=True)


class McpToolConfigResponse(BaseModel):
    """Schema for a tool's admin configuration."""

    chat_callable: bool = Field(default=True, description="Tool is callable from chat")
    feed_eligible: bool = Field(default=False, description="Tool is available as a feed source")
    enabled: bool = Field(default=True, description="Whether the tool is enabled")
    ingest: McpIngestConfig | None = Field(None, description="Ingest configuration")


class McpConnectionResponse(BaseModel):
    """Schema for MCP server connection response."""

    id: str = Field(..., description="Connection ID")
    name: str = Field(..., description="Display name")
    url: str = Field(..., description="MCP server endpoint URL")
    tool_configs: dict[str, McpToolConfigResponse] | None = Field(None, description="Per-tool configuration")
    discovered_tools: list[McpDiscoveredTool] | None = Field(None, description="Tools discovered from server")
    timeouts: McpTimeoutsConfig | None = Field(None, description="Timeout configuration")
    response_size_limit_bytes: int | None = Field(None, description="Max response size in bytes")
    enabled: bool = Field(..., description="Whether the connection is enabled")
    status: McpConnectionStatus = Field(..., description="Derived connection status")
    tool_count: int = Field(default=0, description="Number of discovered tools")
    last_synced_at: datetime | None = Field(None, description="Last successful sync")
    last_connected_at: datetime | None = Field(None, description="Last successful connection")
    last_error: str | None = Field(None, description="Last error message")
    consecutive_failures: int = Field(default=0, description="Consecutive failure count")
    server_info: dict[str, Any] | None = Field(None, description="MCP server metadata")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(from_attributes=True)


class McpConnectionListResponse(BaseModel):
    """Schema for listing MCP server connections."""

    items: list[McpConnectionResponse] = Field(..., description="List of connections")
    total: int = Field(..., description="Total number of connections")


class McpSyncResult(BaseModel):
    """Schema for sync operation result."""

    tools: list[str] = Field(default_factory=list, description="Tool names currently on the server")
    added: list[str] = Field(default_factory=list, description="Newly discovered tools")
    removed: list[str] = Field(default_factory=list, description="Tools no longer present on server")
    errors: list[str] = Field(default_factory=list, description="Any errors during sync")


class McpTestResult(BaseModel):
    """Schema for connection test result."""

    success: bool = Field(..., description="Whether the connection test succeeded")
    server_info: dict[str, Any] | None = Field(None, description="Server metadata from initialize handshake")
    tool_count: int | None = Field(None, description="Number of tools available")
    latency_ms: int | None = Field(None, description="Connection latency in milliseconds")
    error: str | None = Field(None, description="Error message if test failed")
