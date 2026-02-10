from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


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


class CalendarEventsPlugin:
    name = "calendar_events"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "ingest"],
                    "default": "ingest",
                    "x-ui": {"help": "Choose operation"},
                },
                "calendar_id": {
                    "type": ["string", "null"],
                    "default": "primary",
                    "x-ui": {"help": "Calendar ID (default: primary)"},
                },
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 336,
                    "default": 48,
                    "x-ui": {"help": "Look-back window in hours when no syncToken is present."},
                },
                "time_min": {"type": ["string", "null"], "x-ui": {"help": "ISO timeMin override (UTC)."}},
                "time_max": {"type": ["string", "null"], "x-ui": {"help": "ISO timeMax override (UTC)."}},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 250, "default": 50},
                "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
            },
            "required": [],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "events": {"type": "array"},
                "count": {"type": ["integer", "null"]},
                "deleted": {"type": ["integer", "null"]},
                "next_sync_token": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
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
            token, target = await auth.resolve_token_and_target("google")
            return token, target
        except Exception:
            return None, None

    async def _http_json(
        self, host: Any, method: str, url: str, headers: dict[str, str], params: dict[str, Any] | None = None
    ) -> Any:
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        resp = await host.http.fetch(method, url, **kwargs)
        # host.http raises on HTTP >= 400; at this point, resp should be a dict with 'body'
        body = resp.get("body")
        if isinstance(body, (dict, list)):
            return body
        try:
            import json

            return json.loads(body or "{}")
        except Exception:
            return {}

    def _window(self, since_hours: int, time_min: str | None, time_max: str | None) -> tuple[str, str]:
        """Compute time window for initial listing.
        If explicit time_min/time_max provided, honor them. Otherwise, include a
        symmetric window around now: [now - since_hours, now + since_hours]. This
        captures both recent past and near-future events out of the box.
        """
        if time_min and time_max:
            return time_min, time_max
        now = datetime.now(UTC)
        tmin = now - timedelta(hours=since_hours)
        tmax = now + timedelta(hours=since_hours)
        return tmin.isoformat().replace("+00:00", "Z"), tmax.isoformat().replace("+00:00", "Z")

    async def _list(
        self,
        host: Any,
        params: dict[str, Any],
        user_email: str | None,
        calendar_id: str,
        since_hours: int,
        time_min: str | None,
        time_max: str | None,
        max_results: int,
    ) -> _Result:
        token, target_user = await self._resolve_token_and_target(host, params or {}, op="list")
        if not token:
            return _Result.err("No Google access token available. Connect OAuth or configure host.auth.")
        base = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id or 'primary'}"
        headers = {"Authorization": f"Bearer {token}"}

        # If a syncToken exists, use delta listing
        sync_token: str | None = None
        try:
            if hasattr(host, "cursor"):
                sync_token = await host.cursor.get(params.get("kb_id") or "adhoc")
        except Exception:
            sync_token = None

        events: list[dict[str, Any]] = []
        next_sync: str | None = None

        if sync_token:
            page: str | None = None
            while True:
                query: dict[str, Any] = {
                    "syncToken": sync_token,
                    "showDeleted": True,
                    "singleEvents": True,
                    "maxResults": max_results,
                }
                if page:
                    query["pageToken"] = page
                data = await self._http_json(host, "GET", f"{base}/events", headers, params=query)
                events.extend(data.get("items", []) or [])
                next_sync = data.get("nextSyncToken") or next_sync
                page = data.get("nextPageToken")
                if not page:
                    break
            return _Result.ok({"events": events, "next_sync_token": next_sync, "count": len(events)})

        # Initial listing using time window
        tmin, tmax = self._window(since_hours, time_min, time_max)
        page: str | None = None
        while True:
            query: dict[str, Any] = {
                "timeMin": tmin,
                "timeMax": tmax,
                "singleEvents": True,
                "orderBy": "startTime",
                "showDeleted": True,
                "maxResults": max_results,
            }
            if page:
                query["pageToken"] = page
            data = await self._http_json(host, "GET", f"{base}/events", headers, params=query)
            events.extend(data.get("items", []) or [])
            next_sync = data.get("nextSyncToken") or next_sync
            page = data.get("nextPageToken")
            if not page:
                break
        return _Result.ok({"events": events, "next_sync_token": next_sync, "count": len(events)})

    async def _ingest(
        self,
        host: Any,
        params: dict[str, Any],
        user_email: str | None,
        kb_id: str | None,
        calendar_id: str,
        since_hours: int,
        time_min: str | None,
        time_max: str | None,
        max_results: int,
    ) -> _Result:
        if not kb_id:
            return _Result.err("kb_id is required for op=ingest (target Knowledge Base to write KOs)")
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        token, target_user = await self._resolve_token_and_target(host, params or {}, op="ingest")
        if not token:
            return _Result.err("No Google access token available for ingest. Connect OAuth or configure host.auth.")
        base = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id or 'primary'}"
        headers = {"Authorization": f"Bearer {token}"}

        async def _upsert_event(ev: dict[str, Any]) -> None:
            eid = ev.get("id")
            if not eid:
                return
            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
            summary = ev.get("summary") or "(no title)"
            desc = ev.get("description") or ""
            loc = ev.get("location")
            attendees = [a.get("email") for a in (ev.get("attendees") or []) if a.get("email")]
            await host.kb.ingest_text(
                kb_id,
                title=summary,
                content=(desc or summary or ""),
                source_id=eid,
                source_url=None,
                attributes={
                    "start": start,
                    "end": end,
                    "location": loc,
                    "attendees": attendees,
                    "status": ev.get("status"),
                    "calendar": (calendar_id or "primary"),
                    "plugin": self.name,
                },
            )

        # Try delta with syncToken from cursor
        reset_cursor = bool(params.get("reset_cursor"))
        existing_sync: str | None = None
        if hasattr(host, "cursor") and not reset_cursor:
            try:
                existing_sync = await host.cursor.get(kb_id)
            except Exception:
                existing_sync = None

        new_sync: str | None = None
        upserts = 0
        deleted = 0

        try:
            if existing_sync:
                page: str | None = None
                while True:
                    query: dict[str, Any] = {
                        "syncToken": existing_sync,
                        "showDeleted": True,
                        "singleEvents": True,
                        "maxResults": max_results,
                    }
                    if page:
                        query["pageToken"] = page
                    data = await self._http_json(host, "GET", f"{base}/events", headers, params=query)
                    for ev in data.get("items") or []:
                        if (ev.get("status") or "").lower() == "cancelled":
                            try:
                                await host.kb.delete_ko(external_id=ev.get("id"))
                                deleted += 1
                            except Exception:
                                pass
                        else:
                            await _upsert_event(ev)
                            upserts += 1
                    new_sync = data.get("nextSyncToken") or new_sync
                    page = data.get("nextPageToken")
                    if not page:
                        break
            else:
                # Initial windowed fetch
                tmin, tmax = self._window(since_hours, time_min, time_max)
                page: str | None = None
                while True:
                    query: dict[str, Any] = {
                        "timeMin": tmin,
                        "timeMax": tmax,
                        "singleEvents": True,
                        "orderBy": "startTime",
                        "showDeleted": True,
                        "maxResults": max_results,
                    }
                    if page:
                        query["pageToken"] = page
                    data = await self._http_json(host, "GET", f"{base}/events", headers, params=query)
                    for ev in data.get("items") or []:
                        if (ev.get("status") or "").lower() == "cancelled":
                            try:
                                await host.kb.delete_ko(external_id=ev.get("id"))
                                deleted += 1
                            except Exception:
                                pass
                        else:
                            await _upsert_event(ev)
                            upserts += 1
                    new_sync = data.get("nextSyncToken") or new_sync
                    page = data.get("nextPageToken")
                    if not page:
                        break
        except Exception as e:
            # If syncToken invalid/expired (HTTP 410 Gone), drop to full window next time
            msg = str(e)
            if "HTTP 410" in msg or "status_code': 410" in msg:
                existing_sync = None
            else:
                return _Result.err(
                    "Calendar API error during ingest", code="provider_error", details={"message": msg[:300]}
                )

        # Advance cursor if available
        try:
            if hasattr(host, "cursor") and new_sync:
                await host.cursor.set(kb_id, str(new_sync))
        except Exception:
            pass

        return _Result.ok({"count": upserts, "deleted": deleted, "next_sync_token": new_sync})

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> _Result:
        id_cap = getattr(host, "identity", None)
        user_email = (getattr(id_cap, "user_email", None) if id_cap else None) or (
            id_cap.get_primary_email("google") if id_cap and hasattr(id_cap, "get_primary_email") else None
        )

        op = (params.get("op") or "list").lower()
        calendar_id = params.get("calendar_id") or "primary"
        since_hours = int(params.get("since_hours", 48))
        time_min = params.get("time_min")
        time_max = params.get("time_max")
        max_results = int(params.get("max_results", 50))

        if op == "list":
            return await self._list(host, params, user_email, calendar_id, since_hours, time_min, time_max, max_results)
        if op == "ingest":
            return await self._ingest(
                host, params, user_email, params.get("kb_id"), calendar_id, since_hours, time_min, time_max, max_results
            )
        return _Result.err(f"Unsupported op: {op}")
