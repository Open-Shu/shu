"""
Teams Chat Plugin Integration Tests

These tests verify the Teams Chat plugin operations:
- List operation: Fetch recent chat messages
- Ingest operation: Ingest messages into knowledge base with timestamp watermark
- Sender resolution: Cache user profile lookups
- Error handling: Auth failures, missing parameters, API errors
"""

import logging
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from integ.base_integration_test import BaseIntegrationTestSuite

logger = logging.getLogger(__name__)


# ============================================================================
# Mock Fixtures for Graph API Responses
# ============================================================================

def _create_mock_chat(
    chat_id: str = None,
    topic: str = "Test Chat",
    chat_type: str = "group",
    last_updated_hours_ago: int = 0
) -> dict[str, Any]:
    """Create a mock Graph API chat object."""
    if chat_id is None:
        chat_id = f"19:{uuid.uuid4().hex}@thread.v2"

    last_updated = datetime.now(UTC) - timedelta(hours=last_updated_hours_ago)

    return {
        "id": chat_id,
        "topic": topic,
        "chatType": chat_type,
        "lastUpdatedDateTime": last_updated.isoformat().replace("+00:00", "Z")
    }


def _create_mock_message(
    message_id: str = None,
    content: str = "Test message content",
    sender_id: str = None,
    sender_name: str = "Test User",
    created_hours_ago: int = 0,
    content_type: str = "text",
    message_type: str = "message"
) -> dict[str, Any]:
    """Create a mock Graph API chat message object."""
    if message_id is None:
        message_id = str(uuid.uuid4())
    if sender_id is None:
        sender_id = str(uuid.uuid4())

    created_time = datetime.now(UTC) - timedelta(hours=created_hours_ago)

    return {
        "id": message_id,
        "messageType": message_type,
        "createdDateTime": created_time.isoformat().replace("+00:00", "Z"),
        "body": {
            "contentType": content_type,
            "content": content
        },
        "from": {
            "user": {
                "id": sender_id,
                "displayName": sender_name
            }
        }
    }


def _create_mock_user(
    user_id: str,
    display_name: str = "Test User",
    email: str = None
) -> dict[str, Any]:
    """Create a mock Graph API user object."""
    if email is None:
        email = f"{display_name.lower().replace(' ', '.')}@example.com"

    return {
        "id": user_id,
        "displayName": display_name,
        "mail": email,
        "userPrincipalName": email
    }


# ============================================================================
# Mock Host (shared module)
# ============================================================================

from integ.helpers.mock_host import MockHost, create_mock_graph_response

# Local alias for backward compatibility in tests
_create_mock_graph_response = create_mock_graph_response


# ============================================================================
# Test Functions
# ============================================================================

async def test_list_operation_default_parameters(client, db, auth_headers):
    """Test list operation with default parameters."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Set up mock chats
    chats = [_create_mock_chat(chat_id=f"chat_{i}", topic=f"Chat {i}") for i in range(3)]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    # Set up mock messages for each chat
    for i in range(3):
        messages = [
            _create_mock_message(message_id=f"msg_{i}_{j}", content=f"Message {j} in chat {i}")
            for j in range(2)
        ]
        mock_host.http.set_response(f"chat_{i}/messages", _create_mock_graph_response(messages))

    # Execute list operation
    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.data is not None
    assert "messages" in result.data
    assert result.data["count"] == 6  # 3 chats * 2 messages each
    assert result.data["chats_processed"] == 3


async def test_list_operation_with_time_window(client, db, auth_headers):
    """Test list operation with since_hours parameter."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chats = [_create_mock_chat(chat_id="chat_1")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    messages = [_create_mock_message(message_id=f"msg_{i}") for i in range(5)]
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response(messages))

    params = {"op": "list", "since_hours": 24}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 5


async def test_list_operation_auth_failure(client, db, auth_headers):
    """Test list operation returns error when authentication fails."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost(auth_should_fail=True)

    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert result.error is not None
    assert "auth" in result.error.get("code", "").lower() or "token" in result.error.get("message", "").lower()


async def test_ingest_operation_requires_kb_id(client, db, auth_headers):
    """Test ingest operation returns error when kb_id is missing."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    params = {"op": "ingest"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert "kb_id" in result.error.get("message", "").lower()


