from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List, Tuple


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


class GChatDigestPlugin:

    name = "gchat_digest"
    version = "1"

    def get_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {"type": ["string", "null"], "enum": ["list", "ingest"], "default": "ingest", "x-ui": {"help": "Choose operation"}},
                "since_hours": {"type": "integer", "minimum": 1, "maximum": 336, "default": 168, "x-ui": {"help": "Look-back window in hours for recent messages."}},
                "max_spaces": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "max_messages_per_space": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
            },
            "required": [],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "messages": {"type": "array"},
                "count": {"type": ["integer", "null"]},
                "note": {"type": ["string", "null"]},
                "last_ts": {"type": ["string", "null"]},
            },
            "required": [],
            "additionalProperties": True,
        }

    async def _resolve_token_and_target(self, host: Any, params: Dict[str, Any], *, op: str) -> tuple[Optional[str], Optional[str]]:
        auth = getattr(host, "auth", None)
        if not auth:
            return None, None
        try:
            # Executor injects provider/mode/subject/scopes into host auth context from manifest
            token, target = await auth.resolve_token_and_target("google")
            return token, target
        except Exception:
            return None, None

    async def _http_json(self, host: Any, method: str, url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
        kwargs: Dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        resp = await host.http.fetch(method, url, **kwargs)
        # host.http raises on HTTP >= 400; at this point, resp should be a dict
        body = resp.get("body")
        if isinstance(body, (dict, list)):
            return body
        try:
            import json
            return json.loads(body or "{}")
        except Exception:
            return {}

    async def _list_spaces(self, host: Any, headers: Dict[str, str], max_spaces: int) -> List[str]:
        spaces: List[str] = []
        page: Optional[str] = None
        base = "https://chat.googleapis.com/v1"
        while True and len(spaces) < max_spaces:
            params = {"pageSize": min(100, max_spaces - len(spaces))}
            if page:
                params["pageToken"] = page
            data = await self._http_json(host, "GET", f"{base}/spaces", headers, params=params)
            for s in (data.get("spaces") or []):
                name = s.get("name")  # e.g., "spaces/AAA..."
                if name:
                    spaces.append(name)
            page = data.get("nextPageToken")
            if not page or len(spaces) >= max_spaces:
                break
        return spaces

    def _window(self, since_hours: int) -> str:
        tmin = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        return tmin.isoformat().replace("+00:00", "Z")

    async def _resolve_sender(self, host: Any, sender: Dict[str, Any]) -> Dict[str, Any]:
        # Best-effort: use cache; try Admin Directory if token scoped; fallback gracefully
        sender_id = (sender.get("name") or sender.get("userId") or "").strip()  # e.g., users/123456
        display = sender.get("displayName")
        # Check cache first
        cache = getattr(host, "cache", None)
        cache_key = f"dir_user:{sender_id}" if sender_id else None
        if cache and cache_key:
            try:
                cached = await cache.get(cache_key)
                if cached:
                    return cached
            except Exception:
                pass
        # Try Admin Directory only if we have an email-like display or 'email' in sender
        email = sender.get("email")
        profile = {"id": sender_id or None, "displayName": display, "email": email}
        if email:
            try:
                token, _ = None, None
                # We don't need target for Admin SDK; reuse the same token
                # If scope is absent, call will 403 and we'll ignore
                # Use host.http directly
                headers = {}
                auth = getattr(host, "auth", None)
                if auth:
                    # Best-effort reuse token; do not enforce directory scope (optional enhancement)
                    token, _ = await auth.resolve_token_and_target("google")
                if token:
                    headers = {"Authorization": f"Bearer {token}"}
                    url = f"https://admin.googleapis.com/admin/directory/v1/users/{email}"
                    data = await self._http_json(host, "GET", url, headers)
                    profile = {
                        "id": data.get("id") or sender_id or None,
                        "displayName": data.get("name", {}).get("fullName") or display,
                        "email": data.get("primaryEmail") or email,
                    }
            except Exception:
                # Missing scope or 404; keep fallback
                pass
        # Cache result briefly
        if cache and cache_key:
            try:
                await cache.set(cache_key, profile, ttl_seconds=6 * 3600)
            except Exception:
                pass
        return profile

    async def _list(self, host: Any, params: Dict[str, Any], since_hours: int, max_spaces: int, max_messages_per_space: int) -> _Result:
        token, _ = await self._resolve_token_and_target(host, params or {}, op="list")
        if not token:
            return _Result.err("No Google access token available. Connect OAuth or configure host.auth.")
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://chat.googleapis.com/v1"
        tmin_iso = self._window(since_hours)

        spaces = await self._list_spaces(host, headers, max_spaces)
        out: List[Dict[str, Any]] = []
        for sp in spaces:
            page: Optional[str] = None
            fetched = 0
            while True and fetched < max_messages_per_space:
                q: Dict[str, Any] = {"pageSize": min(100, max_messages_per_space - fetched)}
                if page:
                    q["pageToken"] = page
                data = await self._http_json(host, "GET", f"{base}/{sp}/messages", headers, params=q)
                msgs = data.get("messages") or []
                for m in msgs:
                    ctime = m.get("createTime")
                    if ctime and ctime < tmin_iso:
                        continue
                    sender = await self._resolve_sender(host, m.get("sender") or {})
                    out.append({
                        "name": m.get("name"),
                        "createTime": ctime,
                        "text": m.get("text"),
                        "space": sp,
                        "sender": sender,
                    })
                fetched += len(msgs)
                page = data.get("nextPageToken")
                if not page:
                    break
        out.sort(key=lambda x: x.get("createTime") or "", reverse=True)
        return _Result.ok({"messages": out, "count": len(out), "last_ts": (out[0].get("createTime") if out else None)})

    async def _ingest(self, host: Any, params: Dict[str, Any], kb_id: Optional[str], since_hours: int, max_spaces: int, max_messages_per_space: int) -> _Result:
        if not kb_id:
            return _Result.err("kb_id is required for op=ingest (target Knowledge Base to write KOs)")
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        token, _ = await self._resolve_token_and_target(host, params or {}, op="ingest")
        if not token:
            return _Result.err("No Google access token available for ingest. Connect OAuth or configure host.auth.")
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://chat.googleapis.com/v1"

        # Watermark via cursor: ISO last_ts
        reset_cursor = bool(params.get("reset_cursor"))
        last_ts: Optional[str] = None
        if hasattr(host, "cursor") and not reset_cursor:
            try:
                last_ts = await host.cursor.get(kb_id)
            except Exception:
                last_ts = None
        tmin_iso = last_ts or self._window(since_hours)

        spaces = await self._list_spaces(host, headers, max_spaces)
        upserts = 0
        newest_ts = last_ts
        for sp in spaces:
            page: Optional[str] = None
            fetched = 0
            while True and fetched < max_messages_per_space:
                q: Dict[str, Any] = {"pageSize": min(100, max_messages_per_space - fetched)}
                if page:
                    q["pageToken"] = page
                data = await self._http_json(host, "GET", f"{base}/{sp}/messages", headers, params=q)
                msgs = data.get("messages") or []
                for m in msgs:
                    ctime = m.get("createTime")
                    if ctime and ctime < tmin_iso:
                        continue
                    sender = await self._resolve_sender(host, m.get("sender") or {})
                    title = (m.get("text") or "").strip()[:80] or "(message)"
                    await host.kb.ingest_text(
                        kb_id,
                        title=title,
                        content=m.get("text") or "",
                        source_id=m.get("name") or "(missing-id)",
                        source_url=None,
                        attributes={"space": sp, "createTime": ctime, "sender": sender},
                    )
                    upserts += 1
                    if ctime and ((newest_ts or "") < ctime):
                        newest_ts = ctime
                fetched += len(msgs)
                page = data.get("nextPageToken")
                if not page:
                    break
        # Advance cursor
        try:
            if hasattr(host, "cursor") and newest_ts:
                await host.cursor.set(kb_id, newest_ts)
        except Exception:
            pass

        return _Result.ok({"count": upserts, "last_ts": newest_ts})

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        op = (params.get("op") or "list").lower()
        since_hours = int(params.get("since_hours", 168))
        max_spaces = int(params.get("max_spaces", 50))
        max_messages_per_space = int(params.get("max_messages_per_space", 100))
        if op == "list":
            return await self._list(host, params, since_hours, max_spaces, max_messages_per_space)
        if op == "ingest":
            return await self._ingest(host, params, params.get("kb_id"), since_hours, max_spaces, max_messages_per_space)
        return _Result.err(f"Unsupported op: {op}")

