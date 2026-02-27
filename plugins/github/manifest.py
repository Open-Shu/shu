PLUGIN_MANIFEST = {
    "name": "github",
    "display_name": "GitHub",
    "version": "1",
    # Dotted import path used by the Shu plugin loader to instantiate the class.
    "module": "plugins.github.plugin:GithubPlugin",
    # Capability whitelist: only request capabilities the plugin actually uses.
    # - http: GitHub REST API calls
    # - secrets: retrieve GitHub PAT via host.secrets.get("github_pat")
    # - log: structured logging via host.log
    "capabilities": ["http", "secrets", "log"],
    # Chat-callable operations: can be invoked from the chat interface.
    "chat_callable_ops": ["fetch_activity"],
    # Feed-safe operations: can be scheduled as background feed jobs.
    "allowed_feed_ops": ["fetch_activity"],
}