async def test_ingest_operation_full_sync(client, db, auth_headers):
    """Test ingest operation with full sync (no existing cursor)."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chats = [_create_mock_chat(chat_id="chat_1", topic="Project Chat")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    messages = [
        _create_mock_message(message_id=f"msg_{i}", content=f"Test message {i}", sender_name=f"User {i}")
        for i in range(3)
    ]
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response(messages))

    # Mock user lookup
    mock_host.http.set_response("/users/", _create_mock_graph_response([]))

    params = {"op": "ingest", "kb_id": "test-kb-123"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.data["count"] == 3
    assert result.data["chats_processed"] == 1
    assert len(mock_host.kb.ingested_texts) == 3

    # Verify cursor was set
    cursor = await mock_host.cursor.get("test-kb-123")
    assert cursor is not None


async def test_ingest_operation_incremental_sync(client, db, auth_headers):
    """Test ingest operation with incremental sync (existing cursor)."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Set existing cursor
    old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    await mock_host.cursor.set("test-kb-123", old_ts)

    chats = [_create_mock_chat(chat_id="chat_1")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    # Only new messages (after cursor)
    messages = [_create_mock_message(message_id="new_msg_1", content="New message")]
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response(messages))

    params = {"op": "ingest", "kb_id": "test-kb-123"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 1


async def test_ingest_operation_reset_cursor(client, db, auth_headers):
    """Test ingest operation with reset_cursor performs full sync."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Set existing cursor
    old_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    await mock_host.cursor.set("test-kb-123", old_ts)

    chats = [_create_mock_chat(chat_id="chat_1")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    messages = [_create_mock_message(message_id=f"msg_{i}") for i in range(5)]
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response(messages))

    params = {"op": "ingest", "kb_id": "test-kb-123", "reset_cursor": True}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 5


async def test_sender_resolution_with_caching(client, db, auth_headers):
    """Test that sender resolution caches user profiles."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chats = [_create_mock_chat(chat_id="chat_1")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    # Multiple messages from the same sender
    sender_id = "user-123"
    messages = [
        _create_mock_message(message_id=f"msg_{i}", content=f"Message {i}", sender_id=sender_id, sender_name="Test User")
        for i in range(3)
    ]
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response(messages))

    # Mock user lookup response
    user_data = _create_mock_user(sender_id, "Test User", "test.user@example.com")
    mock_host.http.set_response(f"/users/{sender_id}", {"status_code": 200, "body": user_data})

    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"

    # Check cache was used (should have cached the user)
    cache_key = f"teams_user:{sender_id}"
    cached = await mock_host.cache.get(cache_key)
    assert cached is not None
    assert cached["displayName"] == "Test User"


async def test_html_content_stripping(client, db, auth_headers):
    """Test that HTML content is stripped from messages."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chats = [_create_mock_chat(chat_id="chat_1")]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    # Message with HTML content
    html_message = _create_mock_message(
        message_id="html_msg",
        content="<p>Hello <strong>world</strong>!</p>",
        content_type="html"
    )
    mock_host.http.set_response("chat_1/messages", _create_mock_graph_response([html_message]))

    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 1

    # Verify HTML was stripped
    message_content = result.data["messages"][0]["content"]
    assert "<p>" not in message_content
    assert "<strong>" not in message_content
    assert "Hello" in message_content
    assert "world" in message_content


async def test_parameter_validation_since_hours(client, db, auth_headers):
    """Test that since_hours parameter is validated."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Test too low
    params = {"op": "list", "since_hours": 0}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "since_hours" in result.error.get("message", "").lower()

    # Test too high
    params = {"op": "list", "since_hours": 500}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "since_hours" in result.error.get("message", "").lower()


