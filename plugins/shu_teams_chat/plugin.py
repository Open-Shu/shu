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
import re
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
        except Exception as e:
            if hasattr(host, "log"):
                host.log.debug(f"Failed to resolve Microsoft token: {e}")

    async def _graph_request(self, host: Any, access_token: str, method: str, url: str,
                              params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make a Microsoft Graph API request.

        HttpRequestFailed exceptions bubble up to the executor which converts them
        to structured PluginResult.err() with semantic error_category.
        """
        if not url.startswith("http"):
            url = f"https://graph.microsoft.com/v1.0{url}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        response = await host.http.fetch(method=method, url=url, headers=headers, params=params or {})
        body = response.get("body", {})
        return body if isinstance(body, dict) else {}

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

        HttpRequestFailed (403/404 for inaccessible chats) is caught by map_safe at call site.
        """
        messages: List[Dict[str, Any]] = []
        next_link: Optional[str] = None
        filter_param = f"lastModifiedDateTime gt {since_ts}"
        # URL-encode chat_id to prevent injection of unsafe characters
        encoded_chat_id = quote(chat_id, safe='')
        url = f"/me/chats/{encoded_chat_id}/messages"

        while len(messages) < max_messages:
            params = {
                "$top": min(50, max_messages - len(messages)),
                "$filter": filter_param,
                "$orderby": "lastModifiedDateTime desc"
            }

            if next_link:
                data = await self._graph_request(host, access_token, "GET", next_link)
            else:
                data = await self._graph_request(host, access_token, "GET", url, params)

            for msg in data.get("value", []):
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
        """Resolve sender information with caching.

        Uses safe methods (set_safe, fetch_or_none) to avoid try/except
        boilerplate for expected failures (missing permissions, etc).
        """
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

        # Check cache first (cache.get already returns None on error)
        cache = getattr(host, "cache", None)
        cache_key = f"teams_user:{user_id}"

        if cache:
            cached = await cache.get(cache_key)
            if cached:
                return cached

        # Try to get user profile from Graph API (404/403 returns None)
        # URL-encode user_id to prevent injection of unsafe characters
        encoded_user_id = quote(user_id, safe='')
        url = f"https://graph.microsoft.com/v1.0/users/{encoded_user_id}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        user_data = await host.http.fetch_or_none(
            "GET", url, headers=headers, params={"$select": "id,displayName,mail,userPrincipalName"}
        )
        if user_data:
            body = user_data.get("body", {})
            if isinstance(body, dict):
                profile = {
                    "id": body.get("id") or user_id,
                    "displayName": body.get("displayName") or display_name,
                    "email": body.get("mail") or body.get("userPrincipalName")
                }

        # Cache result for 6 hours (set_safe returns bool, doesn't raise)
        if cache:
            await cache.set_safe(cache_key, profile, ttl_seconds=6 * 3600)

        return profile

    # -------------------------------------------------------------------------
    # List operation
    # -------------------------------------------------------------------------

    async def _execute_list(self, host: Any, params: Dict[str, Any], access_token: str,
                             since_hours: int, max_chats: int,
                             max_messages_per_chat: int) -> _Result:
        """Execute list operation - fetch recent messages from chats.

        HttpRequestFailed from _list_chats bubbles up; executor converts to
        structured PluginResult.err() with semantic error_category (auth_error, etc).
        Individual chat message failures are handled gracefully with map_safe.
        """
        since_ts = self._window(since_hours)

        # Get list of chats (HttpRequestFailed bubbles to executor)
        chats = await self._list_chats(host, access_token, max_chats)

        # Fetch messages from each chat using map_safe for graceful failure handling
        # Returns (chat, messages) tuples to maintain alignment
        async def fetch_chat_messages(chat: Dict[str, Any]) -> tuple:
            chat_id = chat.get("id")
            if not chat_id:
                return (chat, [])
            messages = await self._list_chat_messages(host, access_token, chat_id, since_ts, max_messages_per_chat)
            return (chat, messages)

        chat_results, errors = await host.utils.map_safe(chats, fetch_chat_messages)
        if errors:
            host.log.warning(f"Failed to fetch messages from {len(errors)} chats")

        # Process results - each result is a (chat, messages) tuple
        all_messages: List[Dict[str, Any]] = []
        chats_processed = len(chat_results)
        for chat, messages in chat_results:
            chat_id = chat.get("id")

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
        """Execute ingest operation - save messages to knowledge base.

        HttpRequestFailed from _list_chats bubbles up; executor converts to
        structured PluginResult.err() with semantic error_category.
        Uses safe methods for cursor operations and map_safe for batch processing.
        """
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        # Check for cursor (get() returns None on any error and logs)
        reset_cursor = bool(params.get("reset_cursor"))
        last_ts: Optional[str] = None

        if hasattr(host, "cursor") and not reset_cursor:
            cursor_data = await host.cursor.get(kb_id)
            if isinstance(cursor_data, str):
                last_ts = cursor_data
            elif isinstance(cursor_data, dict):
                last_ts = cursor_data.get("last_ts")

        # Use cursor timestamp or fall back to since_hours window
        since_ts = last_ts or self._window(since_hours)

        # Get list of chats (HttpRequestFailed bubbles to executor)
        chats = await self._list_chats(host, access_token, max_chats)

        # Fetch messages from each chat using map_safe
        async def fetch_chat_messages(chat: Dict[str, Any]) -> tuple:
            chat_id = chat.get("id")
            if not chat_id:
                return (chat, [])
            messages = await self._list_chat_messages(host, access_token, chat_id, since_ts, max_messages_per_chat)
            return (chat, messages)

        chat_results, errors = await host.utils.map_safe(chats, fetch_chat_messages)
        if errors:
            host.log.warning(f"Failed to fetch messages from {len(errors)} chats")

        # Ingest messages from all successful chats
        upserts = 0
        chats_processed = len(chat_results)
        newest_ts = last_ts

        for chat, messages in chat_results:
            chat_id = chat.get("id")

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

        # Update cursor with newest timestamp (set_safe logs on error but doesn't raise)
        if hasattr(host, "cursor") and newest_ts:
            await host.cursor.set_safe(kb_id, newest_ts)

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
        else:  # op == "ingest"
            if not kb_id:
                return _Result.err("kb_id is required for ingest operation")
            return await self._execute_ingest(
                host, params, access_token, kb_id, since_hours, max_chats, max_messages_per_chat
            )
