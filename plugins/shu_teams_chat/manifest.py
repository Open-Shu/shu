PLUGIN_MANIFEST = {
    "name": "teams_chat",
    "display_name": "Teams Chat",
    "version": "1",
    "module": "plugins.shu_teams_chat.plugin:TeamsChatPlugin",

    # Host capabilities required
    # - http: Microsoft Graph API calls
    # - identity: default user_email for attribution
    # - auth: OAuth (user) for Microsoft 365
    # - storage: auxiliary state storage
    # - kb: write Knowledge Objects into a knowledge base
    # - cursor: timestamp watermark storage (auto-included with kb)
    # - cache: cache user profile lookups to reduce API calls
    "capabilities": ["http", "identity", "auth", "storage", "kb", "cursor", "cache"],

    # Feed configuration
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],

    # Chat-callable (safe) operations
    "chat_callable_ops": ["list"],

    # Identity requirements (user OAuth with Chat and User read scopes)
    "required_identities": [
        {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Chat.Read",
                "https://graph.microsoft.com/User.Read"
            ]
        }
    ],

    # Per-operation auth configuration
    "op_auth": {
        "list": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Chat.Read",
                "https://graph.microsoft.com/User.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        },
        "ingest": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Chat.Read",
                "https://graph.microsoft.com/User.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        }
    }
}
