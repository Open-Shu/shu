# =============================================================================
# COOKIE-CUTTER MANIFEST — fields you MUST update when copying this template:
#
#   name          → unique snake_case identifier for your plugin
#                   (e.g. "my_github_plugin")
#   display_name  → human-readable name shown in the Shu UI
#   module        → dotted import path + class name for your plugin class
#                   format: "plugins.<package_name>.plugin:<ClassName>"
#                   (e.g. "plugins.my_github_plugin.plugin:MyGithubPlugin")
#   capabilities  → list of host capabilities your plugin actually uses
#                   allowed values: http, auth, kb, storage, secrets,
#                   cursor, cache, log, utils, identity, ocr
#   chat_callable_ops → ops that can be invoked from the chat interface
#   allowed_feed_ops  → ops that run as background feed jobs (if any)
#   op_auth           → per-op OAuth/auth requirements (if any)
# =============================================================================

PLUGIN_MANIFEST = {
    # -------------------------------------------------------------------------
    # MUST UPDATE: Replace with your plugin's unique snake_case identifier.
    # -------------------------------------------------------------------------
    "name": "echo_template",
    "display_name": "Echo Template",

    # -------------------------------------------------------------------------
    # MUST UPDATE: Replace with the actual dotted import path to your class.
    # Format: "plugins.<your_package>.plugin:<YourPluginClass>"
    # -------------------------------------------------------------------------
    "module": "plugins._cookiecutter.plugin:EchoPlugin",

    "version": "1",

    # -------------------------------------------------------------------------
    # MUST UPDATE: Declare only the capabilities your plugin actually uses.
    # Requesting unused capabilities will be flagged during contract validation.
    # -------------------------------------------------------------------------
    "capabilities": ["log", "http", "secrets"],

    # -------------------------------------------------------------------------
    # MUST UPDATE: List the ops that users can invoke from the chat interface.
    # All ops listed here must appear in get_schema()'s op enum.
    # -------------------------------------------------------------------------
    "chat_callable_ops": ["echo", "fetch"],

    # Optional: ops that run as background scheduled feed jobs.
    # "allowed_feed_ops": [],

    # Optional: per-op OAuth/auth requirements.
    # "op_auth": {
    #     "echo": {
    #         "provider": "google",
    #         "mode": "user",
    #         "scopes": ["https://www.googleapis.com/auth/..."],
    #     },
    # },
}
