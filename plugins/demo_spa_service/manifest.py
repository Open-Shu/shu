"""Manifest for Demo Spa Service Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_spa_service",
    "display_name": "Demo: Hotel Spa Service",
    "version": "1.0.0",
    "module": "plugins.demo_spa_service.plugin:DemoSpaServicePlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "check_availability",
    "allowed_feed_ops": ["check_availability"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["check_availability", "get_schedule", "reserve"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {},
}
