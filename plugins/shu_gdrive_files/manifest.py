PLUGIN_MANIFEST = {
    "name": "gdrive_files",
    "display_name": "Google Drive Files",
    "version": "1",
    "module": "plugins.shu_gdrive_files.plugin:GoogleDriveFilesPlugin",
    # Host-mediated access only
    # - http: Google Drive REST calls
    # - identity: default user_email for attribution
    # - auth: OAuth (user) or service account domain delegation
    # - storage: per-user/plugin small JSON storage (cursor for incremental sync)
    # - kb: write Knowledge Objects into a knowledge base
    # - ocr: text extraction/OCR via host capability (policy enforced by host.kb)
    # Note: 'cursor' is auto-included when 'kb' is declared
    "capabilities": ["http", "identity", "auth", "storage", "kb", "ocr"],
    # Feeds policy
    "default_feed_op": "ingest",
    "allowed_feed_ops": ["ingest"],
    # Identity requirements (user OAuth Drive scopes)
    "required_identities": [
        {
            "provider": "google",
            "mode": "user",
            "scopes": [
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/drive.metadata.readonly"
            ]
        }
    ],
    # Per-op auth (capability-driven)
    "op_auth": {
        "ingest": {
            "provider": "google",
            "mode": "user",
            "allowed_modes": ["user", "domain_delegate", "service_account"],
            "scopes": [
                "https://www.googleapis.com/auth/drive"
            ]
        }
    },
}

