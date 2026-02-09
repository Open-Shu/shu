PLUGIN_MANIFEST = {
    "name": "outlook_mail",
    "display_name": "Outlook Mail",
    "version": "1",
    "module": "plugins.shu_outlook_mail.plugin:OutlookMailPlugin",

    # Host capabilities required
    # - http: Microsoft Graph API calls
    # - identity: default user_email for attribution
    # - auth: OAuth (user) for Microsoft 365
    # - secrets: secure credential storage (if needed for future extensions)
    # - kb: write Knowledge Objects into a knowledge base
    # - cursor: delta sync state storage (auto-included with kb)
    "capabilities": ["http", "identity", "auth", "secrets", "kb", "cursor"],

    # Feed configuration
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],

    # Chat-callable (safe) operations
    "chat_callable_ops": ["list", "digest"],

    # Identity requirements for OAuth connection
    "required_identities": [
        {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Mail.Read"
            ]
        }
    ],

    # Per-operation auth (capability-driven)
    "op_auth": {
        "list": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Mail.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        },
        "digest": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Mail.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        },
        "ingest": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Mail.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        }
    }
}
