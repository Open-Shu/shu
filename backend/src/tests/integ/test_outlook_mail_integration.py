"""
Outlook Mail Plugin Integration Tests

These tests verify the Outlook Mail plugin operations:
- List operation: Fetch recent messages from inbox
- Digest operation: Create summary digest of inbox activity
- Ingest operation: Ingest emails into knowledge base with delta sync
- Error handling: Auth failures, missing parameters, API errors
"""

import logging
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.helpers.mock_host import MockHost, create_mock_graph_response

logger = logging.getLogger(__name__)

# Alias for backward compatibility with tests that use local naming
_create_mock_graph_response = create_mock_graph_response


# ============================================================================
# Mock Fixtures for Graph API Responses
# ============================================================================

def _create_mock_message(
    message_id: str = None,
    subject: str = "Test Subject",
    from_email: str = "sender@example.com",
    from_name: str = "Test Sender",
    to_emails: list[str] = None,
    received_datetime: str = None,
    body_preview: str = "Test body preview",
    body_content: str = "Test body content",
    body_type: str = "text"
) -> dict[str, Any]:
    """Create a mock Graph API message object."""
    if message_id is None:
        message_id = f"AAMkAGI2{uuid.uuid4().hex[:20]}"

    if received_datetime is None:
        received_datetime = datetime.now(UTC).isoformat()

    if to_emails is None:
        to_emails = ["recipient@example.com"]

    return {
        "id": message_id,
        "subject": subject,
        "from": {
            "emailAddress": {
                "name": from_name,
                "address": from_email
            }
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "name": f"Recipient {i}",
                    "address": email
                }
            }
            for i, email in enumerate(to_emails)
        ],
        "ccRecipients": [],
        "bccRecipients": [],
        "receivedDateTime": received_datetime,
        "bodyPreview": body_preview,
        "body": {
            "contentType": body_type,
            "content": body_content
        }
    }


def _create_mock_deleted_message(message_id: str) -> dict[str, Any]:
    """Create a mock deleted message for delta sync."""
    return {
        "id": message_id,
        "@removed": {
            "reason": "deleted"
        }
    }


# ============================================================================
# Test Functions
# ============================================================================

async def test_list_operation_default_parameters(client, db, auth_headers):
    """Test list operation with default parameters."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    # Create plugin instance
    plugin = OutlookMailPlugin()

    # Create mock host
    mock_host = MockHost()

    # Create mock messages
    messages = [
        _create_mock_message(
            message_id=f"msg_{i}",
            subject=f"Test Message {i}",
            from_email=f"sender{i}@example.com",
            from_name=f"Sender {i}"
        )
        for i in range(5)
    ]

    # Set up mock response
    mock_host.http.set_default_response(_create_mock_graph_response(messages))

    # Execute list operation
    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.data is not None
    assert "messages" in result.data
    assert len(result.data["messages"]) == 5
    assert result.data["count"] == 5

    # Verify Graph API was called correctly
    assert len(mock_host.http.requests) > 0
    request = mock_host.http.requests[0]
    assert "graph.microsoft.com" in request["url"]
    assert "/me/mailFolders/inbox/messages" in request["url"]
    assert request["headers"]["Authorization"] == "Bearer mock_access_token"


async def test_list_operation_with_filters(client, db, auth_headers):
    """Test list operation with since_hours and query_filter parameters."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    # Create mock messages
    messages = [_create_mock_message(message_id=f"msg_{i}") for i in range(3)]
    mock_host.http.set_default_response(_create_mock_graph_response(messages))

    # Execute with filters
    params = {
        "op": "list",
        "since_hours": 24,
        "query_filter": "from/emailAddress/address eq 'test@example.com'",
        "max_results": 10
    }
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success"
    assert len(result.data["messages"]) == 3

    # Verify filter was applied in request
    request = mock_host.http.requests[0]
    assert "$filter" in request["url"] or "filter" in str(request.get("params", {}))


