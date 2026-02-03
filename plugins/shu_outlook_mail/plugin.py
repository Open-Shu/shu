from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List
from urllib.parse import quote


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


class OutlookMailPlugin:
    """Microsoft Outlook Mail plugin for listing, digesting, and ingesting emails."""

    name = "outlook_mail"
    version = "1"

    def _build_odata_query_string(self, params: Dict[str, str]) -> str:
        """
        Build OData query string with proper URL encoding for Microsoft Graph API.
        
        OData parameters like $select, $filter, $orderby should keep the $ prefix
        unencoded. Values are URL-encoded but certain characters are kept safe
        for OData compatibility.
        
        Args:
            params: Dictionary of OData parameters (e.g., {"$select": "id,subject"})
            
        Returns:
            URL-encoded query string (e.g., "$select=id,subject&$filter=...")
        """
        parts = []
        for key, value in params.items():
            # URL-encode the value but keep certain characters safe for OData
            # - Commas are used in $select lists
            # - Spaces need to be encoded as %20
            # - Colons in datetime values need to be encoded
            # - Parentheses, slashes, and single quotes are used in filter expressions
            # Microsoft Graph API expects these characters to be safe (not encoded)
            encoded_value = quote(str(value), safe=",-/:.'()T")
            parts.append(f"{key}={encoded_value}")
        return "&".join(parts)

    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin parameters."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": ["string", "null"],
                    "enum": ["list", "digest", "ingest"],
                    "default": "ingest",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "list": "List Messages",
                            "digest": "Create Digest",
                            "ingest": "Ingest to Knowledge Base"
                        },
                        "enum_help": {
                            "list": "Fetch and return recent messages without storing",
                            "digest": "Create a summary digest of inbox activity",
                            "ingest": "Ingest individual emails into knowledge base"
                        }
                    }
                },
                "since_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3360,
                    "default": 48,
                    "x-ui": {
                        "help": "Look-back window in hours for messages (1 hour to 140 days)"
                    }
                },
                "query_filter": {
                    "type": ["string", "null"],
                    "x-ui": {
                        "help": "OData filter expression (e.g., \"from/emailAddress/address eq 'user@example.com'\")"
                    }
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                    "x-ui": {
                        "help": "Maximum number of messages to return"
                    }
                },
                "kb_id": {
                    "type": ["string", "null"],
                    "x-ui": {
                        "hidden": True
                    }
                },
                "reset_cursor": {
                    "type": "boolean",
                    "default": False,
                    "x-ui": {
                        "help": "Reset sync cursor and perform full re-ingestion"
                    }
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
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "subject": {"type": ["string", "null"]},
                            "from": {
                                "type": "object",
                                "properties": {
                                    "emailAddress": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": ["string", "null"]},
                                            "address": {"type": "string"}
                                        }
                                    }
                                }
                            },
                            "to": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "emailAddress": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": ["string", "null"]},
                                                "address": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            },
                            "receivedDateTime": {"type": "string"},
                            "bodyPreview": {"type": ["string", "null"]}
                        }
                    }
                },
                "count": {"type": ["integer", "null"]},
                "deleted": {"type": ["integer", "null"]},
                "note": {"type": ["string", "null"]},
                "ko": {"type": ["object", "null"]},
                "window": {
                    "type": ["object", "null"],
                    "properties": {
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                        "hours": {"type": "integer"}
                    }
                },
                "history_id": {"type": ["string", "null"]},
                "diagnostics": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "skips": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string"},
                            "reason": {"type": "string"},
                            "code": {"type": "string"}
                        }
                    }
                }
            },
            "required": [],
            "additionalProperties": True,
        }

    async def execute(self, params: Dict[str, Any], context: Any, host: Any) -> _Result:
        """Execute plugin operation (list, digest, or ingest)."""
        # Extract and validate operation
        op = (params.get("op") or "ingest").lower()
        
        if op not in ["list", "digest", "ingest"]:
            return _Result.err(f"Unsupported op: {op}", code="invalid_parameter")
        
        # Validate required parameters for specific operations
        if op == "ingest":
            kb_id = params.get("kb_id")
            if not kb_id:
                return _Result.err(
                    "kb_id is required for op=ingest (target Knowledge Base to write KOs)",
                    code="missing_parameter"
                )
        
        # Validate parameter ranges
        since_hours = params.get("since_hours", 48)
        if not isinstance(since_hours, int) or since_hours < 1 or since_hours > 3360:
            return _Result.err(
                "since_hours must be between 1 and 3360",
                code="invalid_parameter"
            )
        
        max_results = params.get("max_results", 50)
        if not isinstance(max_results, int) or max_results < 1 or max_results > 500:
            return _Result.err(
                "max_results must be between 1 and 500",
                code="invalid_parameter"
            )
        
        # Resolve Microsoft OAuth token before API requests
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

        # resolve_token_and_target returns Tuple[Optional[str], Optional[str]]
        # First element is the access token string, second is the target
        access_token = auth_result[0] if isinstance(auth_result, tuple) else None

        if not access_token:
            return _Result.err(
                "No Microsoft access token available. Connect OAuth or configure host.auth.",
                code="auth_missing_or_insufficient_scopes"
            )
        
        # Route to appropriate operation handler
        # HttpRequestFailed exceptions bubble up to the executor which converts them
        # to structured PluginResult.err() with semantic error_category.
        if op == "list":
            return await self._execute_list(params, context, host, access_token)
        elif op == "digest":
            return await self._execute_digest(params, context, host, access_token)
        elif op == "ingest":
            return await self._execute_ingest(params, context, host, access_token)

        return _Result.err(f"Unsupported operation: {op}", code="unsupported_operation")
    
    async def _graph_api_request(
        self,
        host: Any,
        access_token: str,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make a request to Microsoft Graph API.

        HttpRequestFailed exceptions bubble up to the caller. The caller can check
        error_category for semantic handling (e.g., 'gone' for expired delta tokens,
        'auth_error' for 401, 'rate_limited' for 429).
        """
        base_url = "https://graph.microsoft.com/v1.0"
        url = f"{base_url}{endpoint}"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        response = await host.http.fetch(
            method=method,
            url=url,
            headers=headers,
            params=params or {},
            json=body
        )
        return response
    
    async def _fetch_all_pages(
        self,
        host: Any,
        access_token: str,
        initial_url: str,
        max_results: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch all pages from a paginated Graph API response.
        
        Args:
            host: Host capabilities object
            access_token: OAuth access token
            initial_url: Initial URL to fetch (can be full URL or endpoint path)
            max_results: Optional maximum number of items to return
            
        Returns:
            List of all items collected across pages
        """
        all_items = []
        next_url = initial_url
        
        # If initial_url is just an endpoint path, prepend base URL
        if not next_url.startswith("http"):
            next_url = f"https://graph.microsoft.com/v1.0{next_url}"
        
        while next_url:
            # For subsequent pages, use the full URL from @odata.nextLink
            if next_url.startswith("http"):
                # Extract endpoint from full URL
                base_url = "https://graph.microsoft.com/v1.0"
                if next_url.startswith(base_url):
                    endpoint = next_url[len(base_url):]
                else:
                    # Handle delta links or other full URLs
                    endpoint = next_url
                
                # Make request directly with full URL
                # HttpRequestFailed exceptions bubble up - caller can check error_category
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }

                response = await host.http.fetch(
                    method="GET",
                    url=next_url,
                    headers=headers
                )
            else:
                # First request with endpoint path
                response = await self._graph_api_request(
                    host=host,
                    access_token=access_token,
                    endpoint=next_url
                )
            
            # Extract items from response body
            # host.http.fetch returns {"status_code": ..., "headers": ..., "body": ...}
            # The actual Graph API response is in the "body" field
            body = response.get("body", {})
            if isinstance(body, dict):
                items = body.get("value", [])
            else:
                items = []
            all_items.extend(items)
            
            # Check if we've reached max_results
            if max_results and len(all_items) >= max_results:
                all_items = all_items[:max_results]
                break
            
            # Get next page URL from response body
            if isinstance(body, dict):
                next_url = body.get("@odata.nextLink")
            else:
                next_url = None
        
        return all_items
    
    async def _execute_list(self, params: Dict[str, Any], context: Any, host: Any, access_token: str) -> _Result:
        """
        Execute list operation to fetch recent messages.
        
        Fetches messages from /me/mailFolders/inbox/messages with:
        - Time filtering based on since_hours parameter
        - Optional OData query_filter
        - Result limit via max_results
        - Metadata fields: Subject, From, To, Cc, ReceivedDateTime, BodyPreview
        
        Args:
            params: Operation parameters
            context: Execution context
            host: Host capabilities
            access_token: Microsoft OAuth access token
            
        Returns:
            _Result with messages array and metadata
        """
        # Extract parameters
        since_hours = params.get("since_hours", 48)
        query_filter = params.get("query_filter")
        max_results = params.get("max_results", 50)
        debug_mode = params.get("debug", False)
        
        # Initialize diagnostics array
        diagnostics = []
        
        # Calculate time window for filtering
        now = datetime.now(timezone.utc)
        since_time = now - timedelta(hours=since_hours)
        
        if debug_mode:
            diagnostics.append(f"Time window: {since_time.isoformat()} to {now.isoformat()}")
        
        # Build OData query parameters
        odata_params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc",
            "$top": str(max_results)
        }
        
        # Build $filter parameter for time-based filtering
        filter_parts = []
        
        # Add time filter
        since_iso = since_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_parts.append(f"receivedDateTime ge {since_iso}")
        
        # Add custom query filter if provided
        if query_filter:
            filter_parts.append(f"({query_filter})")
            if debug_mode:
                diagnostics.append(f"Applied custom filter: {query_filter}")
        
        # Combine filters with AND
        if filter_parts:
            odata_params["$filter"] = " and ".join(filter_parts)
        
        # Fetch messages from Graph API
        # HttpRequestFailed exceptions bubble up to the executor.
        endpoint = "/me/mailFolders/inbox/messages"

        if debug_mode:
            diagnostics.append(f"Fetching from endpoint: {endpoint}")

        # Build full URL with properly URL-encoded query parameters
        query_string = self._build_odata_query_string(odata_params)
        full_endpoint = f"{endpoint}?{query_string}"

        # Fetch all pages up to max_results
        messages = await self._fetch_all_pages(
            host=host,
            access_token=access_token,
            initial_url=full_endpoint,
            max_results=max_results
        )

        if debug_mode:
            diagnostics.append(f"Retrieved {len(messages)} messages")

        # Build result data
        result_data = {
            "messages": messages,
            "count": len(messages),
            "note": f"Retrieved {len(messages)} messages from the last {since_hours} hours"
        }

        # Include diagnostics if debug mode enabled
        if debug_mode and diagnostics:
            result_data["diagnostics"] = diagnostics

        # Return successful result
        return _Result.ok(result_data)
    
    async def _execute_digest(self, params: Dict[str, Any], context: Any, host: Any, access_token: str) -> _Result:
        """
        Execute digest operation to create inbox summary.
        
        Creates a digest summary of inbox activity by:
        - Fetching messages using list operation logic
        - Analyzing top senders with message counts
        - Extracting recent message subjects
        - Creating a Knowledge Object with type "email_digest"
        - Writing the digest to the knowledge base
        
        Args:
            params: Operation parameters
            context: Execution context
            host: Host capabilities
            access_token: Microsoft OAuth access token
            
        Returns:
            _Result with ko object, count, and window metadata
        """
        # Extract parameters
        since_hours = params.get("since_hours", 48)
        query_filter = params.get("query_filter")
        max_results = params.get("max_results", 50)
        kb_id = params.get("kb_id")
        debug_mode = params.get("debug", False)
        
        # Initialize diagnostics array
        diagnostics = []
        
        # Calculate time window for filtering
        now = datetime.now(timezone.utc)
        since_time = now - timedelta(hours=since_hours)
        
        if debug_mode:
            diagnostics.append(f"Time window: {since_time.isoformat()} to {now.isoformat()}")
        
        # Build OData query parameters (same as list operation)
        odata_params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc",
            "$top": str(max_results)
        }
        
        # Build $filter parameter for time-based filtering
        filter_parts = []
        
        # Add time filter
        since_iso = since_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        filter_parts.append(f"receivedDateTime ge {since_iso}")
        
        # Add custom query filter if provided
        if query_filter:
            filter_parts.append(f"({query_filter})")
            if debug_mode:
                diagnostics.append(f"Applied custom filter: {query_filter}")
        
        # Combine filters with AND
        if filter_parts:
            odata_params["$filter"] = " and ".join(filter_parts)
        
        # Fetch messages from Graph API (reuse list operation logic)
        # HttpRequestFailed exceptions bubble up to the executor.
        endpoint = "/me/mailFolders/inbox/messages"

        if debug_mode:
            diagnostics.append(f"Fetching from endpoint: {endpoint}")

        # Build full URL with properly URL-encoded query parameters
        query_string = self._build_odata_query_string(odata_params)
        full_endpoint = f"{endpoint}?{query_string}"

        # Fetch all pages up to max_results
        messages = await self._fetch_all_pages(
            host=host,
            access_token=access_token,
            initial_url=full_endpoint,
            max_results=max_results
        )

        if debug_mode:
            diagnostics.append(f"Retrieved {len(messages)} messages for digest analysis")

        # Analyze messages to identify top senders with counts
        sender_counts = {}
        recent_subjects = []

        for message in messages:
            # Extract sender email address
            from_field = message.get("from", {})
            email_address = from_field.get("emailAddress", {})
            sender_email = email_address.get("address", "")
            sender_name = email_address.get("name", "")

            if sender_email:
                # Count messages per sender
                if sender_email not in sender_counts:
                    sender_counts[sender_email] = {
                        "email": sender_email,
                        "name": sender_name,
                        "count": 0
                    }
                sender_counts[sender_email]["count"] += 1

            # Extract subject for recent subjects list (up to 20)
            subject = message.get("subject")
            if subject and len(recent_subjects) < 20:
                recent_subjects.append(subject)

        # Sort senders by count descending and limit to top 10
        top_senders = sorted(
            sender_counts.values(),
            key=lambda x: x["count"],
            reverse=True
        )[:10]

        if debug_mode:
            diagnostics.append(f"Analyzed {len(sender_counts)} unique senders")

        # Calculate window metadata
        window = {
            "since": since_time.isoformat(),
            "until": now.isoformat(),
            "hours": since_hours
        }

        # Create digest content summary
        total_count = len(messages)
        content_lines = [
            f"Summary of {total_count} messages from {len(sender_counts)} senders",
            f"Time window: {since_hours} hours (from {since_time.strftime('%Y-%m-%d %H:%M:%S')} to {now.strftime('%Y-%m-%d %H:%M:%S')} UTC)",
            "",
            "Top Senders:"
        ]

        for sender in top_senders:
            content_lines.append(f"  - {sender['name']} <{sender['email']}>: {sender['count']} messages")

        if recent_subjects:
            content_lines.append("")
            content_lines.append("Recent Subjects:")
            for subject in recent_subjects[:10]:  # Show first 10 in content
                content_lines.append(f"  - {subject}")

        content = "\n".join(content_lines)

        # Create Knowledge Object with type "email_digest"
        ko = {
            "type": "email_digest",
            "title": f"Outlook Inbox Digest ({now.strftime('%b %d, %Y')})",
            "content": content,
            "attributes": {
                "total_count": total_count,
                "top_senders": top_senders,
                "recent_subjects": recent_subjects,
                "window": window
            },
            "source_id": f"outlook_mail_digest_{kb_id}_{now.strftime('%Y%m%d%H%M%S')}" if kb_id else f"outlook_mail_digest_{now.strftime('%Y%m%d%H%M%S')}",
            "external_id": f"outlook_mail_digest_{kb_id}_{now.strftime('%Y%m%d%H%M%S')}" if kb_id else f"outlook_mail_digest_{now.strftime('%Y%m%d%H%M%S')}"
        }

        # Write digest KO to kb_id using host.kb if kb_id is provided
        if kb_id:
            await host.kb.upsert_knowledge_object(knowledge_base_id=kb_id, ko=ko)
            if debug_mode:
                diagnostics.append(f"Wrote digest KO to knowledge base: {kb_id}")

        # Build result data
        result_data = {
            "ko": ko,
            "count": total_count,
            "window": window
        }

        # Include diagnostics if debug mode enabled
        if debug_mode and diagnostics:
            result_data["diagnostics"] = diagnostics

        # Return ko object, count, and window in result
        return _Result.ok(result_data)
    
    async def _execute_ingest(self, params: Dict[str, Any], context: Any, host: Any, access_token: str) -> _Result:
        """
        Execute ingest operation to add emails to knowledge base.
        
        Ingests individual emails by:
        - Validating kb_id parameter is present
        - Using delta sync when cursor exists (incremental updates)
        - Fetching messages using list operation logic for initial sync
        - For each message, fetching full content including body field
        - Extracting email fields: subject, sender, recipients (to/cc/bcc), date, message_id, body_text
        - Calling host.kb.ingest_email() with extracted fields
        - Handling message deletions via host.kb.delete_ko()
        - Tracking ingestion count and deletion count
        
        Args:
            params: Operation parameters including kb_id
            context: Execution context
            host: Host capabilities
            access_token: Microsoft OAuth access token
            
        Returns:
            _Result with count of ingested messages and deleted messages
        """
        # Validate kb_id parameter is present
        kb_id = params.get("kb_id")
        if not kb_id:
            return _Result.err(
                "kb_id is required for op=ingest (target Knowledge Base to write KOs)",
                code="missing_parameter"
            )
        
        # Check that kb capability is available
        if not hasattr(host, "kb"):
            return _Result.err(
                "kb capability not available. Add 'kb' to manifest capabilities.",
                code="missing_capability"
            )
        
        # Extract parameters
        since_hours = params.get("since_hours", 48)
        query_filter = params.get("query_filter")
        max_results = params.get("max_results", 50)
        reset_cursor = params.get("reset_cursor", False)
        debug_mode = params.get("debug", False)
        
        # Initialize diagnostics array
        diagnostics = []
        
        # Retrieve cursor via host.cursor.get(kb_id) before processing
        cursor_data = None
        use_delta_sync = False
        
        if hasattr(host, "cursor") and not reset_cursor:
            try:
                cursor_data = await host.cursor.get(kb_id)
                if cursor_data:
                    use_delta_sync = True
                    if debug_mode:
                        diagnostics.append("Using delta sync with existing cursor")
            except Exception as e:
                # If cursor retrieval fails, fall back to full sync
                if debug_mode:
                    diagnostics.append(f"Cursor retrieval failed, falling back to full sync: {str(e)}")
        
        if reset_cursor and debug_mode:
            diagnostics.append("Reset cursor requested, performing full sync")
        
        # Calculate time window for filtering (used for full sync)
        now = datetime.now(timezone.utc)
        since_time = now - timedelta(hours=since_hours)
        
        messages = []
        delta_link = None

        # Track ingestion count, deletion count, and skips
        ingestion_count = 0
        deleted_count = 0
        skips = []

        if use_delta_sync and cursor_data:
            # Use delta endpoint for incremental sync
            # The cursor_data should contain the delta link URL
            delta_url = cursor_data if isinstance(cursor_data, str) else cursor_data.get("delta_link")

            if delta_url:
                try:
                    # Fetch delta changes
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    }

                    response = await host.http.fetch(
                        method="GET",
                        url=delta_url,
                        headers=headers
                    )

                    # Success - extract messages and delta link from response body
                    body = response.get("body", {})
                    if isinstance(body, dict):
                        messages = body.get("value", [])
                        delta_link = body.get("@odata.deltaLink")
                    else:
                        messages = []
                        delta_link = None

                except Exception as e:
                    # Check for HTTP 410 (delta token expired) using error_category
                    if hasattr(e, 'error_category') and e.error_category == 'gone':
                        if debug_mode:
                            diagnostics.append("Delta token expired (410), falling back to full sync")
                        use_delta_sync = False
                        # Reset cursor (best-effort)
                        if hasattr(host, "cursor"):
                            await host.cursor.delete_safe(kb_id)
                    else:
                        # Other HTTP errors bubble up to the executor
                        raise
            else:
                # No valid delta URL - fall back to full sync
                use_delta_sync = False
        
        # If not using delta sync, perform full list-based sync
        # This runs when: no cursor exists, cursor retrieval failed, or delta token expired
        if not use_delta_sync:
            # Build OData query parameters (same as list operation)
            odata_params = {
                "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,bodyPreview",
                "$orderby": "receivedDateTime desc",
                "$top": str(max_results)
            }
            
            # Build $filter parameter for time-based filtering
            filter_parts = []
            
            # Add time filter
            since_iso = since_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            filter_parts.append(f"receivedDateTime ge {since_iso}")
            
            # Add custom query filter if provided
            if query_filter:
                filter_parts.append(f"({query_filter})")
            
            # Combine filters with AND
            if filter_parts:
                odata_params["$filter"] = " and ".join(filter_parts)
            
            # Fetch messages from Graph API (reuse list operation logic)
            endpoint = "/me/mailFolders/inbox/messages"
            
            # Build full URL with properly URL-encoded query parameters
            query_string = self._build_odata_query_string(odata_params)
            full_endpoint = f"{endpoint}?{query_string}"
            
            # Fetch all pages up to max_results
            messages = await self._fetch_all_pages(
                host=host,
                access_token=access_token,
                initial_url=full_endpoint,
                max_results=max_results
            )
            
            if debug_mode:
                diagnostics.append(f"Full sync fetched {len(messages)} messages")
            
            # For initial sync, we need to get the delta link for future syncs
            # Make a delta query to get the initial delta token
            try:
                delta_endpoint = "/me/mailFolders/inbox/messages/delta"
                delta_params = {
                    "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,bodyPreview"
                }
                
                # Add the same filters to delta query
                if filter_parts:
                    delta_params["$filter"] = " and ".join(filter_parts)
                
                # Build delta URL with properly URL-encoded query parameters
                delta_query_string = self._build_odata_query_string(delta_params)
                delta_full_endpoint = f"{delta_endpoint}?{delta_query_string}"
                
                # Fetch delta to get initial token
                delta_response = await self._graph_api_request(
                    host=host,
                    access_token=access_token,
                    endpoint=delta_full_endpoint
                )
                
                # Extract delta link for next sync from response body
                delta_body = delta_response.get("body", {})
                if isinstance(delta_body, dict):
                    delta_link = delta_body.get("@odata.deltaLink")
                else:
                    delta_link = None
                
            except Exception:
                # If we can't get delta link, that's okay - we'll do full sync next time
                pass

        # Process messages: handle messageAdded and messageDeleted events
        # This runs for both delta sync and full sync
        for message in messages:
            # Check if this is a messageDeleted event (has @removed field)
            if "@removed" in message:
                # For messageDeleted: call host.kb.delete_ko(external_id=message_id)
                message_id = message.get("id")
                if message_id:
                    try:
                        await host.kb.delete_ko(external_id=message_id)
                        deleted_count += 1
                    except Exception as e:
                        # Track failed deletion in skips array
                        error_str = str(e)
                        skips.append({
                            "item_id": message_id,
                            "reason": f"Failed to delete message: {error_str}",
                            "code": "deletion_failed"
                        })
                continue
            
            # For messageAdded: ingest new messages
            message_id = message.get("id")
            if not message_id:
                skips.append({
                    "item_id": "unknown",
                    "reason": "Message missing id field",
                    "code": "missing_id"
                })
                continue
            
            try:
                # Fetch full message content including body field
                response = await self._graph_api_request(
                    host=host,
                    access_token=access_token,
                    endpoint=f"/me/messages/{message_id}",
                    params={
                        "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,body"
                    }
                )

                # Extract message from response body
                # _graph_api_request returns {"status_code": ..., "body": ...}
                full_message = response.get("body", {}) if isinstance(response.get("body"), dict) else response

                # Extract email fields
                subject = full_message.get("subject") or "(no subject)"
                
                # Extract sender
                from_field = full_message.get("from", {})
                sender_email_obj = from_field.get("emailAddress", {})
                sender_name = sender_email_obj.get("name", "")
                sender_address = sender_email_obj.get("address", "")
                sender = f"{sender_name} <{sender_address}>" if sender_name else sender_address
                
                # Extract recipients (to/cc/bcc)
                def extract_recipients(recipient_list: List[Dict[str, Any]]) -> List[str]:
                    """Extract email addresses from recipient list."""
                    result = []
                    for recipient in recipient_list or []:
                        email_obj = recipient.get("emailAddress", {})
                        name = email_obj.get("name", "")
                        address = email_obj.get("address", "")
                        if address:
                            result.append(f"{name} <{address}>" if name else address)
                    return result
                
                to_recipients = extract_recipients(full_message.get("toRecipients", []))
                cc_recipients = extract_recipients(full_message.get("ccRecipients", []))
                bcc_recipients = extract_recipients(full_message.get("bccRecipients", []))
                
                recipients = {
                    "to": to_recipients,
                    "cc": cc_recipients,
                    "bcc": bcc_recipients
                }
                
                # Extract date
                date = full_message.get("receivedDateTime")
                
                # Extract body text from body.content field
                body_obj = full_message.get("body", {})
                body_content = body_obj.get("content", "")
                body_type = body_obj.get("contentType", "text")
                
                # If body is HTML, we'll pass it as body_text for now
                # The kb capability will handle text extraction if needed
                body_text = body_content or full_message.get("bodyPreview", "")
                
                # Call host.kb.ingest_email() with extracted fields
                await host.kb.ingest_email(
                    kb_id,
                    subject=subject,
                    sender=sender,
                    recipients=recipients,
                    date=date,
                    message_id=message_id,
                    thread_id=None,  # Outlook doesn't have thread_id in the same way
                    body_text=body_text,
                    body_html=body_content if body_type.lower() == "html" else None,
                    labels=None,  # Outlook uses categories, not labels
                    source_url=None,
                    attributes={
                        "extraction_metadata": {
                            "body_type": body_type,
                            "received_datetime": date
                        }
                    }
                )
                
                ingestion_count += 1
                
            except Exception as e:
                # Track failed ingestion in skips array
                error_str = str(e)
                skips.append({
                    "item_id": message_id,
                    "reason": f"Failed to ingest message: {error_str}",
                    "code": "ingestion_failed"
                })
                continue
        
        # Store delta token via host.cursor.set_safe() after successful processing
        # set_safe returns bool and doesn't raise on failure
        if delta_link and hasattr(host, "cursor"):
            cursor_saved = await host.cursor.set_safe(kb_id, delta_link)
            if debug_mode:
                if cursor_saved:
                    diagnostics.append("Stored delta token for next sync")
                else:
                    diagnostics.append("Failed to store delta token (non-fatal)")

        if debug_mode:
            diagnostics.append(f"Ingestion complete: {ingestion_count} ingested, {deleted_count} deleted, {len(skips)} skipped")

        # Return count and deleted count in result
        result_data = {
            "count": ingestion_count,
            "deleted": deleted_count
        }

        # Include history_id (delta token) if available
        if delta_link:
            result_data["history_id"] = delta_link

        # Include skips array if there were any failures
        if skips:
            result_data["skips"] = skips

        # Include diagnostics if debug mode enabled
        if debug_mode and diagnostics:
            result_data["diagnostics"] = diagnostics

        return _Result.ok(result_data)
