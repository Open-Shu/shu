PLUGIN_MANIFEST = {
    "name": "gmail_digest",
    "display_name": "Gmail Digest",
    "version": "1",
    "module": "plugins.shu_gmail_digest.plugin:GmailDigestPlugin",
    # Capability whitelist: enforce host-mediated access only
    # - http: Gmail REST calls
    # - identity: default user_email
    # - auth: service account domain delegation or OAuth flows
    # - secrets: store/retrieve OAuth client_id/secret and refresh_token
    # - storage: per-user/plugin small JSON storage (cursor for incremental digests)
    # - kb: write Knowledge Objects (KO) into a knowledge base
    "capabilities": ["http", "identity", "auth", "secrets", "storage", "kb"],
    # Feeds (background) policy: declare feed-safe ops
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],
    # Chat-callable (safe) operations for M1
    "chat_callable_ops": ["list", "digest"],
    # Identity requirements for host-auth UI/flow (user connection enables user_oauth mode)
    "required_identities": [
        {
            "provider": "google",
            "mode": "user",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
    ],
    # Per-op auth (capability-driven)
    "op_auth": {
        "list": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "subject_hint": "identity:google_email",
        },
        "digest": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "subject_hint": "identity:google_email",
        },
        "ingest": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "subject_hint": "identity:google_email",
        },
        "mark_read": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
            "subject_hint": "identity:google_email",
        },
        "archive": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
            "subject_hint": "identity:google_email",
        },
    },
}
