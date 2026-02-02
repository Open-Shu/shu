"""
Teams Chat Plugin for Shu

Provides list and ingest operations for Microsoft Teams chat messages using
Microsoft Graph API. Uses timestamp-based watermark for incremental sync
(Teams doesn't support delta query for chat messages).

Supports 1:1 and group chats via Chat.Read scope.
Channel messages require ChannelMessage.Read.All (admin consent) - not included in MVP.
"""

from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote


class _Result:
    """Shim result class for plugin responses."""
    
    def __init__(self, status: str, data: Optional[Dict[str, Any]] = None,
                 error: Optional[Dict[str, Any]] = None):
        self.status = status
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: Optional[Dict[str, Any]] = None) -> "_Result":
        return cls("success", data or {})

    @classmethod
    def err(cls, message: str, code: str = "tool_error",
            details: Optional[Dict[str, Any]] = None) -> "_Result":
        return cls("error", error={"code": code, "message": message, "details": details or {}})


class TeamsChatPlugin:
    """Microsoft Teams Chat plugin for listing and ingesting chat messages."""

    name = "teams_chat"
    version = "1"

    # -------------------------------------------------------------------------
    # Schema definitions
    # -------------------------------------------------------------------------

    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Return input parameter schema for the plugin."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "ingest"],
                    "default": "list",
                    "x-ui": {"help": "Operation: list (view messages) or ingest (save to KB)"}
                },
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 336,
                    "default": 168,
                    "x-ui": {"help": "Look-back window in hours (default: 168 = 7 days)"}
                },
                "max_chats": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                    "x-ui": {"help": "Maximum number of chats to fetch messages from"}
                },
                "max_messages_per_chat": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                    "x-ui": {"help": "Maximum messages to fetch per chat"}
                },
                "kb_id": {
                    "type": ["string", "null"],
                    "x-ui": {"hidden": True, "help": "Knowledge base ID for ingest operation"}
                },
                "reset_cursor": {
                    "type": "boolean",
                    "default": False,
                    "x-ui": {"help": "Reset sync cursor and perform full sync"}
                }
            },
            "required": [],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        """Return output schema for the plugin."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "messages": {"type": "array", "description": "List of chat messages"},
                "count": {"type": ["integer", "null"], "description": "Number of messages"},
                "last_ts": {"type": ["string", "null"], "description": "Timestamp of newest message"},
                "chats_processed": {"type": ["integer", "null"], "description": "Number of chats processed"},
            },
            "required": [],
            "additionalProperties": True,
        }

    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------

    def _window(self, since_hours: int) -> str:
        """Calculate ISO timestamp for look-back window."""
        tmin = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        return tmin.isoformat().replace("+00:00", "Z")

    async def _resolve_auth(self, host: Any) -> Optional[str]:
        """Resolve Microsoft access token from host auth."""
        auth = getattr(host, "auth", None)
        if not auth:
            return None
        try:
            token, _ = await auth.resolve_token_and_target("microsoft")
            return token
        except Exception:
            return None

    async def _graph_request(self, host: Any, access_token: str, method: str, url: str,
                              params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a Microsoft Graph API request with error handling."""
        if not url.startswith("http"):
            url = f"https://graph.microsoft.com/v1.0{url}"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        try:
            response = await host.http.fetch(method=method, url=url, headers=headers, params=params or {})
            body = response.get("body", {})
            return body if isinstance(body, dict) else {}
        except Exception as e:
            # Handle HttpRequestFailed with detailed error extraction
            if hasattr(e, 'status_code') and hasattr(e, 'body'):
                status_code = getattr(e, 'status_code')
                body_obj = getattr(e, 'body')
                
                if isinstance(body_obj, dict):
                    error_obj = body_obj.get("error", {})
                    error_msg = error_obj.get("message", str(body_obj))
                    graph_code = error_obj.get("code", "api_error")
                else:
                    error_msg = str(body_obj)[:500] if body_obj else "Unknown error"
                    graph_code = "api_error"

                details = {"http_status": status_code, "graph_error_code": graph_code, "url": url}

                if status_code == 401:
                    raise Exception(f"auth_missing_or_insufficient_scopes:Authentication failed: {error_msg}|{json.dumps(details)}")
                elif status_code == 403:
                    raise Exception(f"insufficient_permissions:Insufficient permissions: {error_msg}|{json.dumps(details)}")
                elif status_code == 429:
                    raise Exception(f"rate_limit_exceeded:Rate limit exceeded: {error_msg}|{json.dumps(details)}")
                elif status_code >= 500:
                    raise Exception(f"server_error:Microsoft Graph API error (HTTP {status_code}): {error_msg}|{json.dumps(details)}")
                else:
                    raise Exception(f"api_error:Microsoft Graph API error (HTTP {status_code}): {error_msg}|{json.dumps(details)}")
            else:
                details = {"exception_type": type(e).__name__, "message": str(e)}
                raise Exception(f"network_error:Network error: {str(e)}|{json.dumps(details)}")

    async def _list_chats(self, host: Any, access_token: str, max_chats: int) -> List[Dict[str, Any]]:
        """List user's chats (1:1 and group chats)."""
        chats: List[Dict[str, Any]] = []
        next_link: Optional[str] = None
        url = "/me/chats"

        while len(chats) < max_chats:
            params = {"$top": min(50, max_chats - len(chats))}
            if next_link:
                # Use full URL for pagination
                data = await self._graph_request(host, access_token, "GET", next_link)
            else:
                data = await self._graph_request(host, access_token, "GET", url, params)

            for chat in data.get("value", []):
                chats.append({
                    "id": chat.get("id"),
                    "topic": chat.get("topic"),
                    "chatType": chat.get("chatType"),  # oneOnOne, group, meeting
                    "lastUpdatedDateTime": chat.get("lastUpdatedDateTime"),
                })

            next_link = data.get("@odata.nextLink")
            if not next_link or len(chats) >= max_chats:
                break

        return chats[:max_chats]

    async def _list_chat_messages(self, host: Any, access_token: str, chat_id: str,
                                   since_ts: str, max_messages: int) -> List[Dict[str, Any]]:
        """List messages in a specific chat, filtered by timestamp.

        Note: Microsoft Graph API for chat messages only supports:
        - lastModifiedDateTime with gt and lt operators
        - createdDateTime with lt operator only (NOT gt)
        - $filter must be used with $orderby on the same property

        We use lastModifiedDateTime gt for incremental sync since createdDateTime gt is not supported.
        """
        messages: List[Dict[str, Any]] = []
        next_link: Optional[str] = None
        # Filter messages modified after since_ts (createdDateTime only supports lt, not gt)
        filter_param = f"lastModifiedDateTime gt {since_ts}"
        url = f"/me/chats/{chat_id}/messages"

        while len(messages) < max_messages:
            params = {
                "$top": min(50, max_messages - len(messages)),
                "$filter": filter_param,
                "$orderby": "lastModifiedDateTime desc"
            }

            try:
                if next_link:
                    data = await self._graph_request(host, access_token, "GET", next_link)
                else:
                    data = await self._graph_request(host, access_token, "GET", url, params)
            except Exception as e:
                # Some chats may not be accessible; skip and continue
                if "403" in str(e) or "404" in str(e):
                    break
                raise

            for msg in data.get("value", []):
                # Skip system messages
                msg_type = msg.get("messageType", "")
                if msg_type != "message":
                    continue
                messages.append(msg)

            next_link = data.get("@odata.nextLink")
            if not next_link or len(messages) >= max_messages:
                break

        return messages[:max_messages]

    async def _resolve_sender(self, host: Any, access_token: str,
                               from_obj: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve sender information with caching."""
        user_info = from_obj.get("user", {})
        user_id = user_info.get("id")
        display_name = user_info.get("displayName")

        # Default profile
        profile = {
            "id": user_id,
            "displayName": display_name,
            "email": None
        }

        if not user_id:
            return profile

        # Check cache first
        cache = getattr(host, "cache", None)
        cache_key = f"teams_user:{user_id}"

        if cache:
            try:
                cached = await cache.get(cache_key)
                if cached:
                    return cached
            except Exception:
                pass

        # Try to get user profile from Graph API
        try:
            user_data = await self._graph_request(
                host, access_token, "GET", f"/users/{user_id}",
                params={"$select": "id,displayName,mail,userPrincipalName"}
            )
            profile = {
                "id": user_data.get("id") or user_id,
                "displayName": user_data.get("displayName") or display_name,
                "email": user_data.get("mail") or user_data.get("userPrincipalName")
            }
        except Exception:
            # User lookup failed (permissions, deleted user, etc.) - use fallback
            pass

        # Cache result for 6 hours
        if cache:
            try:
                await cache.set(cache_key, profile, ttl_seconds=6 * 3600)
            except Exception:
                pass

        return profile

    # -------------------------------------------------------------------------
    # List operation
    # -------------------------------------------------------------------------

    async def _execute_list(self, host: Any, params: Dict[str, Any], access_token: str,
                             since_hours: int, max_chats: int,
                             max_messages_per_chat: int) -> _Result:
        """Execute list operation - fetch recent messages from chats."""
        since_ts = self._window(since_hours)

        # Get list of chats
        try:
            chats = await self._list_chats(host, access_token, max_chats)
        except Exception as e:
            error_str = str(e)
            if "auth_missing" in error_str or "401" in error_str:
                return _Result.err(
                    "Authentication failed. Please reconnect your Microsoft account.",
                    code="auth_error"
                )
            return _Result.err(f"Failed to list chats: {error_str}", code="api_error")

        # Fetch messages from each chat
        all_messages: List[Dict[str, Any]] = []
        chats_processed = 0

        for chat in chats:
            chat_id = chat.get("id")
            if not chat_id:
                continue

            try:
                messages = await self._list_chat_messages(
                    host, access_token, chat_id, since_ts, max_messages_per_chat
                )
            except Exception:
                # Skip chats that fail (permissions, etc.)
                continue

            chats_processed += 1

            for msg in messages:
                # Resolve sender
                from_obj = msg.get("from", {})
                sender = await self._resolve_sender(host, access_token, from_obj)

                # Extract message content
                body = msg.get("body", {})
                content = body.get("content", "")
                content_type = body.get("contentType", "text")

                # Strip HTML if needed (basic)
                if content_type == "html":
                    # Simple HTML stripping - remove tags
                    import re
                    content = re.sub(r'<[^>]+>', '', content)

                all_messages.append({
                    "id": msg.get("id"),
                    "chatId": chat_id,
                    "chatTopic": chat.get("topic"),
                    "chatType": chat.get("chatType"),
                    "createdDateTime": msg.get("createdDateTime"),
                    "content": content.strip(),
                    "sender": sender,
                })

        # Sort by timestamp descending
        all_messages.sort(key=lambda x: x.get("createdDateTime") or "", reverse=True)

        last_ts = all_messages[0].get("createdDateTime") if all_messages else None

        return _Result.ok({
            "messages": all_messages,
            "count": len(all_messages),
            "chats_processed": chats_processed,
            "last_ts": last_ts
        })

    # -------------------------------------------------------------------------
    # Ingest operation
    # -------------------------------------------------------------------------

    async def _execute_ingest(self, host: Any, params: Dict[str, Any], access_token: str,
                               kb_id: str, since_hours: int, max_chats: int,
                               max_messages_per_chat: int) -> _Result:
        """Execute ingest operation - save messages to knowledge base."""
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        # Check for cursor (timestamp watermark)
        reset_cursor = bool(params.get("reset_cursor"))
        last_ts: Optional[str] = None

        if hasattr(host, "cursor") and not reset_cursor:
            try:
                cursor_data = await host.cursor.get(kb_id)
                if isinstance(cursor_data, str):
                    last_ts = cursor_data
                elif isinstance(cursor_data, dict):
                    last_ts = cursor_data.get("last_ts")
            except Exception:
                pass

        # Use cursor timestamp or fall back to since_hours window
        since_ts = last_ts or self._window(since_hours)

        # Get list of chats
        try:
            chats = await self._list_chats(host, access_token, max_chats)
        except Exception as e:
            error_str = str(e)
            if "auth_missing" in error_str or "401" in error_str:
                return _Result.err(
                    "Authentication failed. Please reconnect your Microsoft account.",
                    code="auth_error"
                )
            return _Result.err(f"Failed to list chats: {error_str}", code="api_error")

        # Fetch and ingest messages from each chat
        upserts = 0
        chats_processed = 0
        newest_ts = last_ts

        for chat in chats:
            chat_id = chat.get("id")
            if not chat_id:
                continue

            try:
                messages = await self._list_chat_messages(
                    host, access_token, chat_id, since_ts, max_messages_per_chat
                )
            except Exception:
                continue

            chats_processed += 1

            for msg in messages:
                msg_id = msg.get("id")
                if not msg_id:
                    continue

                # Resolve sender
                from_obj = msg.get("from", {})
                sender = await self._resolve_sender(host, access_token, from_obj)

                # Extract message content
                body = msg.get("body", {})
                content = body.get("content", "")
                content_type = body.get("contentType", "text")

                # Strip HTML if needed
                if content_type == "html":
                    import re
                    content = re.sub(r'<[^>]+>', '', content)

                content = content.strip()
                if not content:
                    continue

                # Create title from content (first 80 chars)
                title = content[:80] if content else "(message)"

                # Build source_id for deduplication
                source_id = f"teams:{chat_id}:{msg_id}"

                created_dt = msg.get("createdDateTime")

                # Ingest to KB
                await host.kb.ingest_text(
                    kb_id,
                    title=title,
                    content=content,
                    source_id=source_id,
                    source_url=None,
                    attributes={
                        "chat_id": chat_id,
                        "chat_topic": chat.get("topic"),
                        "chat_type": chat.get("chatType"),
                        "message_id": msg_id,
                        "created_at": created_dt,
                        "sender_id": sender.get("id"),
                        "sender_name": sender.get("displayName"),
                        "sender_email": sender.get("email"),
                        "plugin": self.name,
                    }
                )
                upserts += 1

                # Track newest timestamp
                if created_dt and (not newest_ts or created_dt > newest_ts):
                    newest_ts = created_dt

        # Update cursor with newest timestamp
        if hasattr(host, "cursor") and newest_ts:
            try:
                await host.cursor.set(kb_id, newest_ts)
            except Exception:
                pass

        return _Result.ok({
            "count": upserts,
            "chats_processed": chats_processed,
            "last_ts": newest_ts
        })

    # -------------------------------------------------------------------------
    # Main execute method
    # -------------------------------------------------------------------------

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        """Main entry point for plugin execution."""
        params = params or {}
        op = (params.get("op") or "list").lower()

        # Validate operation
        if op not in ("list", "ingest"):
            return _Result.err(f"Unsupported operation: {op}. Use 'list' or 'ingest'.")

        # Parse parameters
        since_hours = int(params.get("since_hours", 168))
        max_chats = int(params.get("max_chats", 50))
        max_messages_per_chat = int(params.get("max_messages_per_chat", 100))
        kb_id = params.get("kb_id")

        # Validate parameters
        if since_hours < 1 or since_hours > 336:
            return _Result.err("since_hours must be between 1 and 336")
        if max_chats < 1 or max_chats > 100:
            return _Result.err("max_chats must be between 1 and 100")
        if max_messages_per_chat < 1 or max_messages_per_chat > 500:
            return _Result.err("max_messages_per_chat must be between 1 and 500")

        # Resolve auth
        access_token = await self._resolve_auth(host)
        if not access_token:
            return _Result.err(
                "No Microsoft access token available. Please connect your Microsoft account.",
                code="auth_missing"
            )

        # Execute operation
        if op == "list":
            return await self._execute_list(
                host, params, access_token, since_hours, max_chats, max_messages_per_chat
            )
        elif op == "ingest":
            if not kb_id:
                return _Result.err("kb_id is required for ingest operation")
            return await self._execute_ingest(
                host, params, access_token, kb_id, since_hours, max_chats, max_messages_per_chat
            )

        return _Result.err(f"Unsupported operation: {op}")
