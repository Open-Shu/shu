PLUGIN_MANIFEST = {
    "name": "shu_secret_test",
    "version": "1",
    "label": "Secret Test Plugin",
    "description": "Test plugin for validating system vs user secret scoping in tool calling",
    "module": "plugins.shu_secret_test.plugin:SecretTestPlugin",
    "capabilities": ["secrets"],
    "chat_callable_ops": ["validate_api_key", "validate_user_token", "validate_system_credential"],
    "op_auth": {
        "validate_api_key": {
            "secrets": {
                "api_key": {
                    "allowed_scope": "system_or_user",
                    "description": "API key for external service validation",
                }
            }
        },
        "validate_user_token": {
            "secrets": {
                "user_token": {
                    "allowed_scope": "user",
                    "description": "User-specific authentication token",
                }
            }
        },
        "validate_system_credential": {
            "secrets": {
                "system_credential": {
                    "allowed_scope": "system",
                    "description": "System-wide credential (admin only)",
                }
            }
        },
    },
}
