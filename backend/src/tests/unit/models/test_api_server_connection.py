"""Unit tests for ApiServerConnection model."""

from shu.models.api_server_connection import ApiServerConnection


class TestApiServerConnection:
    """Tests for ApiServerConnection model."""

    def test_table_name(self):
        """Table name is api_server_connections."""
        assert ApiServerConnection.__tablename__ == "api_server_connections"

    def test_instantiation_with_required_fields(self):
        """Model can be instantiated and required fields assigned."""
        conn = ApiServerConnection()
        conn.id = "conn-123"
        conn.name = "My API"
        conn.url = "https://api.example.com/openapi.json"

        assert conn.id == "conn-123"
        assert conn.name == "My API"
        assert conn.url == "https://api.example.com/openapi.json"

    def test_column_defaults_not_applied_without_db(self):
        """Default values (enabled, consecutive_failures, spec_type) are only
        applied on INSERT by SQLAlchemy, so they remain None without a session."""
        conn = ApiServerConnection()

        assert conn.enabled is None
        assert conn.consecutive_failures is None
        assert conn.spec_type is None

    def test_default_columns_have_correct_server_defaults(self):
        """Column definitions declare the expected defaults."""
        table = ApiServerConnection.__table__
        assert table.c.enabled.default.arg is True
        assert table.c.consecutive_failures.default.arg == 0
        assert table.c.spec_type.default.arg == "openapi"

    def test_nullable_fields_accept_none(self):
        """All nullable fields accept None without error."""
        conn = ApiServerConnection()
        conn.name = "Test"
        conn.url = "https://example.com"

        conn.import_source = None
        conn.tool_configs = None
        conn.discovered_tools = None
        conn.timeouts = None
        conn.response_size_limit_bytes = None
        conn.last_synced_at = None
        conn.last_error = None
        conn.auth_config = None
        conn.base_url = None

        assert conn.import_source is None
        assert conn.tool_configs is None
        assert conn.discovered_tools is None
        assert conn.timeouts is None
        assert conn.response_size_limit_bytes is None
        assert conn.last_synced_at is None
        assert conn.last_error is None
        assert conn.auth_config is None
        assert conn.base_url is None

    def test_json_fields_accept_dicts(self):
        """JSON columns can hold dict values."""
        conn = ApiServerConnection()
        conn.import_source = {"type": "github", "repo": "org/repo"}
        conn.tool_configs = {"tool_a": {"enabled": True}}
        conn.discovered_tools = [{"name": "listUsers", "method": "GET"}]
        conn.timeouts = {"connect": 5, "read": 30}
        conn.auth_config = {"type": "bearer", "token_env": "API_TOKEN"}

        assert conn.import_source["type"] == "github"
        assert conn.tool_configs["tool_a"]["enabled"] is True
        assert conn.discovered_tools[0]["name"] == "listUsers"
        assert conn.timeouts["read"] == 30
        assert conn.auth_config["type"] == "bearer"

    def test_to_dict_includes_all_columns(self):
        """to_dict returns a dict containing every column from the model."""
        conn = ApiServerConnection()
        conn.id = "conn-abc"
        conn.name = "Petstore"
        conn.url = "https://petstore.swagger.io/v2/swagger.json"
        conn.spec_type = "openapi"
        conn.enabled = True
        conn.consecutive_failures = 0
        conn.base_url = "https://petstore.swagger.io/v2"
        conn.last_error = None

        result = conn.to_dict()

        expected_keys = {
            "id",
            "created_at",
            "updated_at",
            "name",
            "url",
            "spec_type",
            "import_source",
            "tool_configs",
            "discovered_tools",
            "timeouts",
            "response_size_limit_bytes",
            "enabled",
            "last_synced_at",
            "last_error",
            "consecutive_failures",
            "auth_config",
            "base_url",
        }
        assert expected_keys == set(result.keys())
        assert result["id"] == "conn-abc"
        assert result["name"] == "Petstore"
        assert result["url"] == "https://petstore.swagger.io/v2/swagger.json"
        assert result["spec_type"] == "openapi"
        assert result["enabled"] is True
        assert result["consecutive_failures"] == 0
        assert result["base_url"] == "https://petstore.swagger.io/v2"

    def test_repr(self):
        """__repr__ includes class name and id."""
        conn = ApiServerConnection()
        conn.id = "conn-repr"

        assert repr(conn) == "<ApiServerConnection(id=conn-repr)>"