async def test_list_operation_auth_failure(client, db, auth_headers):
    """Test list operation returns error when authentication fails."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    # Use auth_should_fail=True to simulate auth resolution failure
    # This is the same pattern used by Outlook Calendar tests
    mock_host = MockHost(auth_should_fail=True)

    logger.info("=== EXPECTED TEST OUTPUT: Auth resolution failure is expected ===")

    # Execute list operation
    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    # Verify error result
    assert result.status == "error"
    assert result.error is not None
    assert result.error["code"] == "auth_missing_or_insufficient_scopes"

    logger.info("=== EXPECTED TEST OUTPUT: Auth failure error occurred as expected ===")


async def test_digest_operation_creates_summary(client, db, auth_headers):
    """Test digest operation creates inbox summary with sender analysis."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    # Create mock messages from different senders
    messages = [
        _create_mock_message(message_id="msg_1", from_email="alice@example.com", from_name="Alice"),
        _create_mock_message(message_id="msg_2", from_email="alice@example.com", from_name="Alice"),
        _create_mock_message(message_id="msg_3", from_email="bob@example.com", from_name="Bob"),
        _create_mock_message(message_id="msg_4", from_email="alice@example.com", from_name="Alice"),
    ]
    mock_host.http.set_default_response(_create_mock_graph_response(messages))

    # Execute digest operation
    params = {
        "op": "digest",
        "kb_id": "test_kb_123"
    }
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success"
    assert "ko" in result.data
    assert "count" in result.data
    assert "window" in result.data

    # Verify KO structure
    ko = result.data["ko"]
    assert ko["type"] == "email_digest"
    assert "title" in ko
    assert "content" in ko
    assert "attributes" in ko

    # Verify sender analysis
    attributes = ko["attributes"]
    assert attributes["total_count"] == 4
    assert "top_senders" in attributes
    assert len(attributes["top_senders"]) == 2  # Alice and Bob

    # Verify top sender is Alice (3 messages)
    top_sender = attributes["top_senders"][0]
    assert top_sender["email"] == "alice@example.com"
    assert top_sender["count"] == 3

    # Verify digest was written to KB using upsert_knowledge_object
    assert len(mock_host.kb.upserted_kos) == 1
    assert mock_host.kb.upserted_kos[0]["kb_id"] == "test_kb_123"


async def test_digest_operation_without_kb_id(client, db, auth_headers):
    """Test digest operation works without kb_id (chat-callable)."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    messages = [_create_mock_message(message_id=f"msg_{i}") for i in range(2)]
    mock_host.http.set_default_response(_create_mock_graph_response(messages))

    # Execute digest without kb_id
    params = {"op": "digest"}
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success"
    assert "ko" in result.data

    # Verify no KB write occurred (plugin now uses upsert_knowledge_object)
    assert len(mock_host.kb.upserted_kos) == 0


async def test_ingest_operation_requires_kb_id(client, db, auth_headers):
    """Test ingest operation returns error when kb_id is missing."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    logger.info("=== EXPECTED TEST OUTPUT: Missing kb_id error is expected ===")

    # Execute ingest without kb_id
    params = {"op": "ingest"}
    result = await plugin.execute(params, None, mock_host)

    # Verify error result
    assert result.status == "error"
    assert result.error is not None
    assert result.error["code"] == "missing_parameter"
    assert "kb_id is required" in result.error["message"]

    logger.info("=== EXPECTED TEST OUTPUT: Missing kb_id error occurred as expected ===")