async def test_parameter_validation_max_chats(client, db, auth_headers):
    """Test that max_chats parameter is validated."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Test too low
    params = {"op": "list", "max_chats": 0}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_chats" in result.error.get("message", "").lower()

    # Test too high
    params = {"op": "list", "max_chats": 200}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_chats" in result.error.get("message", "").lower()


async def test_parameter_validation_max_messages_per_chat(client, db, auth_headers):
    """Test that max_messages_per_chat parameter is validated."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    # Test too low
    params = {"op": "list", "max_messages_per_chat": 0}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_messages_per_chat" in result.error.get("message", "").lower()

    # Test too high
    params = {"op": "list", "max_messages_per_chat": 1000}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_messages_per_chat" in result.error.get("message", "").lower()


async def test_invalid_operation_parameter(client, db, auth_headers):
    """Test that invalid operation returns error."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    params = {"op": "invalid_op"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert "unsupported operation" in result.error.get("message", "").lower()


async def test_ingest_creates_correct_source_id(client, db, auth_headers):
    """Test that ingest operation creates correct source_id for deduplication."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chat_id = "chat_123"
    msg_id = "msg_456"

    chats = [_create_mock_chat(chat_id=chat_id)]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    messages = [_create_mock_message(message_id=msg_id, content="Test content")]
    mock_host.http.set_response(f"{chat_id}/messages", _create_mock_graph_response(messages))

    params = {"op": "ingest", "kb_id": "test-kb-123"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert len(mock_host.kb.ingested_texts) == 1

    # Verify source_id format: teams:{chat_id}:{msg_id}
    ingested = mock_host.kb.ingested_texts[0]
    assert ingested["source_id"] == f"teams:{chat_id}:{msg_id}"


async def test_ingest_extracts_message_attributes(client, db, auth_headers):
    """Test that ingest operation extracts correct message attributes."""
    from plugins.shu_teams_chat.plugin import TeamsChatPlugin

    plugin = TeamsChatPlugin()
    mock_host = MockHost()

    chat_id = "chat_abc"
    chat_topic = "Project Discussion"
    chat_type = "group"
    sender_id = "user-xyz"
    sender_name = "John Doe"

    chats = [_create_mock_chat(chat_id=chat_id, topic=chat_topic, chat_type=chat_type)]
    mock_host.http.set_response("/me/chats", _create_mock_graph_response(chats))

    messages = [_create_mock_message(
        message_id="msg_1",
        content="Test message",
        sender_id=sender_id,
        sender_name=sender_name
    )]
    mock_host.http.set_response(f"{chat_id}/messages", _create_mock_graph_response(messages))

    params = {"op": "ingest", "kb_id": "test-kb-123"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert len(mock_host.kb.ingested_texts) == 1

    attrs = mock_host.kb.ingested_texts[0]["attributes"]
    assert attrs["chat_id"] == chat_id
    assert attrs["chat_topic"] == chat_topic
    assert attrs["chat_type"] == chat_type
    assert attrs["plugin"] == "teams_chat"


# ============================================================================
# Test Suite Runner
# ============================================================================

class TeamsChatIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Teams Chat plugin."""

    def get_test_functions(self) -> list[Callable]:
        """Return all test functions for this suite."""
        return [
            test_list_operation_default_parameters,
            test_list_operation_with_time_window,
            test_list_operation_auth_failure,
            test_ingest_operation_requires_kb_id,
            test_ingest_operation_full_sync,
            test_ingest_operation_incremental_sync,
            test_ingest_operation_reset_cursor,
            test_sender_resolution_with_caching,
            test_html_content_stripping,
            test_parameter_validation_since_hours,
            test_parameter_validation_max_chats,
            test_parameter_validation_max_messages_per_chat,
            test_invalid_operation_parameter,
            test_ingest_creates_correct_source_id,
            test_ingest_extracts_message_attributes,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Teams Chat Plugin Integration Tests"

    def get_suite_description(self) -> str:
        """Return a description of this test suite."""
        return "End-to-end integration tests for Teams Chat plugin operations (list, ingest) with timestamp watermark sync"


if __name__ == "__main__":
    suite = TeamsChatIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
