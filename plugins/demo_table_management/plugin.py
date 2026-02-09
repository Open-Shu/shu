"""Demo Restaurant Management Plugin Implementation.

This plugin simulates a restaurant reservation and availability system,
returning synthesized restaurant data for demonstration purposes.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime
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


# Synthesized restaurant and table data for Dubai hotel
DEMO_RESTAURANTS = [
    # Jade Palace - Asian Fusion Restaurant
    {
        "restaurant_id": "JADE-PALACE",
        "name": "Jade Palace",
        "location": "Azure Pearl - Ground Floor",
        "cuisine": "asian_fusion",
        "dress_code": "smart_casual",
        "rating": 4.8,
        "price_range": "$$$$",
        "atmosphere": "elegant",
        "features": ["quiet", "private_dining", "window_views", "arabian_gulf_view"],
        "tables": [
            {
                "table_id": "JP-VIP-02",
                "table_number": "VIP-02",
                "location": "quiet_corner",
                "seats": 2,
                "view": "arabian_gulf",
                "status": "available",
                "features": ["quiet", "private", "window_view"],
            },
            {
                "table_id": "JP-VIP-01",
                "table_number": "VIP-01",
                "location": "private_room",
                "seats": 4,
                "view": "arabian_gulf",
                "status": "available",
                "features": ["private_room", "quiet", "window_view"],
            },
            {
                "table_id": "JP-STD-03",
                "table_number": "03",
                "location": "main_dining",
                "seats": 4,
                "view": "interior",
                "status": "available",
                "features": ["standard"],
            },
            {
                "table_id": "JP-STD-05",
                "table_number": "05",
                "location": "main_dining",
                "seats": 2,
                "view": "interior",
                "status": "reserved",
                "features": ["standard"],
            },
        ],
    },
    # Atmosphere - Modern European Restaurant
    {
        "restaurant_id": "ATMOSPHERE",
        "name": "Atmosphere Burj Khalifa",
        "location": "Burj Khalifa - 122nd Floor",
        "cuisine": "modern_european",
        "dress_code": "formal",
        "rating": 4.9,
        "price_range": "$$$$$",
        "atmosphere": "luxurious",
        "features": ["panoramic_views", "fine_dining", "private_dining", "burj_khalifa_view"],
        "tables": [
            {
                "table_id": "ATM-WIN-12",
                "table_number": "Window-12",
                "location": "window_section",
                "seats": 2,
                "view": "panoramic_city",
                "status": "available",
                "features": ["window_view", "romantic", "quiet"],
            },
            {
                "table_id": "ATM-WIN-08",
                "table_number": "Window-08",
                "location": "window_section",
                "seats": 4,
                "view": "panoramic_city",
                "status": "available",
                "features": ["window_view", "quiet"],
            },
            {
                "table_id": "ATM-PVT-01",
                "table_number": "Private-01",
                "location": "private_room",
                "seats": 8,
                "view": "panoramic_city",
                "status": "available",
                "features": ["private_room", "exclusive", "window_view"],
            },
        ],
    },
    # Al Mahara - Seafood Restaurant
    {
        "restaurant_id": "AL-MAHARA",
        "name": "Al Mahara",
        "location": "Azure Pearl - Lower Level",
        "cuisine": "seafood",
        "dress_code": "formal",
        "rating": 4.9,
        "price_range": "$$$$$",
        "atmosphere": "exclusive",
        "features": ["underwater_dining", "aquarium_view", "fine_dining", "romantic"],
        "tables": [
            {
                "table_id": "AM-AQ-01",
                "table_number": "Aquarium-01",
                "location": "aquarium_side",
                "seats": 2,
                "view": "aquarium",
                "status": "available",
                "features": ["aquarium_view", "romantic", "exclusive"],
            },
            {
                "table_id": "AM-AQ-05",
                "table_number": "Aquarium-05",
                "location": "aquarium_side",
                "seats": 4,
                "view": "aquarium",
                "status": "reserved",
                "features": ["aquarium_view", "exclusive"],
            },
        ],
    },
    # Pierchic - Mediterranean Restaurant
    {
        "restaurant_id": "PIERCHIC",
        "name": "Pierchic",
        "location": "Al Qasr - Pier",
        "cuisine": "mediterranean",
        "dress_code": "smart_casual",
        "rating": 4.7,
        "price_range": "$$$$",
        "atmosphere": "romantic",
        "features": ["overwater_dining", "arabian_gulf_view", "sunset_views", "seafood"],
        "tables": [
            {
                "table_id": "PC-PIER-03",
                "table_number": "Pier-03",
                "location": "pier_end",
                "seats": 2,
                "view": "arabian_gulf",
                "status": "available",
                "features": ["water_view", "romantic", "sunset_view"],
            },
            {
                "table_id": "PC-PIER-07",
                "table_number": "Pier-07",
                "location": "pier_middle",
                "seats": 4,
                "view": "arabian_gulf",
                "status": "available",
                "features": ["water_view", "sunset_view"],
            },
        ],
    },
    # Nathan Outlaw - British Seafood
    {
        "restaurant_id": "NATHAN-OUTLAW",
        "name": "Nathan Outlaw at Al Mahara",
        "location": "Azure Pearl - Lower Level",
        "cuisine": "british_seafood",
        "dress_code": "formal",
        "rating": 4.8,
        "price_range": "$$$$$",
        "atmosphere": "upscale",
        "features": ["michelin_star", "fine_dining", "seafood", "tasting_menu"],
        "tables": [
            {
                "table_id": "NO-VIP-01",
                "table_number": "VIP-01",
                "location": "private_section",
                "seats": 2,
                "view": "interior",
                "status": "available",
                "features": ["private", "quiet", "exclusive"],
            }
        ],
    },
]


class DemoRestaurantManagementPlugin:
    """Demo plugin simulating restaurant reservation and availability system.

    This plugin returns pre-crafted synthesized data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real restaurant management systems.
    """

    name = "demo_restaurant_management"
    version = "1.0.0"

    # In-memory reservation storage (simple dict for demo purposes)
    _reservations: dict[str, dict[str, Any]] = {}

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
                    "enum": ["list_available", "reserve", "cancel", "get_reservation"],
                    "default": "list_available",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "list_available": "List Available Tables",
                            "reserve": "Reserve Table",
                            "cancel": "Cancel Reservation",
                            "get_reservation": "Get Reservation Details",
                        },
                        "enum_help": {
                            "list_available": "List available restaurant tables with optional filtering",
                            "reserve": "Reserve a table for a customer",
                            "cancel": "Cancel an existing table reservation",
                            "get_reservation": "Get details of a specific reservation",
                        },
                    },
                },
                "restaurant_id": {"type": "string", "x-ui": {"help": "Filter by restaurant ID (optional)"}},
                "restaurant_name": {"type": "string", "x-ui": {"help": "Restaurant name (for reserve operation)"}},
                "cuisine": {
                    "type": "string",
                    "enum": ["asian_fusion", "modern_european", "seafood", "mediterranean", "british_seafood"],
                    "x-ui": {"help": "Filter by cuisine type (optional)"},
                },
                "atmosphere": {
                    "type": "string",
                    "enum": ["elegant", "luxurious", "exclusive", "romantic", "upscale"],
                    "x-ui": {"help": "Filter by restaurant atmosphere (optional)"},
                },
                "table_id": {
                    "type": "string",
                    "x-ui": {"help": "Specific table ID (for reserve, cancel, get_reservation operations)"},
                },
                "customer_id": {"type": "string", "x-ui": {"help": "Customer ID (for reserve operation)"}},
                "customer_name": {"type": "string", "x-ui": {"help": "Customer name (for reserve operation)"}},
                "party_size": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 2,
                    "x-ui": {"help": "Number of guests (for reserve operation)"},
                },
                "reservation_time": {
                    "type": "string",
                    "x-ui": {"help": "Reservation time in ISO format (for reserve operation)"},
                },
                "special_requests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-ui": {"help": "Special requests (for reserve operation)"},
                },
                "notes": {"type": "string", "x-ui": {"help": "Reservation notes (for reserve operation)"}},
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
                "restaurants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "restaurant_id": {"type": "string"},
                            "name": {"type": "string"},
                            "location": {"type": "string"},
                            "cuisine": {"type": "string"},
                            "dress_code": {"type": "string"},
                            "rating": {"type": "number"},
                            "price_range": {"type": "string"},
                            "atmosphere": {"type": "string"},
                            "features": {"type": "array", "items": {"type": "string"}},
                            "tables": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "table_id": {"type": "string"},
                                        "table_number": {"type": "string"},
                                        "location": {"type": "string"},
                                        "seats": {"type": "integer"},
                                        "view": {"type": "string"},
                                        "status": {"type": "string"},
                                        "features": {"type": "array", "items": {"type": "string"}},
                                    },
                                },
                            },
                        },
                    },
                },
                "reservation": {
                    "type": "object",
                    "properties": {
                        "reservation_id": {"type": "string"},
                        "time": {"type": "string"},
                        "customer_id": {"type": "string"},
                        "customer_name": {"type": "string"},
                        "party_size": {"type": "integer"},
                        "restaurant_id": {"type": "string"},
                        "restaurant_name": {"type": "string"},
                        "table_id": {"type": "string"},
                        "table_number": {"type": "string"},
                        "reservation_time": {"type": "string"},
                        "special_requests": {"type": "array", "items": {"type": "string"}},
                        "beverage_prepared": {"type": "string"},
                        "status": {"type": "string"},
                        "confirmation_code": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "additionalProperties": False,
        }

    def _filter_restaurants(
        self, restaurant_id: str | None = None, cuisine: str | None = None, atmosphere: str | None = None
    ) -> list:
        """Filter restaurants based on provided criteria.

        Args:
            restaurant_id: Filter by specific restaurant ID
            cuisine: Filter by cuisine type (e.g., "asian_fusion", "seafood")
            atmosphere: Filter by restaurant atmosphere (e.g., "elegant", "exclusive")

        Returns:
            List of restaurants matching the filter criteria

        """
        filtered_restaurants = []

        for restaurant in DEMO_RESTAURANTS:
            # Apply restaurant ID filter
            if restaurant_id and restaurant["restaurant_id"] != restaurant_id:
                continue

            # Apply cuisine filter
            if cuisine and restaurant["cuisine"] != cuisine:
                continue

            # Apply atmosphere filter
            if atmosphere and restaurant["atmosphere"] != atmosphere:
                continue

            # Restaurant matches all filters
            filtered_restaurants.append(restaurant)

        return filtered_restaurants

    def _find_table(self, table_id: str) -> tuple | None:
        """Find a table by ID across all restaurants.

        Args:
            table_id: The table ID to search for

        Returns:
            Tuple of (restaurant, table) if found, None otherwise

        """
        for restaurant in DEMO_RESTAURANTS:
            for table in restaurant["tables"]:
                if table["table_id"] == table_id:
                    return (restaurant, table)
        return None

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> ToolResult:
        """Execute the plugin operation.

        Args:
            params: Operation parameters from the schema
            context: Execution context with user information
            host: Host capabilities interface

        Returns:
            ToolResult containing synthesized restaurant data or error

        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.5, 2.0))

        op = params.get("op", "list_available")

        if op == "list_available":
            # Extract filter parameters
            restaurant_id = params.get("restaurant_id")
            cuisine = params.get("cuisine")
            atmosphere = params.get("atmosphere")

            # Apply filters to get matching restaurants
            filtered_restaurants = self._filter_restaurants(
                restaurant_id=restaurant_id, cuisine=cuisine, atmosphere=atmosphere
            )

            # Build diagnostic message
            diagnostics = ["Demo mode: using synthesized data"]
            filter_info = []
            if restaurant_id:
                filter_info.append(f"restaurant_id={restaurant_id}")
            if cuisine:
                filter_info.append(f"cuisine={cuisine}")
            if atmosphere:
                filter_info.append(f"atmosphere={atmosphere}")

            if filter_info:
                diagnostics.append(f"Filters applied: {', '.join(filter_info)}")
            diagnostics.append(f"Found {len(filtered_restaurants)} matching restaurants")

            return ToolResult.ok(data={"restaurants": filtered_restaurants}, diagnostics=diagnostics)

        if op == "reserve":
            # Extract required parameters
            table_id = params.get("table_id")
            customer_id = params.get("customer_id")
            customer_name = params.get("customer_name")
            party_size = params.get("party_size", 2)
            restaurant_name = params.get("restaurant_name", "")
            reservation_time = params.get("reservation_time")
            special_requests = params.get("special_requests", [])
            notes = params.get("notes", "")

            # Validate required parameters
            if not table_id:
                return ToolResult.err(
                    "table_id is required for reserve operation",
                    code="missing_parameter",
                    details={"parameter": "table_id"},
                )

            if not customer_id:
                return ToolResult.err(
                    "customer_id is required for reserve operation",
                    code="missing_parameter",
                    details={"parameter": "customer_id"},
                )

            # Find the table
            result = self._find_table(table_id)
            if not result:
                return ToolResult.err(
                    f"Table not found: {table_id}", code="table_not_found", details={"table_id": table_id}
                )

            restaurant, table = result

            # Check if table is available
            if table["status"] == "reserved":
                return ToolResult.err(
                    f"Table {table_id} is currently reserved", code="table_reserved", details={"table_id": table_id}
                )

            # Generate reservation details
            reservation_id = f"RES-{datetime.now(UTC).strftime('%Y%m%d')}-{str(uuid.uuid4())[:3].upper()}"

            # Determine beverage based on customer (David Chen gets green tea)
            beverage_prepared = "green_tea" if customer_id == "CUST-5678" else "champagne"

            # Create reservation record
            reservation = {
                "reservation_id": reservation_id,
                "time": "2026-01-20:30:00+04:00",
                "customer_id": customer_id,
                "customer_name": customer_name or "Guest",
                "party_size": party_size,
                "restaurant_id": restaurant["restaurant_id"],
                "restaurant_name": restaurant["name"],
                "table_id": table_id,
                "table_number": table["table_number"],
                "reservation_time": reservation_time or datetime.now(UTC).isoformat(),
                "special_requests": special_requests,
                "beverage_prepared": beverage_prepared,
                "status": "confirmed",
                "confirmation_code": f"{restaurant['restaurant_id'][:2]}-{reservation_id}",
                "notes": notes
                or f"Platinum customer - favorite table and {beverage_prepared.replace('_', ' ')} confirmed",
            }

            # Store reservation in memory
            self._reservations[reservation_id] = reservation

            # Build diagnostics
            diagnostics = [
                "Demo mode: using synthesized data",
                f"Reserved table {table_id} at {restaurant['name']} for {customer_name or customer_id}",
                f"Party size: {party_size}",
                f"Beverage prepared: {beverage_prepared.replace('_', ' ')}",
            ]

            return ToolResult.ok(data={"reservation": reservation}, diagnostics=diagnostics)

        if op == "cancel":
            # Placeholder for cancel operation
            return ToolResult.err("Cancel operation not yet implemented", code="not_implemented")

        if op == "get_reservation":
            # Placeholder for get_reservation operation
            return ToolResult.err("Get reservation operation not yet implemented", code="not_implemented")

        return ToolResult.err(f"Unknown operation: {op}", code="invalid_operation")
