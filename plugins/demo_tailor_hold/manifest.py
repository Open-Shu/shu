"""Manifest for Demo Tailor Hold Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_tailor_hold",
    "display_name": "Demo: Hotel Tailor Hold",
    "version": "1.0.0",
    "module": "plugins.demo_tailor_hold.plugin:DemoTailorHoldPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "check_availability",
    "allowed_feed_ops": ["check_availability"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["add_hold", "check_availability", "get_schedule"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {}
}
