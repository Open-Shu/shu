"""
Outlook Calendar Plugin Integration Tests

These tests verify the Outlook Calendar plugin operations:
- List operation: Fetch recent calendar events
- Ingest operation: Ingest events into knowledge base with delta sync
- Error handling: Auth failures, missing parameters, API errors
"""

import sys
import logging
import uuid
from typing import List, Callable, Dict, Any
from datetime import datetime, timezone, timedelta

from integ.base_integration_test import BaseIntegrationTestSuite
from integ.response_utils import extract_data

logger = logging.getLogger(__name__)


# ============================================================================
# Mock Fixtures for Graph API Responses
# ============================================================================

def _create_mock_event(
    event_id: str = None,
    subject: str = "Test Meeting",
    start_hours_offset: int = 0,
    duration_hours: int = 1,
    location: str = "Conference Room A",
    attendees: List[str] = None,
    organizer_email: str = "organizer@example.com",
    body_preview: str = "Test event body preview",
    body_content: str = "Test event body content",
    is_cancelled: bool = False,
    online_meeting_url: str = None
) -> Dict[str, Any]:
    """Create a mock Graph API calendar event object."""
    if event_id is None:
        event_id = f"AAMkAGI2{uuid.uuid4().hex[:20]}"
    
    if attendees is None:
        attendees = ["attendee1@example.com", "attendee2@example.com"]
    
    start_time = datetime.now(timezone.utc) + timedelta(hours=start_hours_offset)
    end_time = start_time + timedelta(hours=duration_hours)
    
    event = {
        "id": event_id,
        "subject": subject,
        "start": {
            "dateTime": start_time.isoformat().replace("+00:00", ""),
            "timeZone": "UTC"
        },
        "end": {
            "dateTime": end_time.isoformat().replace("+00:00", ""),
            "timeZone": "UTC"
        },
        "location": {"displayName": location},
        "bodyPreview": body_preview,
        "body": {"contentType": "text", "content": body_content},
        "attendees": [
            {"emailAddress": {"name": f"Attendee {i}", "address": email}, "type": "required"}
            for i, email in enumerate(attendees)
        ],
        "organizer": {"emailAddress": {"name": "Organizer", "address": organizer_email}},
        "isCancelled": is_cancelled,
        "webLink": f"https://outlook.office365.com/owa/?itemid={event_id}",
    }
    
    if online_meeting_url:
        event["onlineMeeting"] = {"joinUrl": online_meeting_url}
    
    return event


