from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


# Local minimal result shim to avoid importing host internals
class _Result:
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


class GmailDigestPlugin:
    name = "gmail_digest"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3360,
                    "default": 48,
                    "x-ui": {
                        "help": "Look-back window in hours; used to build newer_than:Xd when query_filter is empty."
                    },
                },
                "query_filter": {
                    "type": ["string", "null"],
                    "x-ui": {
                        "help": "Gmail search query (e.g., from:me is:unread). Requires appropriate Gmail read access. Leave blank to use newer_than derived from since_hours."
                    },
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                    "x-ui": {"help": "Max messages to inspect (capped at 500)."},
                },
                # Action/digest params
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "mark_read", "archive", "digest", "ingest"],
                    "default": "ingest",
                    "x-ui": {
                        "help": "Choose an operation.",
                        "enum_labels": {
                            "list": "List emails",
                            "mark_read": "Mark read",
                            "archive": "Archive",
                            "digest": "Digest summary",
                            "ingest": "Ingest to KB",
                        },
                        "enum_help": {
                            "list": "List recent messages (no changes)",
                            "mark_read": "Mark selected messages as read (approval required)",
                            "archive": "Remove Inbox label for selected messages (approval required)",
                            "digest": "Create a short inbox summary",
                            "ingest": "Ingest full email contents into KB as individual KOs",
                        },
                    },
                },
                "message_ids": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "x-ui": {"help": "For actions, provide Gmail message ids to modify."},
                },
                "preview": {
                    "type": ["boolean", "null"],
                    "default": None,
                    "x-ui": {"help": "When true with approve=false, returns a plan without side effects."},
                },
                "approve": {
                    "type": ["boolean", "null"],
                    "default": None,
                    "x-ui": {"help": "Set to true (with or without preview) to perform the action."},
                },
                "kb_id": {
                    "type": ["string", "null"],
                    "description": "Knowledge base ID to upsert digest KO into (required for op=digest)",
                    "x-ui": {"hidden": True, "help": "Target Knowledge Base for digest output."},
                },
            },
            "required": [],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "messages": {"type": "array"},
                "note": {"type": ["string", "null"]},
                "plan": {"type": ["object", "null"]},
                "result": {"type": ["object", "null"]},
            },
            "required": [],
            "additionalProperties": True,
        }

    async def _resolve_token_and_target(
        self, host: Any, params: dict[str, Any], *, op: str
    ) -> tuple[str | None, str | None]:
        auth = getattr(host, "auth", None)
        if not auth:
            return None, None
        try:
            # Prefer manifest-declared scopes for the op when resolving tokens
            scopes: list[str] | None = None
            try:
                oa = getattr(self, "_op_auth", None) or {}
                spec = oa.get(op) if isinstance(oa, dict) else None
                sc = (spec or {}).get("scopes") if isinstance(spec, dict) else None
                if isinstance(sc, list):
                    scopes = [str(s) for s in sc if s]
            except Exception:
                scopes = None
            token, target = await auth.resolve_token_and_target("google", scopes=scopes)
            return token, target
        except Exception:
            return None, None

    async def _http_json(
        self,
        host: Any,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
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

    async def _list(
        self,
        host: Any,
        params: dict[str, Any],
        user_email: str | None,
        query_filter: str,
        max_results: int,
        since_dt: datetime,
    ) -> _Result:
        token, target_user = await self._resolve_token_and_target(host, params or {}, op="list")
        if not token or not target_user:
            return _Result.err(
                "Missing Google auth or insufficient scopes for search.",
                code="auth_missing_or_insufficient_scopes",
            )
        base = f"https://gmail.googleapis.com/gmail/v1/users/{target_user}"
        headers = {"Authorization": f"Bearer {token}"}
        # Build query; rely on host.auth + manifest to enforce scopes (no plugin-side scope heuristics)
        params_q = {
            "maxResults": max_results,
            "includeSpamTrash": "false",
        }
        if query_filter:
            params_q["q"] = query_filter
        try:
            lst = await self._http_json(host, "GET", f"{base}/messages", headers, params=params_q)
        except Exception as e:
            # Inspect provider error body if available to map to a clearer auth error
            try:
                body = getattr(e, "body", None)
                prov_msg = None
                if isinstance(body, dict):
                    err = body.get("error") or {}
                    if isinstance(err, dict):
                        prov_msg = err.get("message") or str(err)
                if not prov_msg:
                    prov_msg = str(e)
            except Exception:
                prov_msg = str(e)
            # Only remap to insufficient_scopes when we actually requested q
            if params_q.get("q") and "Metadata scope does not support 'q' parameter" in (prov_msg or ""):
                return _Result.err(
                    "Google account is connected with metadata-only scope; gmail.readonly is required to use search (q). Reconnect with Gmail read access.",
                    code="auth_missing_or_insufficient_scopes",
                )
            raise
        ids: list[str] = [m.get("id") for m in lst.get("messages", []) if m.get("id")]
        if not ids:
            return _Result.ok({"messages": [], "note": "No messages matched the query"})
        messages: list[dict[str, Any]] = []
        for mid in ids[: min(len(ids), 50)]:
            m = await self._http_json(
                host,
                "GET",
                f"{base}/messages/{mid}",
                headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "To", "Cc", "Date"],
                },
            )
            payload = {
                "id": m.get("id"),
                "thread_id": m.get("threadId"),
                "label_ids": m.get("labelIds", []),
                "snippet": m.get("snippet"),
                "headers": m.get("payload", {}).get("headers", []),
                "internalDate": m.get("internalDate"),
            }
            messages.append(payload)

        def _to_dt(ms: str | None) -> datetime | None:
            try:
                return datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC) if ms else None
            except Exception:
                return None

        filtered = [m for m in messages if (dt := _to_dt(m.get("internalDate"))) is None or dt >= since_dt]
        filtered.sort(key=lambda m: _to_dt(m.get("internalDate")) or datetime.now(UTC), reverse=True)
        return _Result.ok({"messages": filtered})

    async def _build_action_plan(self, *, op: str, message_ids: list[str]) -> dict[str, Any]:
        action_desc = "mark_read" if op == "mark_read" else "archive"
        return {
            "action": action_desc,
            "message_count": len(message_ids),
            "message_ids": list(message_ids),
            "requires_approval": True,
        }

    async def _perform_action(
        self, host: Any, params: dict[str, Any], user_email: str | None, op: str, message_ids: list[str]
    ) -> dict[str, Any]:
        token, target_user = await self._resolve_token_and_target(host, params or {}, op=op)
        if not token or not target_user:
            raise RuntimeError("No Google access token available. Configure host.auth before performing actions.")
        base = f"https://gmail.googleapis.com/gmail/v1/users/{target_user}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        results: list[dict[str, Any]] = []
        for mid in message_ids:
            if op == "mark_read":
                body = {"removeLabelIds": ["UNREAD"]}
            elif op == "archive":
                body = {"removeLabelIds": ["INBOX"]}
            else:
                raise RuntimeError(f"Unsupported action op: {op}")
            try:
                resp = await self._http_json(host, "POST", f"{base}/messages/{mid}/modify", headers, json_body=body)
                results.append({"id": mid, "status": "ok", "result": resp})
            except Exception as e:
                results.append({"id": mid, "status": "error", "error": str(e)})
        return {"results": results}

    async def _digest(
        self,
        host: Any,
        params: dict[str, Any],
        user_email: str | None,
        kb_id: str | None,
        query_filter: str,
        max_results: int,
        since_dt: datetime,
        since_hours: int,
    ) -> _Result:
        # Fetch messages using the list path
        lst = await self._list(host, params, user_email, query_filter, max_results, since_dt)
        if lst.status != "success":
            return lst
        messages: list[dict[str, Any]] = (lst.data or {}).get("messages", [])
        # Optional incremental cursor via storage
        try:
            cursor_ms: str | None = None
            if hasattr(host, "storage"):
                cursor_ms = await host.storage.get("cursor_internalDate")
            if cursor_ms:
                try:
                    messages = [
                        m for m in messages if m.get("internalDate") and int(m["internalDate"]) > int(cursor_ms)
                    ]
                except Exception:
                    pass
        except Exception:
            pass

        # Utilities
        def _header(msg: dict[str, Any], name: str) -> str | None:
            for h in msg.get("headers", []) or []:
                if (h.get("name") or "").lower() == name.lower():
                    return h.get("value")
            return None

        # Summarize senders and top subjects
        from collections import Counter

        senders = [(_header(m, "From") or "Unknown") for m in messages]
        top_senders = Counter(senders).most_common(10)
        top_lines = [f"{cnt} - {sender}" for sender, cnt in top_senders]
        items = []
        for m in messages[: min(len(messages), 50)]:
            items.append(
                {
                    "id": m.get("id"),
                    "date": m.get("internalDate"),
                    "from": _header(m, "From"),
                    "subject": _header(m, "Subject"),
                    "snippet": m.get("snippet"),
                }
            )
        # Build KO
        now = datetime.now(UTC)
        # Determine account email for display/source attribution
        try:
            id_cap = getattr(host, "identity", None)
            account_email = (getattr(id_cap, "user_email", None) if id_cap else None) or (
                id_cap.get_primary_email("google") if id_cap and hasattr(id_cap, "get_primary_email") else None
            )
        except Exception:
            account_email = None
        title = f"Gmail Digest for {account_email or 'me'} ({since_hours}h)"
        expanded_top_lines = ["  - " + line for line in top_lines] if top_lines else ["  - (none)"]
        content_lines = [
            title,
            "",
            f"Total messages: {len(messages)}",
            "Top senders:",
            *expanded_top_lines,
            "",
            "Recent items:",
            *[
                f"  - {_header(m, 'Subject') or '(no subject)'} | {_header(m, 'From') or 'Unknown'}"
                for m in messages[: min(len(messages), 10)]
            ],
        ]
        content = "\n".join(content_lines)
        ko = {
            "type": "email_digest",
            "source": {"plugin": self.name, "account": account_email or "me"},
            "external_id": f"{(account_email or 'me')}:{int(since_dt.timestamp())}:{int(now.timestamp())}",
            "title": title,
            "content": content,
            "attributes": {
                "window": {"since": since_dt.isoformat(), "until": now.isoformat(), "hours": since_hours},
                "message_count": len(messages),
                "top_senders": top_senders,
                "items": items,
            },
        }
        return _Result.ok(
            {"ko": ko, "count": len(messages), "window": {"since": since_dt.isoformat(), "until": now.isoformat()}}
        )

    async def _ingest(
        self,
        host: Any,
        params: dict[str, Any],
        user_email: str | None,
        kb_id: str | None,
        query_filter: str,
        max_results: int,
        since_dt: datetime,
    ) -> _Result:
        if not kb_id:
            return _Result.err("kb_id is required for op=ingest (target Knowledge Base to write KOs)")
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        # Resolve auth and base
        token, target_user = await self._resolve_token_and_target(host, params or {}, op="ingest")
        if not token or not target_user:
            return _Result.err(
                "Missing Google auth or insufficient scopes for ingest.",
                code="auth_missing_or_insufficient_scopes",
            )
        base = f"https://gmail.googleapis.com/gmail/v1/users/{target_user}"
        headers = {"Authorization": f"Bearer {token}"}

        # Helpers
        import base64
        import re

        def _b64url_to_bytes(s: str | None) -> bytes:
            if not s:
                return b""
            s = s.replace("-", "+").replace("_", "/")
            pad = "=" * (-len(s) % 4)
            return base64.b64decode(s + pad)

        def _extract_text(payload: dict[str, Any]) -> str:
            # Prefer text/plain; fallback to stripped text/html; else aggregate parts
            def walk(p) -> list[dict[str, Any]]:
                parts = p.get("parts") or []
                out = []
                for part in parts:
                    out.append(part)
                    out.extend(walk(part))
                return out

            parts = [payload] + walk(payload)
            text_plain = []
            text_html = []
            for part in parts:
                mime = (part.get("mimeType") or "").lower()
                body = part.get("body", {}) or {}
                data = body.get("data")
                if not data:
                    continue
                raw = _b64url_to_bytes(data).decode("utf-8", errors="ignore")
                if mime == "text/plain":
                    text_plain.append(raw)
                elif mime == "text/html":
                    text_html.append(raw)
            if text_plain:
                return "\n\n".join(text_plain)
            if text_html:
                s = "\n".join(text_html)
                s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
                s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.IGNORECASE)
                s = re.sub(r"<[^>]+>", " ", s)
                s = re.sub(r"\s+", " ", s)
                return s.strip()
            return ""

        async def _ingest_message_by_id(mid: str) -> int | None:
            try:
                full = await self._http_json(host, "GET", f"{base}/messages/{mid}", headers, params={"format": "full"})
            except Exception:
                return None
            payload = full.get("payload") or {}
            content = _extract_text(payload)
            headers_list = full.get("payload", {}).get("headers", [])

            def _header(name: str) -> str | None:
                for h in headers_list or []:
                    if (h.get("name") or "").lower() == name.lower():
                        return h.get("value")
                return None

            def _split(addrs: str | None) -> list[str]:
                if not addrs:
                    return []
                return [a.strip() for a in str(addrs).split(",") if a and a.strip()]

            await host.kb.ingest_email(
                kb_id,
                subject=_header("Subject") or "(no subject)",
                sender=_header("From"),
                recipients={
                    "to": _split(_header("To")),
                    "cc": _split(_header("Cc")),
                    "bcc": _split(_header("Bcc")),
                },
                date=_header("Date"),
                message_id=mid,
                thread_id=full.get("threadId"),
                body_text=(content or (full.get("snippet") or "")),
                body_html=None,
                labels=(full.get("labelIds", [])),
                source_url=None,
                attributes={
                    "extraction_metadata": {
                        "headers": headers_list,
                        "internalDate": full.get("internalDate"),
                    }
                },
            )
            try:
                return int(full.get("internalDate") or 0)
            except Exception:
                return None

        # Try delta via Gmail History API using a cursor stored in host.cursor (scoped per feed+kb)
        reset_cursor = bool(params.get("reset_cursor"))
        existing_history: str | None = None
        if hasattr(host, "cursor") and not reset_cursor:
            try:
                existing_history = await host.cursor.get(kb_id)
            except Exception:
                existing_history = None

        new_history: str | None = None
        ingested = 0
        deleted = 0
        if existing_history:
            # Delta path: fetch changes since last historyId
            try:
                added_ids: list[str] = []
                deleted_ids: list[str] = []
                params_q: dict[str, Any] = {
                    "startHistoryId": existing_history,
                    "historyTypes": ["messageAdded", "messageDeleted"],
                }
                next_page: str | None = None
                while True:
                    if next_page:
                        params_q["pageToken"] = next_page
                    hist = await self._http_json(host, "GET", f"{base}/history", headers, params=params_q)
                    for h in hist.get("history") or []:
                        for a in h.get("messagesAdded") or []:
                            mid = (a.get("message") or {}).get("id")
                            if mid:
                                added_ids.append(mid)
                        for d in h.get("messagesDeleted") or []:
                            mid = (d.get("message") or {}).get("id")
                            if mid:
                                deleted_ids.append(mid)
                    new_history = hist.get("historyId") or new_history
                    next_page = hist.get("nextPageToken")
                    if not next_page:
                        break
                # Process deletions first
                for mid in deleted_ids:
                    try:
                        await host.kb.delete_ko(external_id=mid)
                        deleted += 1
                    except Exception:
                        pass
                # Process additions
                for mid in added_ids[:max_results]:
                    lm = await _ingest_message_by_id(mid)
                    ingested += 1 if lm is not None else 0
            except Exception:
                # Fallback to full ingest if delta fails
                existing_history = None

        if not existing_history:
            # Full list path with optional newer_than filter; then store current historyId
            lst = await self._list(host, params, user_email, query_filter, max_results, since_dt)
            if lst.status != "success":
                return lst
            messages: list[dict[str, Any]] = (lst.data or {}).get("messages", [])
            if not messages:
                # Still advance history cursor to current mailbox state
                try:
                    prof = await self._http_json(host, "GET", f"{base}/profile", headers)
                    new_history = prof.get("historyId") or None
                except Exception:
                    new_history = None
                return _Result.ok({"count": 0, "deleted": 0, "note": "No messages to ingest"})
            # Ingest messages
            last_ms = None
            for m in messages:
                mid = m.get("id")
                if not mid:
                    continue
                lm = await _ingest_message_by_id(mid)
                if lm is not None:
                    ingested += 1
                    last_ms = lm
            # Store current mailbox historyId for future deltas
            try:
                prof = await self._http_json(host, "GET", f"{base}/profile", headers)
                new_history = prof.get("historyId") or None
            except Exception:
                new_history = None

        # Advance history cursor
        try:
            if hasattr(host, "cursor") and new_history:
                await host.cursor.set(kb_id, str(new_history))
        except Exception:
            pass
        return _Result.ok({"count": ingested, "deleted": deleted, "history_id": new_history})

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> _Result:
        # Identity and params
        id_cap = getattr(host, "identity", None)
        user_email = (getattr(id_cap, "user_email", None) if id_cap else None) or (
            id_cap.get_primary_email("google") if id_cap and hasattr(id_cap, "get_primary_email") else None
        )

        op = (params.get("op") or "list").lower()
        since_hours = int(params.get("since_hours", 48))
        since_dt = datetime.now(UTC) - timedelta(hours=since_hours)
        newer_than_days = max(1, since_hours // 24)
        query_filter = params.get("query_filter") or f"newer_than:{newer_than_days}d"
        max_results = int(params.get("max_results", 50))

        if op == "list":
            return await self._list(host, params, user_email, query_filter, max_results, since_dt)
        if op == "digest":
            return await self._digest(
                host, params, user_email, params.get("kb_id"), query_filter, max_results, since_dt, since_hours
            )
        if op == "ingest":
            return await self._ingest(
                host, params, user_email, params.get("kb_id"), query_filter, max_results, since_dt
            )

        # Action ops: require message_ids
        if op in ("mark_read", "archive"):
            mids = params.get("message_ids") or []
            if not isinstance(mids, list) or not all(isinstance(x, str) for x in mids):
                return _Result.err("message_ids must be a list of ids for action operations")
            if not mids:
                return _Result.err("At least one message id is required for action operations")

            preview = params.get("preview")
            approve = params.get("approve")
            plan = await self._build_action_plan(op=op, message_ids=mids)

            # Preview-only path: no side effects
            if preview is True and not approve:
                return _Result.ok({"plan": plan})

            # Enforce approval gate
            if approve is not True:
                return _Result.err(
                    "approval_required: set approve=true to perform this action",
                    code="approval_required",
                    details={"plan": plan},
                )

            # Perform the action
            try:
                result = await self._perform_action(host, params, user_email, op, mids)
            except Exception as e:
                return _Result.err(str(e), code="action_failed", details={"plan": plan})
            return _Result.ok({"plan": plan, "result": result})

        return _Result.err(f"Unsupported op: {op}")
