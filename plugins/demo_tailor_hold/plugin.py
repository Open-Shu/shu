"""Demo Tailor Hold Plugin Implementation.

This plugin simulates a hotel tailor notification system,
returning synthesized notification data for demonstration purposes.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

# Notification counter for generating unique notification IDs
_notification_counter = 0


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


class DemoTailorHoldPlugin:
    """Demo plugin simulating hotel tailor notification system.

    This plugin returns pre-crafted synthesized notification data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real hotel tailor services.
    """

    name = "demo_tailor_hold"
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
                    "enum": ["add_hold", "check_availability", "get_schedule"],
                    "default": "add_hold",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "add_hold": "Add Hold for VIP Guest",
                            "check_availability": "Check Tailor Availability",
                            "get_schedule": "Get Tailor Schedule",
                        },
                        "enum_help": {
                            "add_hold": "Notify tailor to hold schedule for potential VIP appointment",
                            "check_availability": "Check tailor availability for appointments",
                            "get_schedule": "Retrieve tailor's current schedule",
                        },
                    },
                },
                "customer_id": {"type": "string", "x-ui": {"help": "Customer ID for the hold"}},
                "customer_name": {"type": "string", "x-ui": {"help": "Customer name for the hold"}},
                "customer_tier": {
                    "type": "string",
                    "enum": ["platinum", "diamond", "gold", "silver"],
                    "x-ui": {"help": "Customer tier status"},
                },
                "arrival_time": {
                    "type": "string",
                    "format": "date-time",
                    "x-ui": {"help": "Customer arrival time (ISO 8601 format)"},
                },
                "notes": {"type": "string", "x-ui": {"help": "Additional notes or special requirements"}},
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        """Return JSON schema for plugin output.

        Returns:
            JSON schema dictionary defining the structure of plugin output

        """
        notification_schema = {
            "type": "object",
            "properties": {
                "notification_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "customer_name": {"type": "string"},
                "customer_tier": {"type": "string"},
                "arrival_time": {"type": "string", "format": "date-time"},
                "tailor_name": {"type": "string"},
                "message": {"type": "string"},
                "notification_type": {"type": "string"},
                "obligation": {"type": "string"},
                "sent_at": {"type": "string", "format": "date-time"},
                "status": {"type": "string"},
            },
        }

        availability_schema = {
            "type": "object",
            "properties": {
                "tailor_name": {"type": "string"},
                "available_slots": {"type": "array", "items": {"type": "string", "format": "date-time"}},
                "services": {"type": "array", "items": {"type": "string"}},
                "typical_appointment_duration_minutes": {"type": "integer"},
            },
        }

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "notification": {**notification_schema, "description": "Hold notification details"},
                "tailor_availability": {**availability_schema, "description": "Tailor availability information"},
                "message": {"type": "string", "description": "Status or confirmation message"},
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
            ToolResult containing synthesized tailor notification data or error

        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.2, 0.8))

        op = params.get("op", "add_hold")

        if op == "add_hold":
            # Validate required parameters
            customer_id = params.get("customer_id")
            if not customer_id:
                return ToolResult.err("customer_id is required for add_hold operation", code="missing_parameter")

            # Get parameters with defaults
            customer_name = params.get("customer_name", "Guest")
            customer_tier = params.get("customer_tier", "platinum")
            notes = params.get("notes", "")

            # Calculate arrival time (now if not specified)
            arrival_time_str = params.get("arrival_time")
            if arrival_time_str:
                arrival_time = datetime.fromisoformat(arrival_time_str.replace("Z", "+00:00"))
            else:
                # Default to now in Dubai time (UTC+4)
                arrival_time = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))

            # Generate notification ID
            global _notification_counter
            _notification_counter += 1
            notification_id = f"TAILOR-{datetime.now(UTC).strftime('%Y%m%d')}-{_notification_counter:03d}"

            # Create notification message
            message = (
                f"VIP guest {customer_name} ({customer_tier.capitalize()}) arriving "
                f"{arrival_time.strftime('%B %d at %H:%M')}. Guest has expressed interest in "
                f"custom tailoring services. Please keep schedule flexible for potential appointment."
            )

            if notes:
                message += f" Additional notes: {notes}"

            # Create notification
            notification = {
                "notification_id": notification_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_tier": customer_tier,
                "proposed_time_slot": arrival_time.isoformat(),
                "tailor_name": "Master Tailor Giovanni Rossi",
                "message": message,
                "notification_type": "hold_confirmed",
                "obligation": "none",
                "status": "delivered",
            }

            # Generate availability slots (next day, 3 slots)
            next_day = arrival_time + timedelta(days=1)
            base_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            return ToolResult.ok(
                data={
                    "notification": notification,
                },
                diagnostics=["Demo mode: using synthesized data"],
            )

        if op == "check_availability":
            # Return tailor availability
            now = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))
            next_day = now + timedelta(days=1)
            base_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            tailor_availability = {
                "tailor_name": "Giovanni Rossi",
                "available_slots": [
                    base_time.isoformat(),
                    (base_time + timedelta(hours=4)).isoformat(),
                    (base_time + timedelta(hours=6)).isoformat(),
                ],
                "services": ["custom_suits", "alterations", "shirt_fitting", "formal_wear"],
                "typical_appointment_duration_minutes": 60,
            }

            return ToolResult.ok(
                data={"tailor_availability": tailor_availability}, diagnostics=["Demo mode: using synthesized data"]
            )

        if op == "get_schedule":
            # Return tailor schedule
            now = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))
            next_day = now + timedelta(days=1)
            base_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            schedule = {
                "tailor_name": "Giovanni Rossi",
                "date": next_day.date().isoformat(),
                "appointments": [
                    {
                        "time": "2026-01-22T10:00:00+04:00",
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                    {
                        "time": "2026-01-22T14:00:00+04:00",
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                    {
                        "time": "2026-01-22T16:00:00+04:00",
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                ],
                "services": ["custom_suits", "alterations", "shirt_fitting", "formal_wear"],
            }

            return ToolResult.ok(data={"schedule": schedule}, diagnostics=["Demo mode: using synthesized data"])

        return ToolResult.err(f"Unknown operation: {op}", code="invalid_operation")
