"""Manifest for Demo Car Service Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_car_service",
    "display_name": "Demo: Luxury Car Service",
    "version": "1.0.0",
    "module": "plugins.demo_car_service.plugin:DemoCarServicePlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "check_availability",
    "allowed_feed_ops": ["check_availability"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["check_availability", "book", "cancel", "get_booking"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {},
}
