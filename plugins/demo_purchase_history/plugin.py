"""Demo Purchase History Plugin Implementation.

This plugin simulates a beverage and food purchase tracking system,
returning synthesized purchase history data for demonstration purposes.
"""

from __future__ import annotations
import asyncio
import random
from typing import Any, Dict, Optional


# Synthesized purchase history data for demo purposes
# Data structure matches the format expected by demo_alcohol_restriction.yaml
DEMO_PURCHASE_HISTORY = {
    "PLAYER-7777": {
        "bar_lounge": {
            "purchases": [
                {
                    "timestamp": "2026-01-20T19:15:00Z",
                    "item": "Beer",
                    "quantity": 1,
                    "alcohol_content": 5.0,  # Percentage
                    "volume_oz": 12,          # Fluid ounces
                    "server": "Bartender Mike Johnson"
                },
                {
                    "timestamp": "2026-01-20T19:45:00Z",
                    "item": "Beer",
                    "quantity": 1,
                    "alcohol_content": 5.0,
                    "volume_oz": 12,
                    "server": "Bartender Mike Johnson"
                },
                {
                    "timestamp": "2026-01-20T20:15:00Z",
                    "item": "Whiskey Sour",
                    "quantity": 1,
                    "alcohol_content": 40.0,
                    "volume_oz": 2,
                    "server": "Bartender Sarah Lee"
                },
                {
                    "timestamp": "2026-01-20T20:30:00Z",
                    "item": "Beer",
                    "quantity": 1,
                    "alcohol_content": 5.0,
                    "volume_oz": 12,
                    "server": "Bartender Sarah Lee"
                }
            ]
        }
    }
}


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
        error_dict = {
            "message": message,
            "code": code
        }
        if details:
            error_dict["details"] = details
        return cls(status="error", error=error_dict)


class DemoPurchaseHistoryPlugin:
    """Demo plugin simulating purchase history tracking system."""
    
    name = "demo_purchase_history"
    version = "1.0.0"
    
    def get_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin parameters.
        
        Returns:
            JSON schema dictionary defining parameter structure
        """
        return {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["get"],
                    "default": "get",
                    "description": "Operation to perform"
                },
                "player_id": {
                    "type": "string",
                    "description": "Player ID to retrieve purchase history for",
                    "ui_hint": "Player identifier (e.g., PLAYER-7777)"
                },
                "location": {
                    "type": "string",
                    "description": "Location to filter purchases (e.g., 'bar_lounge')",
                    "ui_hint": "Optional location filter"
                },
                "time_window_minutes": {
                    "type": "integer",
                    "description": "Time window in minutes to filter purchases",
                    "default": 90,
                    "ui_hint": "Default: 90 minutes"
                }
            },
            "required": ["player_id"]
        }
    
    def get_output_schema(self) -> Optional[Dict[str, Any]]:
        """Return JSON schema for plugin output.
        
        Returns:
            JSON schema dictionary defining output structure
        """
        return {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "Player ID"
                },
                "location": {
                    "type": ["string", "null"],
                    "description": "Location filter applied (null if all locations)"
                },
                "time_window_minutes": {
                    "type": "integer",
                    "description": "Time window in minutes"
                },
                "purchases": {
                    "type": "array",
                    "description": "List of purchase records",
                    "items": {
                        "type": "object",
                        "properties": {
                            "timestamp": {
                                "type": "string",
                                "format": "date-time",
                                "description": "Purchase timestamp"
                            },
                            "item": {
                                "type": "string",
                                "description": "Item name"
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Quantity purchased"
                            },
                            "alcohol_content": {
                                "type": "number",
                                "description": "Alcohol content percentage"
                            },
                            "volume_oz": {
                                "type": "number",
                                "description": "Volume in fluid ounces"
                            },
                            "server": {
                                "type": "string",
                                "description": "Server name"
                            }
                        }
                    }
                },
                "alert_threshold": {
                    "type": "number",
                    "description": "BAC alert threshold"
                },
                "cutoff_threshold": {
                    "type": "number",
                    "description": "BAC cutoff threshold"
                }
            }
        }
    
    async def execute(
        self,
        params: Dict[str, Any],
        context: Any,
        host: Any
    ) -> ToolResult:
        """Execute the plugin operation.
        
        Args:
            params: Operation parameters
            context: Execution context
            host: Host capabilities
            
        Returns:
            ToolResult with purchase history data or error
        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        # Subtask 6.1: Parameter extraction and validation
        op = params.get("op", "get")
        player_id = params.get("player_id")
        location = params.get("location")
        time_window_minutes = params.get("time_window_minutes", 90)
        
        # Validate required parameters
        if not player_id:
            return ToolResult.err(
                message="player_id is required for get operation",
                code="missing_parameter"
            )
        
        # Validate operation
        if op != "get":
            return ToolResult.err(
                message=f"Unknown operation: {op}",
                code="invalid_operation"
            )
        
        # Subtask 6.2: Purchase data retrieval
        # Look up player in DEMO_PURCHASE_HISTORY
        if player_id not in DEMO_PURCHASE_HISTORY:
            return ToolResult.err(
                message=f"No purchase history found for player: {player_id}",
                code="player_not_found"
            )
        
        player_data = DEMO_PURCHASE_HISTORY[player_id]
        
        # Filter by location if provided
        if location:
            if location not in player_data:
                return ToolResult.err(
                    message=f"No purchases found at location: {location}",
                    code="location_not_found"
                )
            location_data = player_data[location]
            purchases = location_data["purchases"]
        else:
            # If no location specified, return all purchases across all locations
            purchases = []
            for loc_data in player_data.values():
                purchases.extend(loc_data["purchases"])
        
        # Construct response data
        response_data = {
            "player_id": player_id,
            "location": location,
            "time_window_minutes": time_window_minutes,
            "purchases": purchases,
            "alert_threshold": 0.06,
            "cutoff_threshold": 0.08
        }
        
        # Add demo mode diagnostics
        diagnostics = [
            "DEMO MODE: Using synthesized purchase history data",
            f"Player: {player_id}",
            f"Location: {location or 'all locations'}",
            f"Purchases returned: {len(purchases)}"
        ]
        
        return ToolResult.ok(data=response_data, diagnostics=diagnostics)
