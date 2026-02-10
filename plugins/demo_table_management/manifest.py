"""Manifest for Demo Restaurant Management Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_restaurant_management",
    "display_name": "Demo: Restaurant Management System",
    "version": "1.0.0",
    "module": "plugins.demo_table_management.plugin:DemoRestaurantManagementPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "list_available",
    "allowed_feed_ops": ["list_available"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["list_available", "reserve", "cancel", "get_reservation"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {},
}
