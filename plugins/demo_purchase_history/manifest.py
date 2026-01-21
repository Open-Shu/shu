"""Manifest for Demo Purchase History Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_purchase_history",
    "display_name": "Demo: Purchase History Tracking",
    "version": "1.0.0",
    "module": "plugins.demo_purchase_history.plugin:DemoPurchaseHistoryPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "get",
    "allowed_feed_ops": ["get"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["get"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {}
}
