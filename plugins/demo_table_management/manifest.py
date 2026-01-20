"""Manifest for Demo Table Management Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_table_management",
    "display_name": "Demo: Table Management System",
    "version": "1.0.0",
    "module": "plugins.demo_table_management.plugin:DemoTableManagementPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "list_available",
    "allowed_feed_ops": ["list_available"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["list_available", "reserve", "release", "get_status"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {}
}
