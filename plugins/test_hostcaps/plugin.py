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


class TestHostcapsPlugin:
    name = "test_hostcaps"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["get_identity","secret_get","secret_set","storage_put","storage_get","cache_set","cache_get"], "default": "get_identity", "x-ui": {"help": "Exercise host capabilities.", "enum_labels": {"get_identity": "Get Identity", "secret_get": "Secret: Get", "secret_set": "Secret: Set", "storage_put": "Storage: Put", "storage_get": "Storage: Get", "cache_set": "Cache: Set", "cache_get": "Cache: Get"}, "enum_help": {"get_identity":"Fetch the primary identity (email) for a provider","secret_get":"Read a secret from host.secrets","secret_set":"Write a secret into host.secrets","storage_put":"Put an object into host.storage","storage_get":"Get an object from host.storage","cache_set":"Set a key in host.cache with optional TTL","cache_get":"Get a key from host.cache"}}},
                "key": {"type": ["string", "null"]},
                "value": {},
                "ttl": {"type": ["integer", "null"], "minimum": 1, "maximum": 86400},
            },
            "required": ["op"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "result": {},
            },
            "required": ["result"],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        op = params.get("op")
        key = params.get("key")
        if not op:
            return _Result.err("op is required")

        if op == "get_identity":
            ident = getattr(host, "identity", None)
            if not ident:
                return _Result.err("identity capability not available")
            return _Result.ok({"result": ident.get_current_user_identity()})

        if op == "secret_get":
            if not key:
                return _Result.err("key is required for secret_get")
            sec = getattr(host, "secrets", None)
            if not sec:
                return _Result.err("secrets capability not available")
            val = await sec.get(key)
            return _Result.ok({"result": val})

        if op == "secret_set":
            if not key:
                return _Result.err("key is required for secret_set")
            sec = getattr(host, "secrets", None)
            if not sec:
                return _Result.err("secrets capability not available")
            await sec.set(key, str(params.get("value", "")))
            return _Result.ok({"result": True})

        if op == "storage_put":
            if not key:
                return _Result.err("key is required for storage_put")
            st = getattr(host, "storage", None)
            if not st:
                return _Result.err("storage capability not available")
            await st.put(key, params.get("value"))
            return _Result.ok({"result": True})

        if op == "storage_get":
            if not key:
                return _Result.err("key is required for storage_get")
            st = getattr(host, "storage", None)
            if not st:
                return _Result.err("storage capability not available")
            val = await st.get(key)
            return _Result.ok({"result": val})

        if op == "cache_set":
            if not key:
                return _Result.err("key is required for cache_set")
            c = getattr(host, "cache", None)
            if not c:
                return _Result.err("cache capability not available")
            ttl = int(params.get("ttl", 60) or 60)
            await c.set(key, params.get("value"), ttl_seconds=ttl)
            return _Result.ok({"result": True})

        if op == "cache_get":
            if not key:
                return _Result.err("key is required for cache_get")
            c = getattr(host, "cache", None)
            if not c:
                return _Result.err("cache capability not available")
            val = await c.get(key)
            return _Result.ok({"result": val})

        return _Result.err(f"unknown op: {op}")

