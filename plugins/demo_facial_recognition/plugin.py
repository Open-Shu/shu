"""Demo Facial Recognition Plugin Implementation.

This plugin simulates a facial recognition system at casino entry points,
returning synthesized recognition events for demonstration purposes.
"""

from __future__ import annotations
from typing import Any, Dict, Optional


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


# Synthesized recognition events for demo purposes
DEMO_RECOGNITION_EVENTS = [
    # VIP Player 1: David Chen - High-roller, blackjack specialist
    {
        "event_id": "EVT-20260116-201500-001",
        "timestamp": "2026-01-16T20:15:00Z",
        "person_id": "PLAYER-5678",
        "name": "David Chen",
        "confidence": 0.96,
        "location": "main_entrance",
        "camera_id": "CAM-ENTRANCE-01",
        "image_ref": "img_20260116_201500_001.jpg",
        "entry_type": "vip_entrance",
        "companion_count": 0,
        "category": "vip"
    },
    # VIP Player 2: Sarah Martinez - Poker tournament regular
    {
        "event_id": "EVT-20260116-201530-002",
        "timestamp": "2026-01-16T20:15:30Z",
        "person_id": "PLAYER-8901",
        "name": "Sarah Martinez",
        "confidence": 0.94,
        "location": "vip_entrance",
        "camera_id": "CAM-VIP-01",
        "image_ref": "img_20260116_201530_002.jpg",
        "entry_type": "vip_entrance",
        "companion_count": 1,
        "category": "vip"
    },
    # VIP Player 3: James Wilson - Slots and entertainment enthusiast
    {
        "event_id": "EVT-20260116-201600-003",
        "timestamp": "2026-01-16T20:16:00Z",
        "person_id": "PLAYER-2345",
        "name": "James Wilson",
        "confidence": 0.98,
        "location": "main_entrance",
        "camera_id": "CAM-ENTRANCE-02",
        "image_ref": "img_20260116_201600_003.jpg",
        "entry_type": "main_entrance",
        "companion_count": 3,
        "category": "vip"
    },
    # VIP Player 4: Elena Volkov - Baccarat high-stakes player
    {
        "event_id": "EVT-20260116-201645-004",
        "timestamp": "2026-01-16T20:16:45Z",
        "person_id": "PLAYER-6789",
        "name": "Elena Volkov",
        "confidence": 0.95,
        "location": "vip_entrance",
        "camera_id": "CAM-VIP-01",
        "image_ref": "img_20260116_201645_004.jpg",
        "entry_type": "vip_entrance",
        "companion_count": 0,
        "category": "vip"
    },
    # VIP Player 5: Michael Park - New VIP, rapid ascent
    {
        "event_id": "EVT-20260116-201700-005",
        "timestamp": "2026-01-16T20:17:00Z",
        "person_id": "PLAYER-3456",
        "name": "Michael Park",
        "confidence": 0.92,
        "location": "main_entrance",
        "camera_id": "CAM-ENTRANCE-01",
        "image_ref": "img_20260116_201700_005.jpg",
        "entry_type": "main_entrance",
        "companion_count": 2,
        "category": "vip"
    },
    # Flagged Player 1: High-risk banned player
    {
        "event_id": "EVT-20260116-201730-006",
        "timestamp": "2026-01-16T20:17:30Z",
        "person_id": "PLAYER-9999",
        "name": "Robert Blackwell",
        "confidence": 0.97,
        "location": "main_entrance",
        "camera_id": "CAM-ENTRANCE-02",
        "image_ref": "img_20260116_201730_006.jpg",
        "entry_type": "main_entrance",
        "companion_count": 0,
        "category": "flagged"
    },
    # Flagged Player 2: Alcohol-restricted player
    {
        "event_id": "EVT-20260116-201800-007",
        "timestamp": "2026-01-16T20:18:00Z",
        "person_id": "PLAYER-7777",
        "name": "Thomas Anderson",
        "confidence": 0.93,
        "location": "main_entrance",
        "camera_id": "CAM-ENTRANCE-01",
        "image_ref": "img_20260116_201800_007.jpg",
        "entry_type": "main_entrance",
        "companion_count": 1,
        "category": "flagged"
    }
]


class DemoFacialRecognitionPlugin:
    """Demo plugin simulating facial recognition at casino entry points.
    
    This plugin returns pre-crafted synthesized data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real facial recognition systems.
    """
    
    name = "demo_facial_recognition"
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
                    "enum": ["list", "get_event"],
                    "default": "list",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "list": "List Recognition Events",
                            "get_event": "Get Specific Event"
                        },
                        "enum_help": {
                            "list": "List facial recognition events with optional filtering",
                            "get_event": "Retrieve a specific recognition event by ID"
                        }
                    }
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
                            "all": "All Players"
                        }
                    }
                },
                "event_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Event ID for get_event operation"
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
                            "person_id": {"type": "string"},
                            "name": {"type": "string"},
                            "confidence": {"type": "number"},
                            "location": {"type": "string"},
                            "camera_id": {"type": "string"},
                            "image_ref": {"type": "string"},
                            "entry_type": {"type": "string"},
                            "companion_count": {"type": "integer"},
                            "category": {"type": "string"}
                        }
                    }
                },
                "event": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "person_id": {"type": "string"},
                        "name": {"type": "string"},
                        "confidence": {"type": "number"},
                        "location": {"type": "string"},
                        "camera_id": {"type": "string"},
                        "image_ref": {"type": "string"},
                        "entry_type": {"type": "string"},
                        "companion_count": {"type": "integer"},
                        "category": {"type": "string"}
                    }
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
            ToolResult containing synthesized recognition data or error
        """
        op = params.get("op", "list")
        
        if op == "list":
            # Get filter parameter
            filter_category = params.get("filter", "all")
            
            # Filter events based on category
            if filter_category == "all":
                filtered_events = DEMO_RECOGNITION_EVENTS
            else:
                filtered_events = [
                    event for event in DEMO_RECOGNITION_EVENTS
                    if event["category"] == filter_category
                ]
            
            return ToolResult.ok(
                data={"events": filtered_events},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "get_event":
            event_id = params.get("event_id")
            if not event_id:
                return ToolResult.err(
                    "event_id is required for get_event operation",
                    code="missing_parameter"
                )
            
            # Find the event by ID
            event = next(
                (e for e in DEMO_RECOGNITION_EVENTS if e["event_id"] == event_id),
                None
            )
            
            if event is None:
                return ToolResult.err(
                    f"Event not found: {event_id}",
                    code="event_not_found"
                )
            
            return ToolResult.ok(
                data={"event": event},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        else:
            return ToolResult.err(
                f"Unknown operation: {op}",
                code="invalid_operation"
            )