def _create_mock_cancelled_event(event_id: str) -> Dict[str, Any]:
    """Create a mock cancelled event for delta sync."""
    return {
        "id": event_id,
        "isCancelled": True,
        "@removed": {"reason": "deleted"}
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
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    events = [_create_mock_event(event_id=f"evt_{i}", subject=f"Meeting {i}") for i in range(5)]
    mock_host.http.set_default_response(_create_mock_graph_response(events))

    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.data is not None
    assert "events" in result.data
    assert len(result.data["events"]) == 5
    assert result.data["count"] == 5

    assert len(mock_host.http.requests) > 0
    request = mock_host.http.requests[0]
    assert "graph.microsoft.com" in request["url"]
    assert "/me/calendarView" in request["url"]


async def test_list_operation_with_time_window(client, db, auth_headers):
    """Test list operation with since_hours parameter."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    events = [_create_mock_event(event_id=f"evt_{i}") for i in range(3)]
    mock_host.http.set_default_response(_create_mock_graph_response(events))

    params = {"op": "list", "since_hours": 24, "max_results": 10}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert len(result.data["events"]) == 3
    assert "window" in result.data
    assert result.data["window"]["hours"] == 24


async def test_list_operation_auth_failure(client, db, auth_headers):
    """Test list operation returns error when authentication fails."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost(auth_should_fail=True)

    logger.info("=== EXPECTED TEST OUTPUT: Auth resolution failure is expected ===")

    params = {"op": "list"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert result.error is not None
    assert result.error["code"] == "auth_missing_or_insufficient_scopes"


async def test_ingest_operation_requires_kb_id(client, db, auth_headers):
    """Test ingest operation returns error when kb_id is missing."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    params = {"op": "ingest"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert result.error["code"] == "missing_parameter"
    assert "kb_id" in result.error["message"]


async def test_ingest_operation_full_sync(client, db, auth_headers):
    """Test ingest operation with full sync (no existing cursor)."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    events = [_create_mock_event(event_id=f"evt_{i}", subject=f"Meeting {i}") for i in range(3)]
    delta_link = "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=abc123"
    mock_host.http.set_default_response(_create_mock_graph_response(events, delta_link=delta_link))

    params = {"op": "ingest", "kb_id": "test_kb_123"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success", f"Expected success, got {result.status}: {result.error}"
    assert result.data["count"] == 3
    assert result.data["deleted"] == 0
    assert result.data["next_sync_token"] == delta_link

    assert len(mock_host.kb.ingested_texts) == 3
    for i, ingested in enumerate(mock_host.kb.ingested_texts):
        assert ingested["kb_id"] == "test_kb_123"
        assert ingested["source_id"] == f"evt_{i}"
        assert ingested["attributes"]["plugin"] == "outlook_calendar"

    assert mock_host.cursor.cursors.get("test_kb_123") == delta_link


async def test_ingest_operation_delta_sync(client, db, auth_headers):
    """Test ingest operation with delta sync (existing cursor)."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    existing_delta_link = "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=existing"
    mock_host.cursor.cursors["test_kb_456"] = existing_delta_link

    new_events = [_create_mock_event(event_id=f"new_evt_{i}", subject=f"New Meeting {i}") for i in range(2)]
    new_delta_link = "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=updated"
    mock_host.http.set_default_response(_create_mock_graph_response(new_events, delta_link=new_delta_link))

    params = {"op": "ingest", "kb_id": "test_kb_456"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 2
    assert len(mock_host.kb.ingested_texts) == 2

    request = mock_host.http.requests[0]
    assert "deltatoken=existing" in request["url"]

    assert mock_host.cursor.cursors.get("test_kb_456") == new_delta_link


async def test_ingest_operation_handles_cancelled_events(client, db, auth_headers):
    """Test ingest operation correctly handles cancelled events by deleting them."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    events = [
        _create_mock_event(event_id="evt_1", subject="Active Meeting"),
        _create_mock_cancelled_event("evt_cancelled_1"),
        _create_mock_event(event_id="evt_2", subject="Another Meeting"),
    ]
    mock_host.http.set_default_response(_create_mock_graph_response(events))

    params = {"op": "ingest", "kb_id": "test_kb_789"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 2
    assert result.data["deleted"] == 1

    assert len(mock_host.kb.ingested_texts) == 2
    assert len(mock_host.kb.deleted_kos) == 1
    assert "evt_cancelled_1" in mock_host.kb.deleted_kos


async def test_ingest_operation_reset_cursor(client, db, auth_headers):
    """Test ingest operation with reset_cursor performs full sync."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    mock_host.cursor.cursors["test_kb_reset"] = "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=old"

    events = [_create_mock_event(event_id=f"evt_{i}") for i in range(2)]
    new_delta_link = "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=fresh"
    mock_host.http.set_default_response(_create_mock_graph_response(events, delta_link=new_delta_link))

    params = {"op": "ingest", "kb_id": "test_kb_reset", "reset_cursor": True}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert result.data["count"] == 2

    request = mock_host.http.requests[0]
    assert "deltatoken=old" not in request["url"]
    assert "/calendarView/delta" in request["url"]

    assert mock_host.cursor.cursors.get("test_kb_reset") == new_delta_link


async def test_parameter_validation_since_hours(client, db, auth_headers):
    """Test parameter validation for since_hours."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    for invalid_value in [0, -1, 337, 1000]:
        params = {"op": "list", "since_hours": invalid_value}
        result = await plugin.execute(params, None, mock_host)
        assert result.status == "error", f"Expected error for since_hours={invalid_value}"
        assert result.error["code"] == "invalid_parameter"


async def test_parameter_validation_max_results(client, db, auth_headers):
    """Test parameter validation for max_results."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    for invalid_value in [0, -1, 501, 1000]:
        params = {"op": "list", "max_results": invalid_value}
        result = await plugin.execute(params, None, mock_host)
        assert result.status == "error", f"Expected error for max_results={invalid_value}"
        assert result.error["code"] == "invalid_parameter"


async def test_invalid_operation_parameter(client, db, auth_headers):
    """Test that invalid operation parameter returns error."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    params = {"op": "invalid_operation"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "error"
    assert result.error["code"] == "invalid_parameter"
    assert "Unsupported op" in result.error["message"]


async def test_event_attributes_extraction(client, db, auth_headers):
    """Test that event attributes are correctly extracted and stored."""
    from plugins.shu_outlook_calendar.plugin import OutlookCalendarPlugin

    plugin = OutlookCalendarPlugin()
    mock_host = MockHost()

    events = [_create_mock_event(
        event_id="evt_detailed",
        subject="Detailed Meeting",
        location="Building 42 Room 101",
        attendees=["alice@example.com", "bob@example.com"],
        organizer_email="host@example.com",
        online_meeting_url="https://teams.microsoft.com/meet/123"
    )]
    mock_host.http.set_default_response(_create_mock_graph_response(events))

    params = {"op": "ingest", "kb_id": "test_kb_attrs"}
    result = await plugin.execute(params, None, mock_host)

    assert result.status == "success"
    assert len(mock_host.kb.ingested_texts) == 1

    ingested = mock_host.kb.ingested_texts[0]
    assert ingested["title"] == "Detailed Meeting"
    assert ingested["attributes"]["location"] == "Building 42 Room 101"
    assert "alice@example.com" in ingested["attributes"]["attendees"]
    assert "bob@example.com" in ingested["attributes"]["attendees"]
    assert ingested["attributes"]["organizer"] == "host@example.com"
    assert ingested["attributes"]["online_meeting_url"] == "https://teams.microsoft.com/meet/123"


# ============================================================================
# Test Suite Class
# ============================================================================

class OutlookCalendarIntegrationTestSuite(BaseIntegrationTestSuite):
    """Test suite for Outlook Calendar plugin integration tests."""

    def get_test_functions(self) -> List[Callable]:
        """Return all Outlook Calendar plugin test functions."""
        return [
            test_list_operation_default_parameters,
            test_list_operation_with_time_window,
            test_list_operation_auth_failure,
            test_ingest_operation_requires_kb_id,
            test_ingest_operation_full_sync,
            test_ingest_operation_delta_sync,
            test_ingest_operation_handles_cancelled_events,
            test_ingest_operation_reset_cursor,
            test_parameter_validation_since_hours,
            test_parameter_validation_max_results,
            test_invalid_operation_parameter,
            test_event_attributes_extraction,
        ]

    def get_suite_name(self) -> str:
        """Return the name of this test suite."""
        return "Outlook Calendar Plugin Integration Tests"

    def get_suite_description(self) -> str:
        """Return description of this test suite."""
        return "End-to-end integration tests for Outlook Calendar plugin operations (list, ingest) with delta sync"


if __name__ == "__main__":
    suite = OutlookCalendarIntegrationTestSuite()
    exit_code = suite.run()
    sys.exit(exit_code)
