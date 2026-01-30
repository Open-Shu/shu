from __future__ import annotations

from typing import Any


class _Result:
    """Minimal result shim to avoid importing host internals."""

    def __init__(self, status: str, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None):
        self.status = status
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None):
        return cls("success", data or {})

    @classmethod
    def err(cls, message: str, code: str = "tool_error", details: dict[str, Any] | None = None):
        return cls("error", error={"code": code, "message": message, "details": (details or {})})


class SecretTestPlugin:
    """Test plugin for validating system vs user secret scoping.

    This plugin provides three operations that demonstrate different secret scoping behaviors:
    1. validate_api_key - Uses system_or_user scope (user secret preferred, system fallback)
    2. validate_user_token - Requires user-scoped secret only
    3. validate_system_credential - Requires system-scoped secret only
    """

    name = "shu_secret_test"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": [
                        "validate_api_key",
                        "validate_user_token",
                        "validate_system_credential",
                    ],
                    "description": "Operation to perform",
                    "x-ui": {
                        "help": "Select the secret validation operation to test",
                        "enum_labels": {
                            "validate_api_key": "Validate API Key (system_or_user)",
                            "validate_user_token": "Validate User Token (user only)",
                            "validate_system_credential": "Validate System Credential (system only)",
                        },
                        "enum_help": {
                            "validate_api_key": "Tests system_or_user scope - uses user secret if available, falls back to system secret",
                            "validate_user_token": "Tests user scope - requires user to configure their own token",
                            "validate_system_credential": "Tests system scope - requires admin to configure system-wide credential",
                        },
                    },
                }
            },
            "required": ["op"],
            "additionalProperties": False,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "operation": {"type": "string"},
                "secret_source": {"type": "string", "enum": ["user", "system", "not_found"]},
                "secret_value_masked": {"type": "string"},
                "validation_result": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["operation", "secret_source", "validation_result"],
            "additionalProperties": True,
        }

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> _Result:
        """Execute secret validation operations.

        Note: Secret enforcement happens BEFORE this method is called via ensure_secrets_for_plugin().
        If we reach this point, all required secrets are available.
        """
        op = params.get("op")
        if not op:
            return _Result.err("op parameter is required")

        secrets = getattr(host, "secrets", None)
        if not secrets:
            return _Result.err("secrets capability not available")

        # Map operations to their secret keys
        secret_key_map = {
            "validate_api_key": "api_key",
            "validate_user_token": "user_token",
            "validate_system_credential": "system_credential",
        }

        if op not in secret_key_map:
            return _Result.err(f"Unknown operation: {op}")

        secret_key = secret_key_map[op]

        # Retrieve the secret (user->system fallback happens automatically in SecretsCapability)
        secret_value = await secrets.get(secret_key)

        if secret_value is None:
            # This should not happen if enforcement is working correctly
            return _Result.ok(
                {
                    "operation": op,
                    "secret_source": "not_found",
                    "secret_value_masked": "(not found)",
                    "validation_result": "FAILED",
                    "message": f"Secret '{secret_key}' not found (enforcement should have prevented this)",
                }
            )

        # Determine source by checking if it starts with known prefixes
        # (This is a heuristic - in real scenarios you'd have metadata from the storage layer)
        # Default to "system" if we can't determine the source from the value
        secret_source = "system"  # Default assumption
        if secret_value.startswith("user-"):
            secret_source = "user"
        elif secret_value.startswith("system-"):
            secret_source = "system"
        # Note: For UAT, configure secrets with "user-" or "system-" prefix to see clear source indication

        # Mask the secret value for security (show only first 4 chars + length)
        # SECURITY: Never return full secrets in plugin output - they get logged and shown in UI
        masked_value = f"{secret_value[:4]}...({len(secret_value)} chars)"

        # Simulate validation (in real plugin, this would call external API)
        # In a real plugin, you'd use the secret_value to make an API call, not return it
        validation_result = "SUCCESS" if len(secret_value) > 0 else "FAILED"

        return _Result.ok(
            {
                "operation": op,
                "secret_source": secret_source,
                "secret_value_masked": masked_value,
                "validation_result": validation_result,
                "message": f"Successfully validated {secret_key} from {secret_source} scope",
            }
        )
