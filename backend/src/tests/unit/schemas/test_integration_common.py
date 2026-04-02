"""Unit tests for shared integration ingest schemas."""

import pytest
from pydantic import ValidationError

from shu.schemas.integration_common import (
    IngestConfig,
    IngestFieldMapping,
    IngestMethod,
    ToolConfigUpdate,
)


def _make_field_mapping(**overrides: str) -> dict:
    """Build a minimal valid IngestFieldMapping dict with optional overrides."""
    base = {
        "title": "result.title",
        "content": "result.body",
        "source_id": "result.id",
    }
    base.update(overrides)
    return base


def _make_ingest_config(**overrides) -> dict:
    """Build a minimal valid IngestConfig dict with optional overrides."""
    base: dict = {"field_mapping": _make_field_mapping()}
    base.update(overrides)
    return base


class TestIngestMethod:
    def test_values(self):
        assert IngestMethod.TEXT.value == "text"
        assert IngestMethod.DOCUMENT.value == "document"

    def test_is_str_enum(self):
        assert isinstance(IngestMethod.TEXT, str)
        assert IngestMethod.TEXT == "text"


class TestIngestFieldMapping:
    def test_valid_with_required_fields(self):
        mapping = IngestFieldMapping(**_make_field_mapping())
        assert mapping.title == "result.title"
        assert mapping.content == "result.body"
        assert mapping.source_id == "result.id"

    def test_source_url_optional(self):
        mapping = IngestFieldMapping(**_make_field_mapping())
        assert mapping.source_url is None

    def test_source_url_accepted(self):
        mapping = IngestFieldMapping(**_make_field_mapping(source_url="result.url"))
        assert mapping.source_url == "result.url"

    @pytest.mark.parametrize("missing_field", ["title", "content", "source_id"])
    def test_missing_required_field_raises(self, missing_field: str):
        data = _make_field_mapping()
        del data[missing_field]
        with pytest.raises(ValidationError) as exc_info:
            IngestFieldMapping(**data)
        assert missing_field in str(exc_info.value)


class TestIngestConfig:
    def test_minimal_valid(self):
        config = IngestConfig(**_make_ingest_config())
        assert config.method == IngestMethod.TEXT
        assert config.collection_field is None
        assert config.attributes is None
        assert config.cursor_field is None
        assert config.cursor_param is None

    def test_method_defaults_to_text(self):
        config = IngestConfig(**_make_ingest_config())
        assert config.method is IngestMethod.TEXT

    def test_full_valid(self):
        config = IngestConfig(
            **_make_ingest_config(
                method="document",
                collection_field="data.items",
                attributes={"source": "github"},
                cursor_field="meta.next_cursor",
                cursor_param="cursor",
            )
        )
        assert config.method == IngestMethod.DOCUMENT
        assert config.collection_field == "data.items"
        assert config.attributes == {"source": "github"}
        assert config.cursor_field == "meta.next_cursor"
        assert config.cursor_param == "cursor"

    def test_invalid_method_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            IngestConfig(**_make_ingest_config(method="invalid"))
        assert "method" in str(exc_info.value)

    def test_field_mapping_required(self):
        with pytest.raises(ValidationError) as exc_info:
            IngestConfig()
        assert "field_mapping" in str(exc_info.value)


class TestToolConfigUpdate:
    def test_defaults(self):
        update = ToolConfigUpdate()
        assert update.chat_callable is True
        assert update.feed_eligible is False
        assert update.enabled is True
        assert update.ingest is None

    def test_feed_eligible_with_explicit_ingest_none_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ToolConfigUpdate(feed_eligible=True, ingest=None)
        assert "ingest" in str(exc_info.value).lower()

    def test_feed_eligible_with_ingest_succeeds(self):
        update = ToolConfigUpdate(
            feed_eligible=True,
            ingest=_make_ingest_config(),
        )
        assert update.feed_eligible is True
        assert update.ingest is not None

    def test_feed_not_eligible_without_ingest_succeeds(self):
        update = ToolConfigUpdate(feed_eligible=False)
        assert update.ingest is None


class TestMcpReExports:
    def test_mcp_ingest_config(self):
        from shu.schemas.mcp_admin import McpIngestConfig

        assert McpIngestConfig is IngestConfig

    def test_mcp_ingest_field_mapping(self):
        from shu.schemas.mcp_admin import McpIngestFieldMapping

        assert McpIngestFieldMapping is IngestFieldMapping

    def test_mcp_ingest_method(self):
        from shu.schemas.mcp_admin import McpIngestMethod

        assert McpIngestMethod is IngestMethod

    def test_mcp_tool_config_update(self):
        from shu.schemas.mcp_admin import McpToolConfigUpdate

        assert McpToolConfigUpdate is ToolConfigUpdate