async def test_ingest_operation_delta_sync(client, db, auth_headers):
    """Test ingest operation uses delta sync when cursor exists."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    # Set up existing cursor
    await mock_host.cursor.set("test_kb_789", "https://graph.microsoft.com/delta?token=existing")

    # Create mock delta response with new and deleted messages
    new_message = _create_mock_message(message_id="msg_new", subject="New Message")
    deleted_message = _create_mock_deleted_message("msg_deleted")

    delta_response = _create_mock_graph_response(
        [new_message, deleted_message],
        delta_link="https://graph.microsoft.com/delta?token=updated"
    )

    # Set up mock responses
    mock_host.http.set_response("delta?token=existing", delta_response)
    mock_host.http.set_response("/me/messages/msg_new", {
        "status_code": 200,
        "body": new_message
    })

    # Execute ingest operation
    params = {
        "op": "ingest",
        "kb_id": "test_kb_789"
    }
    result = await plugin.execute(params, None, mock_host)

    # Verify result
    assert result.status == "success"
    assert result.data["count"] == 1  # One new message
    assert result.data["deleted"] == 1  # One deleted message

    # Verify new message was ingested
    assert len(mock_host.kb.ingested_emails) == 1
    assert mock_host.kb.ingested_emails[0]["message_id"] == "msg_new"

    # Verify deleted message was removed
    assert len(mock_host.kb.deleted_kos) == 1
    assert mock_host.kb.deleted_kos[0] == "msg_deleted"

    # Verify delta token was updated
    cursor = await mock_host.cursor.get("test_kb_789")
    assert "token=updated" in cursor


async def test_ingest_operation_delta_token_expired(client, db, auth_headers):
    """Test ingest operation falls back to full sync when delta token expires (410)."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    # Set up existing cursor
    await mock_host.cursor.set("test_kb_expired", "https://graph.microsoft.com/delta?token=expired")

    # Set up 410 response for delta query
    mock_host.http.set_response("delta?token=expired", {
        "status_code": 410,
        "error": {
            "message": "Delta token expired"
        }
    })

    # Set up full sync response
    messages = [_create_mock_message(message_id="msg_1")]
    mock_host.http.set_response("/me/mailFolders/inbox/messages",
                               _create_mock_graph_response(messages))
    mock_host.http.set_response("/me/messages/msg_1", {
        "status_code": 200,
        "body": messages[0]
    })

    logger.info("=== EXPECTED TEST OUTPUT: 410 delta token expired is expected, fallback to full sync ===")

    # Execute ingest operation
    params = {
        "op": "ingest",
        "kb_id": "test_kb_expired"
    }
    result = await plugin.execute(params, None, mock_host)

    # Verify result (should succeed with full sync)
    assert result.status == "success"
    assert result.data["count"] == 1

    logger.info("=== EXPECTED TEST OUTPUT: Fallback to full sync succeeded as expected ===")


async def test_invalid_operation_parameter(client, db, auth_headers):
    """Test plugin returns error for invalid operation."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    logger.info("=== EXPECTED TEST OUTPUT: Invalid operation error is expected ===")

    # Execute with invalid op
    params = {"op": "invalid_operation"}
    result = await plugin.execute(params, None, mock_host)

    # Verify error result
    assert result.status == "error"
    assert result.error["code"] == "invalid_parameter"
    assert "Unsupported op" in result.error["message"]

    logger.info("=== EXPECTED TEST OUTPUT: Invalid operation error occurred as expected ===")


async def test_parameter_validation_since_hours(client, db, auth_headers):
    """Test parameter validation for since_hours range."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    logger.info("=== EXPECTED TEST OUTPUT: Parameter validation errors are expected ===")

    # Test since_hours too small
    params = {"op": "list", "since_hours": 0}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "since_hours must be between 1 and 3360" in result.error["message"]

    # Test since_hours too large
    params = {"op": "list", "since_hours": 5000}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "since_hours must be between 1 and 3360" in result.error["message"]

    logger.info("=== EXPECTED TEST OUTPUT: Parameter validation errors occurred as expected ===")


async def test_parameter_validation_max_results(client, db, auth_headers):
    """Test parameter validation for max_results range."""
    from plugins.shu_outlook_mail.plugin import OutlookMailPlugin

    plugin = OutlookMailPlugin()
    mock_host = MockHost()

    logger.info("=== EXPECTED TEST OUTPUT: Parameter validation errors are expected ===")

    # Test max_results too small
    params = {"op": "list", "max_results": 0}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_results must be between 1 and 500" in result.error["message"]

    # Test max_results too large
    params = {"op": "list", "max_results": 1000}
    result = await plugin.execute(params, None, mock_host)
    assert result.status == "error"
    assert "max_results must be between 1 and 500" in result.error["message"]

    logger.info("=== EXPECTED TEST OUTPUT: Parameter validation errors occurred as expected ===")


# ============================================================================
# Test Suite Class
# ============================================================================

class OutlookMailIntegrationTestSuite(BaseIntegrationTestSuite):
    """Integration test suite for Outlook Mail Plugin."""

    def get_test_functions(self) -> list[Callable]:
        """Return all Outlook Mail plugin test functions."""
        return [
            test_list_operation_default_parameters,
            test_list_operation_with_filters,
            test_list_operation_auth_failure,
            test_digest_operation_creates_summary,
            test_digest_operation_without_kb_id,
            test_ingest_operation_requires_kb_id,
            test_ingest_operation_delta_sync,
            test_ingest_operation_delta_token_expired,
            test_invalid_operation_parameter,
            test_parameter_validation_since_hours,
            test_parameter_validation_max_results,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Outlook Mail Plugin Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Outlook Mail plugin operations (list, digest, ingest) with delta sync"


if __name__ == "__main__":
    suite = OutlookMailIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
