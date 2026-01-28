"""Shared fixtures for Outlook Mail plugin tests."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from plugins.shu_outlook_mail.plugin import OutlookMailPlugin


class MockHttpRequestFailed(Exception):
    """Mock exception that simulates HttpRequestFailed from host.http."""
    def __init__(self, status_code: int, url: str, body: object = None, headers: dict = None):
        self.status_code = status_code
        self.url = url
        self.body = body
        self.headers = headers or {}
        super().__init__(f"HTTP {status_code} calling {url}")


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
    
    # Mock auth capability
    host.auth = AsyncMock()
    host.auth.resolve_token_and_target = AsyncMock(return_value={
        "access_token": "test_token_123"
    })
    
    # Mock http capability
    host.http = AsyncMock()
    host.http.fetch = AsyncMock()
    
    # Mock kb capability
    host.kb = AsyncMock()
    host.kb.ingest_email = AsyncMock()
    host.kb.delete_ko = AsyncMock()
    host.kb.write_ko = AsyncMock(return_value={"id": "mock_ko_id"})
    
    # Mock cursor capability
    host.cursor = AsyncMock()
    host.cursor.get = AsyncMock(return_value=None)
    host.cursor.set = AsyncMock()
    host.cursor.delete = AsyncMock()
    
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
        async def resolve_token_and_target(self, provider):
            return {"access_token": "mock_token_123"}
    
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
            self.write_calls = []
            self.ingest_calls = []
        
        async def write_ko(self, kb_id, ko):
            self.write_calls.append({"kb_id": kb_id, "ko": ko})
            return {"id": "mock_ko_id"}
        
        async def ingest_email(self, kb_id, **kwargs):
            self.ingest_calls.append({"kb_id": kb_id, **kwargs})
            return {"id": "mock_email_ko_id"}
    
    class MockCursor:
        def __init__(self):
            self.cursor_value = None
        
        async def get(self, key):
            return self.cursor_value
        
        async def set(self, key, value):
            self.cursor_value = value
        
        async def delete(self, key):
            self.cursor_value = None
    
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
