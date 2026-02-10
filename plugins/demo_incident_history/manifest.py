"""Manifest for Demo Incident History Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_incident_history",
    "display_name": "Demo: Incident History Tracking",
    "version": "1.0.0",
    "module": "plugins.demo_incident_history.plugin:DemoIncidentHistoryPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "list",
    "allowed_feed_ops": ["list"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["list", "search", "get_by_player"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {},
}
