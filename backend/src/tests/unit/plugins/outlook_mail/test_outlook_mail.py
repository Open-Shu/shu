"""Core unit tests for Outlook Mail plugin.

Tests schema validation, parameter validation, operation routing, and authentication.
"""
import pytest


class TestOutlookMailPluginSchema:
    """Test plugin schema and manifest."""

    def test_plugin_has_required_attributes(self, plugin):
        """Test plugin has name and version attributes."""
        assert plugin.name == "outlook_mail"
        assert plugin.version == "1"

    def test_get_schema_returns_valid_structure(self, plugin):
        """Test get_schema returns valid JSON schema with required properties."""
        schema = plugin.get_schema()
        assert schema is not None
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "op" in schema["properties"]
        assert "since_hours" in schema["properties"]
        assert "query_filter" in schema["properties"]
        assert "max_results" in schema["properties"]
        assert "kb_id" in schema["properties"]
        assert "reset_cursor" in schema["properties"]

    def test_op_enum_has_correct_values(self, plugin):
        """Test op parameter has correct enum values and default."""
        schema = plugin.get_schema()
        op_prop = schema["properties"]["op"]
        assert op_prop["enum"] == ["list", "digest", "ingest"]
        assert op_prop["default"] == "ingest"

    def test_since_hours_constraints(self, plugin):
        """Test since_hours has correct type and range constraints."""
        schema = plugin.get_schema()
        since_hours = schema["properties"]["since_hours"]
        assert since_hours["type"] == "integer"
        assert since_hours["minimum"] == 1
        assert since_hours["maximum"] == 3360
        assert since_hours["default"] == 48

    def test_max_results_constraints(self, plugin):
        """Test max_results has correct type and range constraints."""
        schema = plugin.get_schema()
        max_results = schema["properties"]["max_results"]
        assert max_results["type"] == "integer"
        assert max_results["minimum"] == 1
        assert max_results["maximum"] == 500
        assert max_results["default"] == 50

    def test_kb_id_is_hidden(self, plugin):
        """Test kb_id is marked as hidden in UI."""
        schema = plugin.get_schema()
        kb_id = schema["properties"]["kb_id"]
        assert kb_id["x-ui"]["hidden"] is True

    def test_schema_excludes_auth_fields(self, plugin):
        """Test schema does not include auth-related fields (handled by host)."""
        schema = plugin.get_schema()
        properties = schema["properties"]
        assert "auth_mode" not in properties
        assert "user_email" not in properties
        assert "access_token" not in properties

    def test_output_schema_structure(self, plugin):
        """Test get_output_schema returns valid structure."""
        schema = plugin.get_output_schema()
        assert schema is not None
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "messages" in schema["properties"]
        assert "count" in schema["properties"]
        assert "deleted" in schema["properties"]
        assert "ko" in schema["properties"]
        assert "window" in schema["properties"]
        assert "diagnostics" in schema["properties"]
        assert "skips" in schema["properties"]


