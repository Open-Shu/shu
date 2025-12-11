from __future__ import annotations
from typing import Any, Dict, Optional


# Local minimal result shim to avoid importing host internals
class _Result:
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None):
        return cls("success", data or {})

    @classmethod
    def err(cls, message: str, code: str = "tool_error", details: Optional[Dict[str, Any]] = None):
        return cls("error", error={"code": code, "message": message, "details": (details or {})})


class TestKoWritePlugin:
    name = "test_kowrite"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["run"], "default": "run", "x-ui": {"help": "Run the test KO write operation.", "enum_labels": {"run": "Run"}, "enum_help": {"run": "Write a single KO into the selected KB"}}},
                "kb_id": {"type": "string", "x-ui": {"hidden": True, "help": "Target Knowledge Base"}},
                "type": {"type": "string"},
                "external_id": {"type": "string"},
                "title": {"type": ["string", "null"]},
                "content": {"type": "string"},
                "attributes": {"type": "object"},
            },
            "required": ["kb_id", "type", "external_id", "content"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "ko_id": {"type": "string"},
            },
            "required": ["ko_id"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        kb = getattr(host, "kb", None)
        if not kb:
            return _Result.err("kb capability not available")

        ident = getattr(host, "identity", None)
        user_email = None
        if ident:
            try:
                ident_dict = ident.get_current_user_identity()
                user_email = ident_dict.get("email")
            except Exception:
                user_email = None

        kb_id = params.get("kb_id")
        ko_type = params.get("type")
        external_id = params.get("external_id")
        content = params.get("content")
        title = params.get("title")
        attributes = params.get("attributes") or {}

        if not kb_id or not ko_type or not external_id or content is None:
            return _Result.err("kb_id, type, external_id, and content are required")

        # Build KO
        ko = {
            "id": None,
            "type": str(ko_type),
            "source": {"plugin": self.name, "account": user_email},
            "external_id": str(external_id),
            "title": title,
            "content": str(content),
            "attributes": attributes,
        }

        try:
            ko_id = await kb.upsert_knowledge_object(kb_id, ko)
            return _Result.ok({"ko_id": ko_id})
        except Exception as e:
            return _Result.err(f"KO upsert failed: {e}")

