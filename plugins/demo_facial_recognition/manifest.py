"""Manifest for Demo Facial Recognition Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_facial_recognition",
    "display_name": "Demo: Airport VIP Recognition",
    "version": "1.0.0",
    "module": "plugins.demo_facial_recognition.plugin:DemoFacialRecognitionPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "list",
    "allowed_feed_ops": ["list"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["list", "get_event"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {},
}
