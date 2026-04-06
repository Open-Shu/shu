"""Pydantic schemas for API integration admin API."""

from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shu.schemas.integration_common import IngestConfig


def _validate_api_url(v: str) -> str:
    """Validate URL scheme: HTTPS required except for localhost."""
    if v.startswith("https://"):
        return v
    if v.startswith("http://"):
        hostname = urlparse(v).hostname or ""
        if hostname in ("localhost", "127.0.0.1", "::1"):
            return v
        raise ValueError("Plain HTTP is only allowed for localhost connections. Use HTTPS for remote servers.")
    raise ValueError("URL must start with http:// or https://")


class AuthType(str, Enum):
    """Authentication injection method."""

    HEADER = "header"
    QUERY = "query"


class AuthConfig(BaseModel):
    """Authentication configuration for an API integration."""

    type: AuthType = Field(..., description="Where to inject the credential: header or query param")
    name: str = Field(..., min_length=1, max_length=256, description="Header name or query parameter name")
    prefix: str = Field(default="", max_length=64, description="Value prefix (e.g. 'Bearer ', 'Token ')")
    setup_instructions: str | None = Field(
        None, max_length=2000, description="Human-readable instructions for obtaining the credential"
    )


class ApiIntegrationDefinition(BaseModel):
    """Schema for the YAML import file that defines an API integration.

    This validates the parsed YAML content. The YAML is the delivery format;
    once imported, the system works with ApiServerConnection records.
    """

    api_integration_version: int = Field(..., ge=1, le=1, description="Schema version (must be 1)")
    name: str = Field(
        ...,
        min_length=1,
        max_length=96,
        pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$",
        description="Integration name (lowercase alphanumeric with hyphens, no leading/trailing hyphens)",
    )
    description: str | None = Field(None, max_length=500, description="Human-readable description")
    openapi_definition: str = Field(
        ..., min_length=1, max_length=2000, description="URL to the OpenAPI specification (JSON or YAML)"
    )
    auth: AuthConfig | None = Field(None, description="Authentication configuration")
    ingest_defaults: dict[str, IngestConfig] | None = Field(
        None, description="Pre-configured ingest settings keyed by operation name"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Reject names containing __ which conflicts with the tool name delimiter."""
        if "__" in v:
            raise ValueError("Integration name must not contain '__' (reserved as tool name delimiter)")
        return v

    @field_validator("openapi_definition")
    @classmethod
    def validate_openapi_url(cls, v: str) -> str:
        """Validate OpenAPI spec URL scheme."""
        return _validate_api_url(v)


class ApiConnectionStatus(str, Enum):
    """Derived connection status based on health tracking."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    ERROR = "error"


class ApiTimeoutsConfig(BaseModel):
    """Timeout configuration for an API integration connection."""

    connect_ms: int = Field(default=5000, ge=1000, le=60000, description="Connection timeout in milliseconds")
    call_ms: int = Field(default=30000, ge=1000, le=600000, description="Tool call timeout in milliseconds")
    read_ms: int = Field(default=30000, ge=1000, le=600000, description="Read timeout in milliseconds")


class ApiConnectionCreate(BaseModel):
    """Schema for creating an API integration from YAML content."""

    yaml_content: str = Field(
        ..., min_length=1, max_length=100000, description="Raw YAML content defining the integration"
    )
    auth_credential: str | None = Field(
        None, min_length=1, max_length=4096, description="Auth credential value (API key, token, etc.)"
    )


class ApiConnectionUpdate(BaseModel):
    """Schema for updating an API integration connection."""

    timeouts: ApiTimeoutsConfig | None = Field(None, description="Timeout overrides")
    response_size_limit_bytes: int | None = Field(None, ge=1024, le=104857600, description="Max response size")
    enabled: bool | None = Field(None, description="Whether the connection is enabled")


class ApiDiscoveredTool(BaseModel):
    """Schema for a discovered API tool."""

    name: str = Field(..., description="Operation name")
    description: str | None = Field(None, description="Operation description")
    input_schema: dict[str, Any] | None = Field(None, alias="inputSchema", description="JSON Schema for tool input")
    method: str | None = Field(None, description="HTTP method")
    path: str | None = Field(None, description="URL path template")
    stale: bool = Field(default=False, description="Whether the tool was absent from last sync")

    model_config = ConfigDict(populate_by_name=True)


class ApiToolConfigResponse(BaseModel):
    """Schema for a tool's admin configuration."""

    chat_callable: bool = Field(default=True, description="Tool is callable from chat")
    feed_eligible: bool = Field(default=False, description="Tool is available as a feed source")
    enabled: bool = Field(default=True, description="Whether the tool is enabled")
    ingest: IngestConfig | None = Field(None, description="Ingest configuration")
    stale: bool = Field(default=False, description="Whether the tool was absent from last sync")


class ApiConnectionResponse(BaseModel):
    """Schema for API integration connection response."""

    id: str = Field(..., description="Connection ID")
    name: str = Field(..., description="Display name")
    url: str = Field(..., description="OpenAPI specification URL")
    spec_type: str = Field(..., description="Specification type (e.g. 'openapi')")
    base_url: str | None = Field(None, description="Resolved API base URL from spec")
    tool_configs: dict[str, ApiToolConfigResponse] | None = Field(None, description="Per-tool configuration")
    discovered_tools: list[ApiDiscoveredTool] | None = Field(None, description="Tools discovered from spec")
    timeouts: ApiTimeoutsConfig | None = Field(None, description="Timeout configuration")
    response_size_limit_bytes: int | None = Field(None, description="Max response size in bytes")
    enabled: bool = Field(..., description="Whether the connection is enabled")
    status: ApiConnectionStatus = Field(..., description="Derived connection status")
    tool_count: int = Field(default=0, description="Number of discovered tools")
    last_synced_at: datetime | None = Field(None, description="Last successful sync")
    last_error: str | None = Field(None, description="Last error message")
    consecutive_failures: int = Field(default=0, description="Consecutive failure count")
    has_auth: bool = Field(default=False, description="Whether auth credentials are configured")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(from_attributes=True)


class ApiConnectionListResponse(BaseModel):
    """Schema for listing API integration connections."""

    items: list[ApiConnectionResponse] = Field(..., description="List of connections")
    total: int = Field(..., description="Total number of connections")


class ApiSyncResult(BaseModel):
    """Schema for sync operation result."""

    tools: list[str] = Field(default_factory=list, description="Tool names currently in the spec")
    added: list[str] = Field(default_factory=list, description="Newly discovered tools")
    stale: list[str] = Field(default_factory=list, description="Tools no longer present in spec (preserved)")
    errors: list[str] = Field(default_factory=list, description="Any errors during sync")
