"""Shared fixtures for Outlook Mail plugin tests."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.shu_outlook_mail.plugin import OutlookMailPlugin


class HttpRequestFailed(Exception):
    """Copy of HttpRequestFailed from shu.plugins.host.exceptions.
    
    This is a standalone copy to avoid circular import issues when running
    unit tests. It has the exact same interface as the real class so the
    plugin's exception handling works correctly.
    
    The real class is at: shu/backend/src/shu/plugins/host/exceptions.py
    """
    def __init__(self, status_code: int, url: str, body: object = None, headers: dict = None):
        self.status_code = int(status_code)
        self.url = str(url)
        self.body = body
        self.headers = dict(headers or {})
        msg = f"HTTP {self.status_code} calling {self.url}"
        super().__init__(msg)

    @property
    def error_category(self) -> str:
        if self.status_code == 401:
            return "auth_error"
        if self.status_code == 403:
            return "forbidden"
        if self.status_code == 404:
            return "not_found"
        if self.status_code == 410:
            return "gone"
        if self.status_code == 429:
            return "rate_limited"
        if self.status_code >= 500:
            return "server_error"
        return "client_error"

    @property
    def is_retryable(self) -> bool:
        return self.status_code == 429 or self.status_code >= 500

    @property
    def retry_after_seconds(self):
        retry_after = self.headers.get("retry-after") or self.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return int(retry_after)
        except (ValueError, TypeError):
            return None

    @property
    def provider_message(self) -> str:
        if self.body is None:
            return ""
        if isinstance(self.body, str):
            return self.body[:500] if len(self.body) > 500 else self.body
        if isinstance(self.body, dict):
            error_obj = self.body.get("error")
            if isinstance(error_obj, dict):
                msg = error_obj.get("message")
                if msg:
                    return str(msg)
            for key in ("error_description", "message", "error", "detail"):
                val = self.body.get(key)
                if val and isinstance(val, str):
                    return val
            return str(self.body)[:500]
        return str(self.body)[:500]

    @property
    def provider_error_code(self):
        if not isinstance(self.body, dict):
            return None
        error_obj = self.body.get("error")
        if isinstance(error_obj, dict):
            code = error_obj.get("code") or error_obj.get("status")
            if code:
                return str(code)
        code = self.body.get("code")
        if code:
            return str(code)
        return None


def wrap_graph_response(body_data: dict, status_code: int = 200) -> dict:
    """
    Wrap Graph API response data in the format returned by http_capability.py.
    
    The http_capability.py returns responses in the format:
    {"status_code": ..., "headers": {...}, "body": <actual_api_response>}
    
    Args:
        body_data: The actual Graph API response (with "value", "@odata.nextLink", etc.)
        status_code: HTTP status code (default 200)
        
    Returns:
        Response dict in the format expected by the plugin
    """
    return {
        "status_code": status_code,
        "headers": {},
        "body": body_data
    }


@pytest.fixture
def plugin():
    """Create plugin instance for testing."""
    return OutlookMailPlugin()


@pytest.fixture
def mock_host():
    """Create mock host with all required capabilities using MagicMock/AsyncMock."""
    host = MagicMock()

    # Mock auth capability - returns Tuple[Optional[str], Optional[str]]
    host.auth = AsyncMock()
    host.auth.resolve_token_and_target = AsyncMock(return_value=("test_token_123", "me"))

    # Mock http capability
    host.http = AsyncMock()
    host.http.fetch = AsyncMock()

    # Mock kb capability
    host.kb = AsyncMock()
    host.kb.ingest_email = AsyncMock()
    host.kb.delete_ko = AsyncMock()
    host.kb.upsert_knowledge_object = AsyncMock(return_value="mock_ko_id")

    # Mock cursor capability
    host.cursor = AsyncMock()
    host.cursor.get = AsyncMock(return_value=None)
    host.cursor.set = AsyncMock()
    host.cursor.set_safe = AsyncMock(return_value=True)
    host.cursor.delete = AsyncMock()
    host.cursor.delete_safe = AsyncMock(return_value=True)

    return host


def create_mock_host_with_messages(messages=None, track_requests=False):
    """
    Factory function to create mock host with sample messages.
    
    Args:
        messages: List of message dicts to return from fetch
        track_requests: If True, track fetch calls in http.fetch_calls list
    """
    if messages is None:
        messages = []

    class MockAuth:
        async def resolve_token_and_target(self, provider, *, scopes=None):
            """Returns Tuple[Optional[str], Optional[str]] matching real AuthCapability."""
            return ("mock_token_123", "me")

    class MockHttp:
        def __init__(self):
            self.fetch_calls = []
            self.last_url = None
            self.last_headers = None

        async def fetch(self, method, url, headers, params=None, json=None):
            self.last_url = url
            self.last_headers = headers
            if track_requests:
                self.fetch_calls.append({
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "params": params
                })
            # Return response in the same format as http_capability.py
            # The actual Graph API response is in the "body" field
            return {
                "status_code": 200,
                "headers": {},
                "body": {
                    "value": messages,
                    "@odata.nextLink": None
                }
            }

    class MockKb:
        def __init__(self):
            self.upsert_calls = []
            self.ingest_calls = []

        async def upsert_knowledge_object(self, knowledge_base_id, ko):
            self.upsert_calls.append({"knowledge_base_id": knowledge_base_id, "ko": ko})
            return "mock_ko_id"

        async def ingest_email(self, knowledge_base_id, **kwargs):
            self.ingest_calls.append({"knowledge_base_id": knowledge_base_id, **kwargs})
            return {"ko_id": "mock_email_ko_id"}

    class MockCursor:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

    class MockHost:
        def __init__(self):
            self.auth = MockAuth()
            self.http = MockHttp()
            self.kb = MockKb()
            self.cursor = MockCursor()

    return MockHost()


def create_sample_message(
    msg_id="msg1",
    subject="Test Subject",
    sender_email="sender@example.com",
    sender_name="Test Sender",
    received_datetime="2024-01-15T10:00:00Z",
    body_preview="Test body preview",
    include_body=False
):
    """Create a sample message dict for testing."""
    msg = {
        "id": msg_id,
        "subject": subject,
        "from": {
            "emailAddress": {
                "name": sender_name,
                "address": sender_email
            }
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "name": "Recipient",
                    "address": "recipient@example.com"
                }
            }
        ],
        "ccRecipients": [],
        "bccRecipients": [],
        "receivedDateTime": received_datetime,
        "bodyPreview": body_preview
    }

    if include_body:
        msg["body"] = {"contentType": "text", "content": "Full message body content"}

    return msg


def create_sample_messages(count=3, sender_distribution=None):
    """
    Create multiple sample messages.
    
    Args:
        count: Number of messages to create
        sender_distribution: Optional dict mapping sender emails to count of messages
    """
    messages = []

    if sender_distribution:
        msg_id = 1
        for email, msg_count in sender_distribution.items():
            for i in range(msg_count):
                messages.append(create_sample_message(
                    msg_id=f"msg{msg_id}",
                    subject=f"Subject from {email} #{i+1}",
                    sender_email=email,
                    sender_name=email.split("@")[0].title()
                ))
                msg_id += 1
    else:
        for i in range(count):
            messages.append(create_sample_message(
                msg_id=f"msg{i+1}",
                subject=f"Test Subject {i+1}",
                sender_email=f"sender{i+1}@example.com",
                sender_name=f"Sender {i+1}"
            ))

    return messages
