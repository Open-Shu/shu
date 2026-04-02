"""Shared Pydantic schemas for integration ingest configuration.

These schemas are used by MCP and YAML-based plugin integrations alike.
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class IngestMethod(str, Enum):
    """Ingest method for integration tools."""

    TEXT = "text"
    DOCUMENT = "document"


class IngestFieldMapping(BaseModel):
    """Field mapping for ingest-type integration tools."""

    title: str = Field(..., description="Dot-notation path to title field in response")
    content: str = Field(..., description="Dot-notation path to content field in response")
    source_id: str = Field(..., description="Dot-notation path to source ID field in response")
    source_url: str | None = Field(None, description="Dot-notation path to source URL field in response")


class IngestConfig(BaseModel):
    """Ingest configuration for an integration tool."""

    method: IngestMethod = Field(default=IngestMethod.TEXT, description="Ingest method")
    field_mapping: IngestFieldMapping = Field(..., description="Field mapping from tool response to ingest fields")
    collection_field: str | None = Field(None, description="Dot-notation path to collection array in response")
    attributes: dict[str, str] | None = Field(None, description="Static attributes to attach to ingested items")
    cursor_field: str | None = Field(
        None, description="Dot-notation path to next-page cursor in response (enables pagination loop)"
    )
    cursor_param: str | None = Field(None, description="Tool argument name to pass the cursor value as")


class ToolConfigUpdate(BaseModel):
    """Schema for updating a single tool's configuration."""

    chat_callable: bool = Field(default=True, description="Tool is callable from chat")
    feed_eligible: bool = Field(default=False, description="Tool is available as a feed source")
    enabled: bool = Field(default=True, description="Whether the tool is enabled")
    ingest: IngestConfig | None = Field(None, description="Ingest configuration (required when feed_eligible is True)")

    @field_validator("ingest")
    @classmethod
    def validate_ingest_config(cls, v: IngestConfig | None, info) -> IngestConfig | None:
        """Require ingest config when feed_eligible is True."""
        if info.data.get("feed_eligible") and v is None:
            raise ValueError("ingest configuration is required when feed_eligible is True")
        return v
