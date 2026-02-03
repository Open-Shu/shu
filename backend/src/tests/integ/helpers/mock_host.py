"""
Mock Host Capabilities for Plugin Integration Tests

This module provides mock implementations of host capabilities for testing plugins
without requiring the full Shu backend infrastructure. It serves as the foundation
for a plugin development kit that allows plugin authors to test their plugins in
isolation.

Usage:
    from integ.helpers.mock_host import MockHost, create_mock_graph_response

    mock_host = MockHost()
    mock_host.http.set_response("/me/messages", create_mock_graph_response(messages))
    result = await plugin.execute(params, None, mock_host)
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar

from shu.plugins.host.exceptions import HttpRequestFailed

T = TypeVar("T")
R = TypeVar("R")


# ============================================================================
# Graph API Response Helpers
# ============================================================================

def create_mock_graph_response(
    items: List[Dict[str, Any]],
    next_link: Optional[str] = None,
    delta_link: Optional[str] = None,
    status_code: int = 200
) -> Dict[str, Any]:
    """Create a mock Graph API response.

    Args:
        items: List of items to include in the "value" field
        next_link: Optional @odata.nextLink for pagination
        delta_link: Optional @odata.deltaLink for delta sync
        status_code: HTTP status code (default 200)

    Returns:
        Dict matching Graph API response structure
    """
    response = {
        "status_code": status_code,
        "body": {"value": items}
    }

    if next_link:
        response["body"]["@odata.nextLink"] = next_link

    if delta_link:
        response["body"]["@odata.deltaLink"] = delta_link

    return response


# ============================================================================
# Mock Host Capabilities
# ============================================================================

class MockHostAuth:
    """Mock host.auth capability."""

    def __init__(self, access_token: str = "mock_access_token", should_fail: bool = False):
        self.access_token = access_token
        self.should_fail = should_fail

    async def resolve_token_and_target(self, provider: str, *, scopes: Optional[List[str]] = None):
        """Mock token resolution.

        Returns:
            Tuple of (access_token_string, target) or (None, None) if should_fail.
            Matches the real AuthCapability signature: Tuple[Optional[str], Optional[str]]
        """
        if self.should_fail:
            return None, None
        if provider in ("microsoft", "google"):
            return self.access_token, "me"
        return None, None


class MockHostHttp:
    """Mock host.http capability with fetch, fetch_or_none, and bytes variants."""

    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self.responses: Dict[str, Dict[str, Any]] = {}
        self.default_response: Optional[Dict[str, Any]] = None

    def set_response(self, url_pattern: str, response: Dict[str, Any]):
        """Set a mock response for a URL pattern."""
        self.responses[url_pattern] = response

    def set_default_response(self, response: Dict[str, Any]):
        """Set a default response for all unmatched requests."""
        self.default_response = response

    def _find_response(self, url: str) -> Dict[str, Any]:
        """Find matching response, preferring longer (more specific) patterns."""
        # Sort patterns by length (longest first) for more specific matching
        sorted_patterns = sorted(self.responses.keys(), key=len, reverse=True)
        for pattern in sorted_patterns:
            if pattern in url:
                return self.responses[pattern]

        if self.default_response:
            return self.default_response

        return create_mock_graph_response([])

    async def fetch(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Mock HTTP fetch.

        Raises HttpRequestFailed for 4xx/5xx status codes, matching real
        HttpCapability behavior.
        """
        self.requests.append({
            "method": method,
            "url": url,
            "headers": headers,
            "params": params,
            "json": json
        })
        response = self._find_response(url)
        status_code = response.get("status_code", 200)
        if status_code >= 400:
            # Extract body for the exception
            body = response.get("body") or response.get("error") or response
            raise HttpRequestFailed(
                status_code=status_code,
                url=url,
                body=body,
                headers=response.get("headers")
            )
        return response

    async def fetch_or_none(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Mock HTTP fetch that returns None on 4xx (optional lookups).

        This mirrors HttpCapability.fetch_or_none behavior:
        - Returns response dict on success (2xx/3xx)
        - Returns None on 4xx client errors
        - Raises on 5xx server errors (not implemented in mock for simplicity)
        """
        response = await self.fetch(method, url, headers=headers, params=params, json=json, **kwargs)
        status = response.get("status_code", 200)
        if 400 <= status < 500:
            return None
        return response

    async def fetch_bytes(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Mock HTTP fetch for binary content."""
        return await self.fetch(method, url, **kwargs)

    async def fetch_bytes_or_none(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Mock HTTP fetch_bytes that returns None on 4xx."""
        return await self.fetch_or_none(method, url, **kwargs)


class MockHostKb:
    """Mock host.kb capability for knowledge base operations."""

    def __init__(self):
        self.ingested_texts: List[Dict[str, Any]] = []
        self.ingested_emails: List[Dict[str, Any]] = []
        self.ingested_threads: List[Dict[str, Any]] = []
        self.ingested_documents: List[Dict[str, Any]] = []
        self.upserted_kos: List[Dict[str, Any]] = []
        self.deleted_kos: List[str] = []
        self.written_kos: List[Dict[str, Any]] = []  # For write_ko() calls

    async def ingest_text(
        self,
        kb_id: str,
        *,
        title: str,
        content: str,
        source_id: str,
        source_url: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Mock text ingestion."""
        record = {
            "kb_id": kb_id,
            "title": title,
            "content": content,
            "source_id": source_id,
            "source_url": source_url,
            "attributes": attributes
        }
        self.ingested_texts.append(record)
        return {"ko_id": f"mock_ko_{source_id}"}

    async def ingest_email(
        self,
        kb_id: str,
        *,
        subject: str,
        sender: Optional[str],
        recipients: Dict[str, Any],
        date: Optional[str],
        message_id: str,
        thread_id: Optional[str] = None,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
        labels: Optional[List[str]] = None,
        source_url: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Mock email ingestion."""
        record = {
            "kb_id": kb_id,
            "subject": subject,
            "sender": sender,
            "recipients": recipients,
            "date": date,
            "message_id": message_id,
            "thread_id": thread_id,
            "body_text": body_text,
            "body_html": body_html,
            "labels": labels,
            "source_url": source_url,
            "attributes": attributes
        }
        self.ingested_emails.append(record)
        return {"ko_id": f"mock_ko_{message_id}"}

    async def ingest_thread(
        self,
        kb_id: str,
        *,
        title: str,
        content: str,
        thread_id: str,
        source_url: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Mock thread ingestion."""
        record = {
            "kb_id": kb_id,
            "title": title,
            "content": content,
            "thread_id": thread_id,
            "source_url": source_url,
            "attributes": attributes
        }
        self.ingested_threads.append(record)
        return {"ko_id": f"mock_ko_{thread_id}"}

    async def upsert_knowledge_object(
        self,
        knowledge_base_id: str,
        ko: Dict[str, Any]
    ) -> str:
        """Mock KO upsert."""
        record = {"kb_id": knowledge_base_id, "ko": ko}
        self.upserted_kos.append(record)
        external_id = ko.get("external_id", "unknown")
        return f"mock_ko_{external_id}"

    async def write_ko(self, *, kb_id: str, ko: Dict[str, Any]) -> Dict[str, Any]:
        """Mock KO write (non-standard method used by Outlook Mail plugin).

        NOTE: This method doesn't exist in the actual KbCapability. The plugin
        should use upsert_knowledge_object() instead. This mock exists for
        backward compatibility with tests until the plugin is refactored.
        """
        record = {"kb_id": kb_id, "ko": ko}
        self.written_kos.append(record)
        return {"id": f"mock_ko_{ko.get('external_id', 'unknown')}"}

    async def delete_ko(self, *, external_id: str) -> Dict[str, Any]:
        """Mock KO deletion."""
        self.deleted_kos.append(external_id)
        return {"deleted": True, "ko_id": f"mock_ko_{external_id}"}

    async def delete_kos_batch(self, *, external_ids: List[str]) -> Dict[str, Any]:
        """Mock batch KO deletion."""
        for eid in external_ids:
            self.deleted_kos.append(eid)
        return {"deleted_count": len(external_ids), "failed": []}


class MockHostCache:
    """Mock host.cache capability with safe methods."""

    def __init__(self):
        self.cache: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        return self.cache.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Set a value in cache."""
        self.cache[key] = value

    async def set_safe(self, key: str, value: Any, ttl_seconds: int = 300) -> bool:
        """Safe version that returns bool instead of raising."""
        self.cache[key] = value
        return True

    async def delete(self, key: str) -> None:
        """Delete a value from cache."""
        self.cache.pop(key, None)

    async def delete_safe(self, key: str) -> bool:
        """Safe version that returns bool instead of raising."""
        self.cache.pop(key, None)
        return True


class MockHostCursor:
    """Mock host.cursor capability with safe methods."""

    def __init__(self):
        self.cursors: Dict[str, str] = {}

    async def get(self, kb_id: str) -> Optional[str]:
        """Get cursor value for a knowledge base."""
        return self.cursors.get(kb_id)

    async def set(self, kb_id: str, value: str) -> None:
        """Set cursor value for a knowledge base."""
        self.cursors[kb_id] = value

    async def set_safe(self, kb_id: str, value: str) -> bool:
        """Safe version that returns bool instead of raising."""
        self.cursors[kb_id] = value
        return True

    async def delete(self, kb_id: str) -> None:
        """Delete cursor for a knowledge base."""
        self.cursors.pop(kb_id, None)

    async def delete_safe(self, kb_id: str) -> bool:
        """Safe version that returns bool instead of raising."""
        self.cursors.pop(kb_id, None)
        return True


class MockHostLog:
    """Mock host.log capability for structured logging."""

    def __init__(self):
        self.messages: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []

    def debug(self, msg: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log a debug message."""
        self.messages.append(("debug", msg, extra))

    def info(self, msg: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log an info message."""
        self.messages.append(("info", msg, extra))

    def warning(self, msg: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log a warning message."""
        self.messages.append(("warning", msg, extra))

    def error(self, msg: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log an error message."""
        self.messages.append(("error", msg, extra))

    def exception(self, msg: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log an exception message."""
        self.messages.append(("exception", msg, extra))


class MockHostUtils:
    """Mock host.utils capability for batch operations."""

    async def map_safe(
        self,
        items: List[T],
        async_fn: Callable[[T], Awaitable[R]],
        *,
        max_errors: Optional[int] = None
    ) -> Tuple[List[R], List[Tuple[T, Exception]]]:
        """Process items, collecting errors instead of failing.

        Args:
            items: List of items to process
            async_fn: Async function to apply to each item
            max_errors: Optional maximum errors before stopping

        Returns:
            Tuple of (results, errors)
        """
        results: List[R] = []
        errors: List[Tuple[T, Exception]] = []

        for item in items:
            if max_errors is not None and len(errors) >= max_errors:
                break
            try:
                result = await async_fn(item)
                results.append(result)
            except Exception as e:
                errors.append((item, e))

        return results, errors

    async def filter_safe(
        self,
        items: List[T],
        async_predicate: Callable[[T], Awaitable[bool]],
        *,
        max_errors: Optional[int] = None
    ) -> Tuple[List[T], List[Tuple[T, Exception]]]:
        """Filter items, collecting errors instead of failing.

        Args:
            items: List of items to filter
            async_predicate: Async function returning True to keep item
            max_errors: Optional maximum errors before stopping

        Returns:
            Tuple of (kept_items, errors)
        """
        kept: List[T] = []
        errors: List[Tuple[T, Exception]] = []

        for item in items:
            if max_errors is not None and len(errors) >= max_errors:
                break
            try:
                if await async_predicate(item):
                    kept.append(item)
            except Exception as e:
                errors.append((item, e))

        return kept, errors


class MockHost:
    """Complete mock host object with all capabilities.

    This is the main class that plugins interact with. It provides all
    standard host capabilities as mock implementations.

    Example:
        mock_host = MockHost()
        mock_host.http.set_response("/api/data", {"status_code": 200, "body": {"items": []}})
        result = await plugin.execute(params, None, mock_host)
    """

    def __init__(
        self,
        access_token: str = "mock_access_token",
        auth_should_fail: bool = False
    ):
        """Initialize MockHost with all capabilities.

        Args:
            access_token: Token to return from auth.resolve_token_and_target
            auth_should_fail: If True, auth will return (None, None)
        """
        self.auth = MockHostAuth(access_token, should_fail=auth_should_fail)
        self.http = MockHostHttp()
        self.kb = MockHostKb()
        self.cache = MockHostCache()
        self.cursor = MockHostCursor()
        self.log = MockHostLog()
        self.utils = MockHostUtils()

