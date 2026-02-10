"""Demo Facial Recognition Plugin Implementation.

This plugin simulates a facial recognition system at Dubai International Airport,
returning synthesized recognition events for demonstration purposes.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any


# Local ToolResult shim to avoid importing shu.* from plugins
class ToolResult:
    """Result wrapper for plugin execution."""

    def __init__(
        self,
        status: str,
        data: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        diagnostics: list | None = None,
    ):
        """Initialize ToolResult.

        Args:
            status: Execution status ("success" or "error")
            data: Result data dictionary
            error: Error information dictionary
            diagnostics: List of diagnostic messages

        """
        self.status = status
        self.data = data or {}
        self.error = error
        self.diagnostics = diagnostics or []

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None, diagnostics: list | None = None):
        """Create a successful result.

        Args:
            data: Result data dictionary
            diagnostics: List of diagnostic messages

        Returns:
            ToolResult with success status

        """
        return cls(status="success", data=data, diagnostics=diagnostics)

    @classmethod
    def err(cls, message: str, code: str = "error", details: dict[str, Any] | None = None):
        """Create an error result.

        Args:
            message: Error message
            code: Error code
            details: Additional error details

        Returns:
            ToolResult with error status

        """
        error = {"message": message, "code": code}
        if details:
            error["details"] = details
        return cls(status="error", error=error)


# Synthesized recognition events for demo purposes
DEMO_RECOGNITION_EVENTS = [
    # VIP Guest: David Chen - Platinum tier guest arriving in evening with companion
    {
        "event_id": "DXB-20260121-193000-001",
        "timestamp": "2026-01-21T19:30:00+04:00",  # Dubai time (UTC+4)
        "customer_id": "CUST-5678",
        "name": "David Chen",
        "confidence": 0.97,
        "location": "terminal_3_gate_b12",
        "aircraft": "EK001",
        "companion_count": 1,
        "time_of_day": "evening",
        "image_ref": "dxb_recognition_20260121_193000.jpg",
    }
]


class DemoFacialRecognitionPlugin:
    """Demo plugin simulating facial recognition at Dubai International Airport.

    This plugin returns pre-crafted synthesized data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real facial recognition systems.
    """

    name = "demo_facial_recognition"
    version = "1.0.0"

    def get_schema(self) -> dict[str, Any] | None:
        """Return JSON schema for plugin parameters.

        Returns:
            JSON schema dictionary defining valid plugin parameters

        """
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["list", "get_event"],
                    "default": "list",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {"list": "List Recognition Events", "get_event": "Get Specific Event"},
                        "enum_help": {
                            "list": "List facial recognition events with optional filtering",
                            "get_event": "Retrieve a specific recognition event by ID",
                        },
                    },
                },
                "filter": {
                    "type": "string",
                    "enum": ["vip", "flagged", "other", "all"],
                    "default": "all",
                    "x-ui": {
                        "help": "Filter recognition events by player category",
                        "enum_labels": {
                            "vip": "VIP Players",
                            "flagged": "Flagged Players",
                            "other": "Other Players",
                            "all": "All Players",
                        },
                    },
                },
                "event_id": {"type": "string", "x-ui": {"help": "Event ID for get_event operation"}},
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        """Return JSON schema for plugin output.

        Returns:
            JSON schema dictionary defining the structure of plugin output

        """
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string"},
                            "timestamp": {"type": "string"},
                            "customer_id": {"type": "string"},
                            "name": {"type": "string"},
                            "confidence": {"type": "number"},
                            "location": {"type": "string"},
                            "camera_id": {"type": "string"},
                            "image_ref": {"type": "string"},
                            "entry_type": {"type": "string"},
                            "companion_count": {"type": "integer"},
                            "category": {"type": "string"},
                        },
                    },
                },
                "event": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "customer_id": {"type": "string"},
                        "name": {"type": "string"},
                        "confidence": {"type": "number"},
                        "location": {"type": "string"},
                        "camera_id": {"type": "string"},
                        "image_ref": {"type": "string"},
                        "entry_type": {"type": "string"},
                        "companion_count": {"type": "integer"},
                        "category": {"type": "string"},
                    },
                },
            },
            "additionalProperties": False,
        }

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> ToolResult:
        """Execute the plugin operation.

        Args:
            params: Operation parameters from the schema
            context: Execution context with user information
            host: Host capabilities interface

        Returns:
            ToolResult containing synthesized recognition data or error

        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.5, 2.0))

        op = params.get("op", "list")

        if op == "get_event":
            event_id = params.get("event_id")
            if not event_id:
                return ToolResult.err("event_id is required for get_event operation", code="missing_parameter")

            # Find the event by ID
            event = next((e for e in DEMO_RECOGNITION_EVENTS if e["event_id"] == event_id), None)

            if event is None:
                return ToolResult.err(f"Event not found: {event_id}", code="event_not_found")

            return ToolResult.ok(data={"event": event}, diagnostics=["Demo mode: using synthesized data"])

        return ToolResult.err(f"Unknown operation: {op}", code="invalid_operation")
