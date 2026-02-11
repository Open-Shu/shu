"""Demo Spa Service Plugin Implementation.

This plugin simulates a hotel spa service system,
returning synthesized spa availability data for demonstration purposes.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta, timezone
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


class DemoSpaServicePlugin:
    """Demo plugin simulating hotel spa service system.

    This plugin returns pre-crafted synthesized spa availability data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real hotel spa services.
    """

    name = "demo_spa_service"
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
                    "enum": ["check_availability", "get_schedule", "reserve"],
                    "default": "check_availability",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "check_availability": "Check Spa Availability",
                            "get_schedule": "Get Spa Schedule",
                            "reserve": "Reserve Spa Appointment",
                        },
                        "enum_help": {
                            "check_availability": "Check spa availability for appointments",
                            "get_schedule": "Retrieve spa's current schedule",
                            "reserve": "Reserve a spa appointment for a customer",
                        },
                    },
                },
                "customer_id": {"type": "string", "x-ui": {"help": "Customer ID for the reservation"}},
                "customer_name": {"type": "string", "x-ui": {"help": "Customer name for the reservation"}},
                "service_type": {
                    "type": "string",
                    "enum": [
                        "signature_massage",
                        "aromatherapy",
                        "hot_stone_therapy",
                        "couples_massage",
                        "facial_treatment",
                        "body_scrub",
                    ],
                    "x-ui": {"help": "Type of spa service to reserve"},
                },
                "appointment_time": {
                    "type": "string",
                    "format": "date-time",
                    "x-ui": {"help": "Appointment time (ISO 8601 format)"},
                },
                "party_size": {
                    "type": "integer",
                    "default": 1,
                    "x-ui": {"help": "Number of people for the appointment"},
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
        availability_schema = {
            "type": "object",
            "properties": {
                "spa_name": {"type": "string"},
                "available_slots": {"type": "array", "items": {"type": "string", "format": "date-time"}},
                "services": {"type": "array", "items": {"type": "string"}},
                "typical_appointment_duration_minutes": {"type": "integer"},
            },
        }

        reservation_schema = {
            "type": "object",
            "properties": {
                "reservation_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "customer_name": {"type": "string"},
                "spa_name": {"type": "string"},
                "service_type": {"type": "string"},
                "appointment_time": {"type": "string", "format": "date-time"},
                "party_size": {"type": "integer"},
                "duration_minutes": {"type": "integer"},
                "therapist_name": {"type": "string"},
                "room_number": {"type": "string"},
                "notes": {"type": "string"},
                "status": {"type": "string"},
                "created_at": {"type": "string", "format": "date-time"},
            },
        }

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "spa_availability": {**availability_schema, "description": "Spa availability information"},
                "reservation": {**reservation_schema, "description": "Spa reservation details"},
                "schedule": {"type": "object", "description": "Spa schedule information"},
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
            ToolResult containing synthesized spa availability data or error

        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.2, 0.8))

        op = params.get("op", "check_availability")

        if op == "reserve":
            # Validate required parameters
            customer_id = params.get("customer_id")
            if not customer_id:
                return ToolResult.err("customer_id is required for reserve operation", code="missing_parameter")

            service_type = params.get("service_type")
            if not service_type:
                return ToolResult.err("service_type is required for reserve operation", code="missing_parameter")

            # Get parameters with defaults
            customer_name = params.get("customer_name", "Guest")
            party_size = params.get("party_size", 1)
            notes = params.get("notes", "")

            # Calculate appointment time (now + 1 day if not specified)
            appointment_time_str = params.get("appointment_time")
            if appointment_time_str:
                appointment_time = datetime.fromisoformat(appointment_time_str.replace("Z", "+00:00"))
            else:
                # Default to tomorrow at 10 AM in Dubai time (UTC+4)
                now = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))
                next_day = now + timedelta(days=1)
                appointment_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            # Generate reservation ID
            reservation_id = f"SPA-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{random.randint(100, 999)}"

            # Determine duration based on service type
            duration_map = {
                "signature_massage": 90,
                "aromatherapy": 90,
                "hot_stone_therapy": 120,
                "couples_massage": 120,
                "facial_treatment": 60,
                "body_scrub": 75,
            }
            duration = duration_map.get(service_type, 90)

            # Assign therapist and room
            therapists = ["Amira Hassan", "Sofia Martinez", "Mei Lin", "Priya Sharma"]
            therapist = random.choice(therapists)
            room_number = f"SPA-{random.randint(1, 8):02d}"

            # Create reservation
            reservation = {
                "reservation_id": reservation_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "spa_name": "Azure Pearl Spa & Wellness",
                "service_type": service_type,
                "appointment_time": appointment_time.isoformat(),
                "party_size": party_size,
                "duration_minutes": duration,
                "therapist_name": therapist,
                "room_number": room_number,
                "notes": notes,
                "status": "confirmed",
                "created_at": datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4))).isoformat(),
            }

            return ToolResult.ok(data={"reservation": reservation}, diagnostics=["Demo mode: using synthesized data"])

        if op == "check_availability":
            # Return spa availability
            now = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))
            next_day = now + timedelta(days=1)
            base_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            spa_availability = {
                "spa_name": "Azure Pearl Spa & Wellness",
                "available_slots": [
                    base_time.isoformat(),
                    (base_time + timedelta(hours=2)).isoformat(),
                    (base_time + timedelta(hours=4)).isoformat(),
                    (base_time + timedelta(hours=6)).isoformat(),
                ],
                "services": [
                    "signature_massage",
                    "aromatherapy",
                    "hot_stone_therapy",
                    "couples_massage",
                    "facial_treatment",
                    "body_scrub",
                ],
                "typical_appointment_duration_minutes": 90,
            }

            return ToolResult.ok(
                data={"spa_availability": spa_availability}, diagnostics=["Demo mode: using synthesized data"]
            )

        if op == "get_schedule":
            # Return spa schedule
            now = datetime.now(UTC).replace(tzinfo=timezone(timedelta(hours=4)))
            next_day = now + timedelta(days=1)
            base_time = next_day.replace(hour=10, minute=0, second=0, microsecond=0)

            schedule = {
                "spa_name": "Azure Pearl Spa & Wellness",
                "date": next_day.date().isoformat(),
                "appointments": [
                    {"time": base_time.isoformat(), "customer": "Available", "service": None, "status": "available"},
                    {
                        "time": (base_time + timedelta(hours=2)).isoformat(),
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                    {
                        "time": (base_time + timedelta(hours=4)).isoformat(),
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                    {
                        "time": (base_time + timedelta(hours=6)).isoformat(),
                        "customer": "Available",
                        "service": None,
                        "status": "available",
                    },
                ],
                "services": [
                    "signature_massage",
                    "aromatherapy",
                    "hot_stone_therapy",
                    "couples_massage",
                    "facial_treatment",
                    "body_scrub",
                ],
            }

            return ToolResult.ok(data={"schedule": schedule}, diagnostics=["Demo mode: using synthesized data"])

        return ToolResult.err(f"Unknown operation: {op}", code="invalid_operation")
