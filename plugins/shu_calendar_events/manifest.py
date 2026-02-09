PLUGIN_MANIFEST = {
    "name": "calendar_events",
    "display_name": "Google Calendar Events",
    "version": "1",
    "module": "plugins.shu_calendar_events.plugin:CalendarEventsPlugin",
    # Host capabilities:
    # - http: Google Calendar REST calls
    # - identity: default user_email for attribution/subject hints
    # - auth: OAuth (user) or domain delegation for Google APIs
    # - storage: small JSON storage (not strictly needed but useful for aux state)
    # - kb: write Knowledge Objects (KO) into a knowledge base
    # Note: 'cursor' is auto-included when 'kb' is declared
    "capabilities": ["http", "identity", "auth", "storage", "kb"],
    # Feed policy: ingestion is the background-safe operation
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],
    # Chat-callable safe operations (read-only)
    "chat_callable_ops": ["list"],
    # Identity requirements (user OAuth Calendar scopes)
    "required_identities": [
        {
            "provider": "google",
            "mode": "user",
            "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
        }
    ],
    # Per-op auth configuration
    "op_auth": {
        "list": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
            "subject_hint": "identity:google_email",
        },
        "ingest": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate"],
            "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
            "subject_hint": "identity:google_email",
        },
    },
}
