PLUGIN_MANIFEST = {
    "name": "outlook_calendar",
    "display_name": "Outlook Calendar",
    "version": "1",
    "module": "plugins.shu_outlook_calendar.plugin:OutlookCalendarPlugin",

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

    # Chat-callable (safe) operations - list only, no digest for calendar
    "chat_callable_ops": ["list"],

    # Identity requirements for OAuth connection
    "required_identities": [
        {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Calendars.Read"
            ]
        }
    ],

    # Per-operation auth (capability-driven)
    "op_auth": {
        "list": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Calendars.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        },
        "ingest": {
            "provider": "microsoft",
            "mode": "user",
            "scopes": [
                "https://graph.microsoft.com/Calendars.Read"
            ],
            "subject_hint": "identity:microsoft_email"
        }
    }
}
