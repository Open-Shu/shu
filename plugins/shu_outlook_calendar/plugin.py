from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
from urllib.parse import quote
import json


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


class OutlookCalendarPlugin:
    """Microsoft Outlook Calendar plugin for listing and ingesting calendar events."""

    name = "outlook_calendar"
    version = "1"

    def _build_odata_query_string(self, params: Dict[str, str]) -> str:
        """Build OData query string with proper URL encoding for Microsoft Graph API."""
        parts = []
        for key, value in params.items():
            encoded_value = quote(str(value), safe=",-/:.'()T")
            parts.append(f"{key}={encoded_value}")
        return "&".join(parts)

    def _parse_exception_details(self, exception: Exception) -> tuple[str, str, Optional[Dict[str, Any]]]:
        """Parse exception into error code, message, and details."""
        error_str = str(exception)
        if "|" in error_str:
            code_msg, details_json = error_str.split("|", 1)
            try:
                details = json.loads(details_json)
            except Exception:
                details = None
        else:
            code_msg = error_str
            details = None
        if ":" in code_msg:
            code, message = code_msg.split(":", 1)
            return code.strip(), message.strip(), details
        return "execution_error", code_msg, details

    def _window(self, since_hours: int, time_min: Optional[str], time_max: Optional[str]) -> tuple[str, str]:
        """Compute symmetric time window (past + future) for calendar events."""
        if time_min and time_max:
            return time_min, time_max
        now = datetime.now(timezone.utc)
        tmin = now - timedelta(hours=since_hours)
        tmax = now + timedelta(hours=since_hours)
        return tmin.isoformat().replace("+00:00", "Z"), tmax.isoformat().replace("+00:00", "Z")

    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin parameters."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "ingest"],
                    "default": "ingest",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {"list": "List Events", "ingest": "Ingest to Knowledge Base"},
                        "enum_help": {
                            "list": "Fetch and return recent events without storing",
                            "ingest": "Ingest calendar events into knowledge base"
                        }
                    }
                },
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 336,
                    "default": 48,
                    "x-ui": {"help": "Look-back/ahead window in hours (symmetric: past and future)"}
                },
                "time_min": {"type": ["string", "null"], "x-ui": {"help": "ISO timeMin override (UTC)"}},
                "time_max": {"type": ["string", "null"], "x-ui": {"help": "ISO timeMax override (UTC)"}},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                    "x-ui": {"help": "Maximum number of events to return"}
                },
                "kb_id": {"type": ["string", "null"], "x-ui": {"hidden": True}},
                "reset_cursor": {
                    "type": "boolean",
                    "default": False,
                    "x-ui": {"help": "Reset sync cursor and perform full re-ingestion"}
                }
            },
            "required": [],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin output."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "events": {"type": "array"},
                "count": {"type": ["integer", "null"]},
                "deleted": {"type": ["integer", "null"]},
                "next_sync_token": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "window": {
                    "type": ["object", "null"],
                    "properties": {"since": {"type": "string"}, "until": {"type": "string"}, "hours": {"type": "integer"}}
                },
                "diagnostics": {"type": "array", "items": {"type": "string"}}
            },
            "required": [],
            "additionalProperties": True,
        }

    # Placeholder methods to be implemented
    async def _graph_api_request(self, host: Any, access_token: str, endpoint: str,
                                  method: str = "GET", params: Optional[Dict[str, Any]] = None,
                                  body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a request to Microsoft Graph API with error handling."""
        base_url = "https://graph.microsoft.com/v1.0"
        url = f"{base_url}{endpoint}"

        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        try:
            response = await host.http.fetch(
                method=method, url=url, headers=headers, params=params or {}, json=body
            )

            status_code = response.get("status_code") or response.get("status")

            if status_code == 401:
                provider_msg = response.get("error", {}).get("message", "")
                details = {"http_status": 401}
                if provider_msg:
                    details["provider_message"] = provider_msg
                raise Exception(f"auth_missing_or_insufficient_scopes:Authentication failed. Please reconnect your Microsoft account.|{json.dumps(details)}")
            elif status_code == 403:
                provider_msg = response.get("error", {}).get("message", "")
                details = {"http_status": 403}
                if provider_msg:
                    details["provider_message"] = provider_msg
                raise Exception(f"insufficient_permissions:Insufficient permissions. Required scope: Calendars.Read|{json.dumps(details)}")
            elif status_code == 410:
                provider_msg = response.get("error", {}).get("message", "")
                details = {"http_status": 410}
                if provider_msg:
                    details["provider_message"] = provider_msg
                raise Exception(f"delta_token_expired:Delta sync token expired. Will perform full sync.|{json.dumps(details)}")
            elif status_code == 429:
                provider_msg = response.get("error", {}).get("message", "")
                details = {"http_status": 429}
                if provider_msg:
                    details["provider_message"] = provider_msg
                raise Exception(f"rate_limit_exceeded:Rate limit exceeded. Please try again later.|{json.dumps(details)}")
            elif status_code and status_code >= 500:
                error_msg = response.get("error", {}).get("message", "Unknown server error")
                details = {"http_status": status_code, "provider_message": error_msg}
                raise Exception(f"server_error:Microsoft Graph API error: {status_code} - {error_msg}|{json.dumps(details)}")
            elif status_code and status_code >= 400:
                error_msg = response.get("error", {}).get("message", "Unknown error")
                details = {"http_status": status_code, "provider_message": error_msg}
                raise Exception(f"api_error:Microsoft Graph API error: {status_code} - {error_msg}|{json.dumps(details)}")

            return response

        except Exception as e:
            error_str = str(e)
            if ":" in error_str and error_str.split(":")[0] in [
                "auth_missing_or_insufficient_scopes", "insufficient_permissions",
                "delta_token_expired", "rate_limit_exceeded", "server_error", "api_error"
            ]:
                raise
            details = {"exception_type": type(e).__name__}
            raise Exception(f"network_error:Network error communicating with Microsoft Graph API: {error_str}|{json.dumps(details)}")

    async def _fetch_all_pages(self, host: Any, access_token: str, initial_url: str,
                                max_results: Optional[int] = None) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch all pages from a paginated Graph API response. Returns (items, delta_link)."""
        all_items = []
        next_url = initial_url
        delta_link = None

        if not next_url.startswith("http"):
            next_url = f"https://graph.microsoft.com/v1.0{next_url}"

        while next_url:
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

            try:
                response = await host.http.fetch(method="GET", url=next_url, headers=headers)
                status_code = response.get("status_code") or response.get("status")

                if status_code and status_code >= 400:
                    body = response.get("body", {})
                    provider_msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else str(body)[:200]

                    if status_code == 401:
                        raise Exception(f"auth_missing_or_insufficient_scopes:Authentication failed|{json.dumps({'http_status': 401, 'provider_message': provider_msg})}")
                    elif status_code == 403:
                        raise Exception(f"insufficient_permissions:Insufficient permissions|{json.dumps({'http_status': 403, 'provider_message': provider_msg})}")
                    elif status_code == 410:
                        raise Exception(f"delta_token_expired:Delta sync token expired|{json.dumps({'http_status': 410, 'provider_message': provider_msg})}")
                    elif status_code == 429:
                        raise Exception(f"rate_limit_exceeded:Rate limit exceeded|{json.dumps({'http_status': 429, 'provider_message': provider_msg})}")
                    else:
                        raise Exception(f"api_error:API error: {status_code}|{json.dumps({'http_status': status_code, 'provider_message': provider_msg})}")

            except Exception as e:
                if hasattr(e, 'status_code') and hasattr(e, 'body'):
                    status_code = e.status_code
                    body = e.body
                    provider_msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else str(body)[:200]

                    if status_code == 410:
                        raise Exception(f"delta_token_expired:Delta sync token expired|{json.dumps({'http_status': 410, 'provider_message': provider_msg})}")
                    raise Exception(f"api_error:API error: {status_code}|{json.dumps({'http_status': status_code, 'provider_message': provider_msg})}")

                error_str = str(e)
                if ":" in error_str and error_str.split(":")[0] in [
                    "auth_missing_or_insufficient_scopes", "insufficient_permissions",
                    "delta_token_expired", "rate_limit_exceeded", "server_error", "api_error"
                ]:
                    raise
                raise Exception(f"network_error:Network error: {error_str}|{json.dumps({'exception_type': type(e).__name__})}")

            body = response.get("body", {})
            if isinstance(body, dict):
                items = body.get("value", [])
                delta_link = body.get("@odata.deltaLink") or delta_link
            else:
                items = []
            all_items.extend(items)

            if max_results and len(all_items) >= max_results:
                all_items = all_items[:max_results]
                break

            next_url = body.get("@odata.nextLink") if isinstance(body, dict) else None

        return all_items, delta_link

    async def _execute_list(self, params: Dict[str, Any], context: Any, host: Any, access_token: str) -> _Result:
        """Execute list operation to fetch calendar events using calendarView endpoint."""
        since_hours = params.get("since_hours", 48)
        time_min = params.get("time_min")
        time_max = params.get("time_max")
        max_results = params.get("max_results", 50)

        # Compute symmetric time window
        tmin, tmax = self._window(since_hours, time_min, time_max)

        # Build calendarView query with time range
        # Microsoft Graph calendarView gives expanded view of recurring events
        query_params = {
            "$select": "id,subject,start,end,location,bodyPreview,attendees,organizer,isCancelled,webLink,onlineMeeting,body",
            "$orderby": "start/dateTime",
            "$top": str(max_results),
            "startDateTime": tmin,
            "endDateTime": tmax
        }

        query_string = self._build_odata_query_string(query_params)
        endpoint = f"/me/calendarView?{query_string}"

        try:
            events, _ = await self._fetch_all_pages(host, access_token, endpoint, max_results)

            # Transform events to normalized format
            normalized_events = []
            for ev in events:
                normalized_events.append({
                    "id": ev.get("id"),
                    "subject": ev.get("subject"),
                    "start": ev.get("start", {}),
                    "end": ev.get("end", {}),
                    "location": ev.get("location", {}).get("displayName"),
                    "bodyPreview": ev.get("bodyPreview"),
                    "attendees": [a.get("emailAddress", {}).get("address") for a in ev.get("attendees", []) if a.get("emailAddress")],
                    "organizer": ev.get("organizer", {}).get("emailAddress", {}).get("address"),
                    "isCancelled": ev.get("isCancelled", False),
                    "webLink": ev.get("webLink"),
                    "onlineMeetingUrl": (ev.get("onlineMeeting") or {}).get("joinUrl")
                })

            return _Result.ok({
                "events": normalized_events,
                "count": len(normalized_events),
                "window": {"since": tmin, "until": tmax, "hours": since_hours}
            })

        except Exception as e:
            code, message, details = self._parse_exception_details(e)
            if details:
                return _Result.err(message, code=code, details=details)
            return _Result.err(message, code=code)

    async def _execute_ingest(self, params: Dict[str, Any], context: Any, host: Any, access_token: str) -> _Result:
        """Execute ingest operation to add events to knowledge base with delta sync support."""
        kb_id = params.get("kb_id")
        if not kb_id:
            return _Result.err("kb_id is required for op=ingest", code="missing_parameter")

        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available", code="missing_capability")

        since_hours = params.get("since_hours", 48)
        time_min = params.get("time_min")
        time_max = params.get("time_max")
        max_results = params.get("max_results", 50)
        reset_cursor = params.get("reset_cursor", False)

        # Try to get existing delta cursor
        cursor_data = None
        use_delta_sync = False

        if hasattr(host, "cursor") and not reset_cursor:
            try:
                cursor_data = await host.cursor.get(kb_id)
                if cursor_data:
                    use_delta_sync = True
            except Exception:
                pass

        upserts = 0
        deleted = 0
        delta_link = None

        async def _upsert_event(ev: Dict[str, Any]) -> None:
            """Upsert a single event to KB."""
            eid = ev.get("id")
            if not eid:
                return

            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
            subject = ev.get("subject") or "(no title)"
            body_text = ev.get("body", {}).get("content", "") if isinstance(ev.get("body"), dict) else ""
            body_preview = ev.get("bodyPreview", "")
            content = body_text or body_preview or subject
            location = (ev.get("location") or {}).get("displayName")
            attendees = [a.get("emailAddress", {}).get("address") for a in (ev.get("attendees") or []) if a.get("emailAddress")]
            organizer = (ev.get("organizer") or {}).get("emailAddress", {}).get("address")
            online_meeting_url = (ev.get("onlineMeeting") or {}).get("joinUrl")

            await host.kb.ingest_text(
                kb_id,
                title=subject,
                content=content,
                source_id=eid,
                source_url=ev.get("webLink"),
                attributes={
                    "start": start,
                    "end": end,
                    "location": location,
                    "attendees": attendees,
                    "organizer": organizer,
                    "online_meeting_url": online_meeting_url,
                    "is_cancelled": ev.get("isCancelled", False),
                    "plugin": self.name,
                },
            )

        try:
            if use_delta_sync and cursor_data:
                # Use delta endpoint for incremental sync
                delta_url = cursor_data if isinstance(cursor_data, str) else cursor_data.get("delta_link")

                if delta_url:
                    try:
                        events, delta_link = await self._fetch_all_pages(host, access_token, delta_url, max_results)

                        for ev in events:
                            if ev.get("isCancelled") or ev.get("@removed"):
                                try:
                                    await host.kb.delete_ko(external_id=ev.get("id"))
                                    deleted += 1
                                except Exception:
                                    pass
                            else:
                                await _upsert_event(ev)
                                upserts += 1

                    except Exception as e:
                        if "delta_token_expired" in str(e):
                            use_delta_sync = False
                            cursor_data = None
                        else:
                            raise

            if not use_delta_sync or not cursor_data:
                # Initial full sync with time window
                tmin, tmax = self._window(since_hours, time_min, time_max)

                query_params = {
                    "$select": "id,subject,start,end,location,body,bodyPreview,attendees,organizer,isCancelled,webLink,onlineMeeting",
                    "startDateTime": tmin,
                    "endDateTime": tmax
                }
                query_string = self._build_odata_query_string(query_params)
                endpoint = f"/me/calendarView/delta?{query_string}"

                events, delta_link = await self._fetch_all_pages(host, access_token, endpoint, max_results)

                for ev in events:
                    if ev.get("isCancelled") or ev.get("@removed"):
                        try:
                            await host.kb.delete_ko(external_id=ev.get("id"))
                            deleted += 1
                        except Exception:
                            pass
                    else:
                        await _upsert_event(ev)
                        upserts += 1

            # Store delta link for next sync
            if hasattr(host, "cursor") and delta_link:
                try:
                    await host.cursor.set(kb_id, delta_link)
                except Exception:
                    pass

            return _Result.ok({"count": upserts, "deleted": deleted, "next_sync_token": delta_link})

        except Exception as e:
            code, message, details = self._parse_exception_details(e)
            if details:
                return _Result.err(message, code=code, details=details)
            return _Result.err(message, code=code)

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        """Execute plugin operation (list or ingest)."""
        op = (params.get("op") or "ingest").lower()

        if op not in ["list", "ingest"]:
            return _Result.err(f"Unsupported op: {op}", code="invalid_parameter")

        if op == "ingest":
            kb_id = params.get("kb_id")
            if not kb_id:
                return _Result.err(
                    "kb_id is required for op=ingest (target Knowledge Base to write KOs)",
                    code="missing_parameter"
                )

        # Validate parameter ranges
        since_hours = params.get("since_hours", 48)
        if not isinstance(since_hours, int) or since_hours < 1 or since_hours > 336:
            return _Result.err("since_hours must be between 1 and 336", code="invalid_parameter")

        max_results = params.get("max_results", 50)
        if not isinstance(max_results, int) or max_results < 1 or max_results > 500:
            return _Result.err("max_results must be between 1 and 500", code="invalid_parameter")

        # Resolve Microsoft OAuth token
        try:
            auth_result = await host.auth.resolve_token_and_target("microsoft")
        except Exception as e:
            return _Result.err(
                f"Failed to resolve Microsoft OAuth token: {str(e)}",
                code="auth_missing_or_insufficient_scopes",
                details={"exception_type": type(e).__name__}
            )

        if auth_result is None:
            return _Result.err(
                "No Microsoft access token available. Connect OAuth or configure host.auth.",
                code="auth_missing_or_insufficient_scopes"
            )

        # Extract access token
        if isinstance(auth_result, tuple):
            access_token = auth_result[0] if auth_result else None
        elif isinstance(auth_result, dict):
            access_token = auth_result.get("access_token")
        else:
            access_token = None

        if not access_token:
            return _Result.err(
                "No Microsoft access token available. Connect OAuth or configure host.auth.",
                code="auth_missing_or_insufficient_scopes"
            )

        try:
            if op == "list":
                return await self._execute_list(params, context, host, access_token)
            elif op == "ingest":
                return await self._execute_ingest(params, context, host, access_token)
        except Exception as e:
            return _Result.err(
                f"Unexpected error during {op} operation: {str(e)}",
                code="execution_error",
                details={"exception_type": type(e).__name__}
            )

