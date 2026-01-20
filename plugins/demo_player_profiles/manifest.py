"""Manifest for Demo Player Profiles Plugin."""

PLUGIN_MANIFEST = {
    "name": "demo_player_profiles",
    "display_name": "Demo: Player Profile Management",
    "version": "1.0.0",
    "module": "plugins.demo_player_profiles.plugin:DemoPlayerProfilesPlugin",
    # Demo plugin capabilities - minimal requirements
    # - kb: For potential knowledge base ingestion demos
    "capabilities": ["kb"],
    # Feeds policy: declare feed-safe operations
    "default_feed_op": "list",
    "allowed_feed_ops": ["list"],
    # Chat-callable operations for demo workflows
    "chat_callable_ops": ["get", "list", "search", "get_by_players"],
    # No authentication required for demo plugin (uses synthesized data)
    "required_identities": [],
    "op_auth": {}
}
