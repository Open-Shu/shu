PLUGIN_MANIFEST = {
    "name": "github",
    "display_name": "GitHub",
    "version": "1",
    # Dotted import path used by the Shu plugin loader to instantiate the class.
    "module": "plugins.github.plugin:GithubPlugin",
    # Capability whitelist: only request capabilities the plugin actually uses.
    # - http: GitHub REST API calls
    # - secrets: PAT and GitHub username via host.secrets
    # - log: structured logging via host.log
    "capabilities": ["http", "secrets", "log"],
    # Chat-callable operations: can be invoked from the chat interface.
    "chat_callable_ops": ["fetch_activity"],
    # Feed-safe operations: can be scheduled as background feed jobs.
    "allowed_feed_ops": ["fetch_activity"],
    # Per-op secret requirements.  The subscription UI prompts users to
    # configure secrets with "user" or "system_or_user" scope.
    # ensure_secrets_for_plugin() enforces presence before execute().
    "op_auth": {
        "fetch_activity": {
            "secrets": {
                "github_pat": {
                    "allowed_scope": "system_or_user",
                    "description": "GitHub Personal Access Token (fine-grained, read-only)",
                },
                "github_username": {
                    "allowed_scope": "user",
                    "description": "Your GitHub username",
                },
            }
        }
    },
}
