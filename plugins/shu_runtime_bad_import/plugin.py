from __future__ import annotations

import importlib


class RuntimeBadImportPlugin:
    name = "test_runtime_bad_import"
    version = "0"

    def get_schema(self):
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Attempt an import that fails at runtime.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Trigger a runtime import error (for testing)"}}},
            },
            "required": [],
            "additionalProperties": True,
        }

    async def execute(self, params, context, host):
        # Dynamic import to evade static scan; should be blocked by runtime deny-hook
        importlib.import_module("shu.core.config")
        return {"status": "ok"}  # Should not be reached

