PLUGIN_MANIFEST = {
    "name": "gchat_digest",
    "display_name": "Google Chat Digest",
    "version": "1",
    "module": "plugins.shu_gchat_digest.plugin:GChatDigestPlugin",
    # Capabilities used:
    # - http: Google Chat REST calls (and Admin Directory when available)
    # - identity: default user_email if needed for attribution
    # - auth: OAuth (user) or domain delegation; scopes declared below
    # - storage: auxiliary state
    # - kb: write Knowledge Objects
    # - cache: cache Admin Directory user lookups to avoid repeated requests
    # Note: 'cursor' is auto-included when 'kb' is declared
    "capabilities": ["http", "identity", "auth", "storage", "kb", "cache"],
    # Background-safe op
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],
    # Read-only op callable from chat
    "chat_callable_ops": ["list"],
    # Identity requirements (user OAuth, Chat read scopes)
    "required_identities": [
        {
            "provider": "google",
            "mode": "user",
            "scopes": [
                "https://www.googleapis.com/auth/chat.messages.readonly",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
            ],
        }
    ],
    # Per-op auth. Admin Directory scope is optional; plugin will fallback if missing.
    "op_auth": {
        "list": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": [
                "https://www.googleapis.com/auth/chat.messages.readonly",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
            ],
            "subject_hint": "identity:google_email",
        },
        "ingest": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": [
                "https://www.googleapis.com/auth/chat.messages.readonly",
                "https://www.googleapis.com/auth/chat.spaces.readonly",
            ],
            "subject_hint": "identity:google_email",
        },
    },
}