class TestOutlookMailParameterValidation:
    """Test parameter validation for all operations."""

    @pytest.mark.asyncio
    async def test_invalid_op_returns_error(self, plugin):
        """Test execute with invalid operation returns error."""
        result = await plugin.execute({"op": "invalid"}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"
        assert "Unsupported op" in result.error["message"]

    @pytest.mark.asyncio
    async def test_ingest_without_kb_id_returns_error(self, plugin):
        """Test ingest operation without kb_id returns error."""
        result = await plugin.execute({"op": "ingest"}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "missing_parameter"
        assert "kb_id is required" in result.error["message"]

    @pytest.mark.asyncio
    async def test_since_hours_below_minimum(self, plugin):
        """Test since_hours below minimum (1) returns error."""
        result = await plugin.execute({"op": "list", "since_hours": 0}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"
        assert "since_hours must be between 1 and 3360" in result.error["message"]

    @pytest.mark.asyncio
    async def test_since_hours_above_maximum(self, plugin):
        """Test since_hours above maximum (3360) returns error."""
        result = await plugin.execute({"op": "list", "since_hours": 5000}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"
        assert "since_hours must be between 1 and 3360" in result.error["message"]

    @pytest.mark.asyncio
    async def test_since_hours_non_integer(self, plugin):
        """Test non-integer since_hours returns error."""
        result = await plugin.execute({"op": "list", "since_hours": "invalid"}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"

    @pytest.mark.asyncio
    async def test_max_results_below_minimum(self, plugin):
        """Test max_results below minimum (1) returns error."""
        result = await plugin.execute({"op": "list", "max_results": 0}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"
        assert "max_results must be between 1 and 500" in result.error["message"]

    @pytest.mark.asyncio
    async def test_max_results_above_maximum(self, plugin):
        """Test max_results above maximum (500) returns error."""
        result = await plugin.execute({"op": "list", "max_results": 1000}, None, None)
        assert result.status == "error"
        assert result.error["code"] == "invalid_parameter"
        assert "max_results must be between 1 and 500" in result.error["message"]


class TestOutlookMailOperationRouting:
    """Test operation routing to correct handlers."""

    @pytest.mark.asyncio
    async def test_list_routes_correctly(self, plugin, mock_host):
        """Test list operation routes to _execute_list."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "headers": {},
            "body": {"value": [], "@odata.nextLink": None}
        }

        result = await plugin.execute({"op": "list"}, None, mock_host)

        assert result.status == "success"
        assert "messages" in result.data
        assert isinstance(result.data["messages"], list)

    @pytest.mark.asyncio
    async def test_digest_routes_correctly(self, plugin, mock_host):
        """Test digest operation routes to _execute_digest."""
        mock_host.http.fetch.return_value = {
            "status_code": 200,
            "headers": {},
            "body": {"value": [], "@odata.nextLink": None}
        }

        result = await plugin.execute({"op": "digest"}, None, mock_host)

        assert result.status == "success"
        assert "ko" in result.data
        assert result.data["ko"]["type"] == "email_digest"
        assert "count" in result.data
        assert "window" in result.data

    @pytest.mark.asyncio
    async def test_ingest_routes_correctly(self, plugin, mock_host):
        """Test ingest operation routes to _execute_ingest."""
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "headers": {}, "body": {"value": [], "@odata.nextLink": None}},
            {"status_code": 200, "headers": {}, "body": {"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}}
        ]

        result = await plugin.execute({"op": "ingest", "kb_id": "test-kb"}, None, mock_host)

        assert result.status == "success"
        assert "count" in result.data
        assert result.data["count"] >= 0

    @pytest.mark.asyncio
    async def test_default_operation_is_ingest(self, plugin, mock_host):
        """Test default operation is ingest when op not specified."""
        mock_host.http.fetch.side_effect = [
            {"status_code": 200, "headers": {}, "body": {"value": [], "@odata.nextLink": None}},
            {"status_code": 200, "headers": {}, "body": {"value": [], "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc"}}
        ]

        result = await plugin.execute({"kb_id": "test-kb"}, None, mock_host)

        assert result.status == "success"
        assert "count" in result.data


class TestOutlookMailAuthentication:
    """Test authentication handling."""

    @pytest.mark.asyncio
    async def test_missing_oauth_token_returns_error(self, plugin, mock_host):
        """Test missing OAuth token returns auth error."""
        mock_host.auth.resolve_token_and_target.return_value = (None, None)

        result = await plugin.execute({"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "auth_missing_or_insufficient_scopes"
        assert "No Microsoft access token available" in result.error["message"]

    @pytest.mark.asyncio
    async def test_empty_access_token_returns_error(self, plugin, mock_host):
        """Test auth result without access_token field returns error."""
        mock_host.auth.resolve_token_and_target.return_value = ("", None)

        result = await plugin.execute({"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "auth_missing_or_insufficient_scopes"
        assert "No Microsoft access token available" in result.error["message"]

    @pytest.mark.asyncio
    async def test_oauth_resolution_exception(self, plugin, mock_host):
        """Test OAuth resolution exception returns auth error."""
        mock_host.auth.resolve_token_and_target.side_effect = Exception("OAuth service unavailable")

        result = await plugin.execute({"op": "list"}, None, mock_host)

        assert result.status == "error"
        assert result.error["code"] == "auth_missing_or_insufficient_scopes"
        assert "Failed to resolve Microsoft OAuth token" in result.error["message"]
