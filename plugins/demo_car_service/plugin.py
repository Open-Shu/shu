"""Demo Car Service Plugin Implementation.

This plugin simulates a luxury car service booking system,
returning synthesized booking data for demonstration purposes.
"""

from __future__ import annotations
import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional


# Synthesized vehicle data for demo purposes
AVAILABLE_VEHICLES = {
    "DXB-MERC-S580-001": {
        "vehicle_id": "DXB-MERC-S580-001",
        "make": "Mercedes-Benz",
        "model": "S 580",
        "year": 2025,
        "color": "black",
        "features": ["wifi", "privacy_partition", "champagne_bar", "massage_seats"],
        "capacity": {"passengers": 4, "luggage": 4},
        "available": True
    },
    "DXB-ROLLS-PHANTOM-001": {
        "vehicle_id": "DXB-ROLLS-PHANTOM-001",
        "make": "Rolls-Royce",
        "model": "Phantom",
        "year": 2025,
        "color": "black",
        "features": ["wifi", "privacy_partition", "champagne_bar", "starlight_ceiling"],
        "capacity": {"passengers": 4, "luggage": 5},
        "available": True
    }
}


# Booking counter for generating unique booking IDs
_booking_counter = 0


# Local ToolResult shim to avoid importing shu.* from plugins
class ToolResult:
    """Result wrapper for plugin execution."""
    
    def __init__(
        self,
        status: str,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
        diagnostics: Optional[list] = None
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
    def ok(cls, data: Optional[Dict[str, Any]] = None, diagnostics: Optional[list] = None):
        """Create a successful result.
        
        Args:
            data: Result data dictionary
            diagnostics: List of diagnostic messages
            
        Returns:
            ToolResult with success status
        """
        return cls(status="success", data=data, diagnostics=diagnostics)

    @classmethod
    def err(cls, message: str, code: str = "error", details: Optional[Dict[str, Any]] = None):
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


class DemoCarServicePlugin:
    """Demo plugin simulating luxury car service booking system.
    
    This plugin returns pre-crafted synthesized booking data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real car service providers.
    """
    
    name = "demo_car_service"
    version = "1.0.0"

    def get_schema(self) -> Optional[Dict[str, Any]]:
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
                    "enum": ["check_availability", "book", "cancel", "get_booking"],
                    "default": "check_availability",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "check_availability": "Check Vehicle Availability",
                            "book": "Book Car Service",
                            "cancel": "Cancel Booking",
                            "get_booking": "Get Booking Details"
                        },
                        "enum_help": {
                            "check_availability": "Check available luxury vehicles",
                            "book": "Book a luxury car service",
                            "cancel": "Cancel an existing booking",
                            "get_booking": "Retrieve booking details"
                        }
                    }
                },
                "customer_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Customer ID for booking"
                    }
                },
                "customer_name": {
                    "type": "string",
                    "x-ui": {
                        "help": "Customer name for booking"
                    }
                },
                "car_delivery_location": {
                    "type": "string",
                    "x-ui": {
                        "help": "Pickup location address"
                    }
                },
                "dropoff_location": {
                    "type": "string",
                    "x-ui": {
                        "help": "Drop-off location address"
                    }
                },
                "passengers": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 1,
                    "x-ui": {
                        "help": "Number of passengers"
                    }
                },
                "car_type": {
                    "type": "string",
                    "enum": ["mercedes_s_class", "rolls_royce_phantom", "any"],
                    "default": "any",
                    "x-ui": {
                        "help": "Preferred vehicle type"
                    }
                },
                "car_delivery_time": {
                    "type": "string",
                    "format": "date-time",
                    "x-ui": {
                        "help": "Requested pickup time (ISO 8601 format)"
                    }
                },
                "booking_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Booking ID for cancel or get_booking operations"
                    }
                },
                "notes": {
                    "type": "string",
                    "x-ui": {
                        "help": "Special instructions or notes"
                    }
                }
            },
            "required": [],
            "additionalProperties": False
        }

    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin output.
        
        Returns:
            JSON schema dictionary defining the structure of plugin output
        """
        vehicle_schema = {
            "type": "object",
            "properties": {
                "vehicle_id": {"type": "string"},
                "make": {"type": "string"},
                "model": {"type": "string"},
                "year": {"type": "integer"},
                "color": {"type": "string"},
                "features": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "capacity": {
                    "type": "object",
                    "properties": {
                        "passengers": {"type": "integer"},
                        "luggage": {"type": "integer"}
                    }
                },
                "available": {"type": "boolean"}
            }
        }
        
        booking_schema = {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "customer_name": {"type": "string"},
                "vehicle_id": {"type": "string"},
                "vehicle": {"type": "string"},
                "car_delivery_location": {"type": "string"},
                "car_delivery_time": {"type": "string", "format": "date-time"},
                "destination": {"type": "string"},
                "estimated_duration_minutes": {"type": "integer"},
                "special_instructions": {"type": "string"},
                "status": {"type": "string"},
                "confirmation_code": {"type": "string"}
            }
        }
        
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "available_vehicles": {
                    "type": "array",
                    "items": vehicle_schema,
                    "description": "List of available vehicles"
                },
                "booking": {
                    **booking_schema,
                    "description": "Booking confirmation details"
                },
                "message": {
                    "type": "string",
                    "description": "Status or confirmation message"
                }
            },
            "additionalProperties": False
        }

    async def execute(
        self,
        params: Dict[str, Any],
        context: Any,
        host: Any
    ) -> ToolResult:
        """Execute the plugin operation.
        
        Args:
            params: Operation parameters from the schema
            context: Execution context with user information
            host: Host capabilities interface
            
        Returns:
            ToolResult containing synthesized car service data or error
        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.3, 1.0))
        
        op = params.get("op", "check_availability")
        
        if op == "check_availability":
            # Return available vehicles
            return ToolResult.ok(
                data={
                    "available_vehicles": list(AVAILABLE_VEHICLES.values())
                },
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "book":
            # Validate required parameters
            customer_id = params.get("customer_id")
            if not customer_id:
                return ToolResult.err(
                    "customer_id is required for book operation",
                    code="missing_parameter"
                )
            
            car_delivery_location = params.get("car_delivery_location")
            if not car_delivery_location:
                return ToolResult.err(
                    "car_delivery_location is required for book operation",
                    code="missing_parameter"
                )
            
            dropoff_location = params.get("dropoff_location")
            if not dropoff_location:
                return ToolResult.err(
                    "dropoff_location is required for book operation",
                    code="missing_parameter"
                )
            
            # Get parameters
            customer_name = params.get("customer_name", "Guest")
            passengers = params.get("passengers", 1)
            car_type = params.get("car_type", "any")
            notes = params.get("notes", "")
            
            # Select vehicle based on preference
            if car_type == "rolls_royce_phantom":
                vehicle = AVAILABLE_VEHICLES["DXB-ROLLS-PHANTOM-001"]
            else:
                # Default to Mercedes S-Class (or any available)
                vehicle = AVAILABLE_VEHICLES["DXB-MERC-S580-001"]
            
            # Generate booking ID
            global _booking_counter
            _booking_counter += 1
            booking_id = f"CAR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{_booking_counter:03d}"
            
            # Calculate pickup time (15 minutes from now if not specified)
            car_delivery_time_str = params.get("car_delivery_time")
            if car_delivery_time_str:
                car_delivery_time = datetime.fromisoformat(car_delivery_time_str.replace('Z', '+00:00'))
            else:
                # Default to 15 minutes from now in Dubai time (UTC+4)
                car_delivery_time = datetime.now(timezone.utc) + timedelta(minutes=15)
                car_delivery_time = car_delivery_time.replace(tzinfo=timezone(timedelta(hours=4)))
            
            # Add special instructions for David Chen (CUST-5678) based on incident history
            special_instructions = notes
            if customer_id == "CUST-5678":
                if not special_instructions:
                    special_instructions = "Platinum guest with companion. Previous complaint about vehicle size - S-Class minimum required."
                else:
                    special_instructions += " Platinum guest with companion. Previous complaint about vehicle size - S-Class minimum required."
            
            # Create booking
            booking = {
                "booking_id": booking_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "vehicle_id": vehicle["vehicle_id"],
                "vehicle": f"{vehicle['make']} {vehicle['model']}",
                "car_delivery_location": car_delivery_location,
                "car_delivery_time": car_delivery_time.isoformat(),
                "destination": dropoff_location,
                "estimated_duration_minutes": 25,
                "special_instructions": special_instructions,
                "status": "confirmed",
                "confirmation_code": f"DXB-{booking_id}"
            }
            
            return ToolResult.ok(
                data={"booking": booking},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "cancel":
            booking_id = params.get("booking_id")
            if not booking_id:
                return ToolResult.err(
                    "booking_id is required for cancel operation",
                    code="missing_parameter"
                )
            
            return ToolResult.ok(
                data={
                    "message": f"Booking {booking_id} has been cancelled",
                    "booking_id": booking_id,
                    "status": "cancelled"
                },
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "get_booking":
            booking_id = params.get("booking_id")
            if not booking_id:
                return ToolResult.err(
                    "booking_id is required for get_booking operation",
                    code="missing_parameter"
                )
            
            # Return a sample booking (in real system, would look up by ID)
            booking = {
                "booking_id": booking_id,
                "customer_id": "CUST-5678",
                "customer_name": "David Chen",
                "vehicle_id": "DXB-MERC-S580-001",
                "vehicle": "Mercedes-Benz S 580",
                "car_delivery_location": "Dubai International Airport - Terminal 3 - VIP Arrivals",
                "car_delivery_time": datetime.now(timezone(timedelta(hours=4))).isoformat(),
                "destination": "Azure Pearl Hotel",
                "estimated_duration_minutes": 25,
                "special_instructions": "Platinum guest with companion. Previous complaint about vehicle size - S-Class minimum required.",
                "status": "confirmed",
                "confirmation_code": f"DXB-{booking_id}"
            }
            
            return ToolResult.ok(
                data={"booking": booking},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        else:
            return ToolResult.err(
                f"Unknown operation: {op}",
                code="invalid_operation"
            )
