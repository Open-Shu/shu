"""Demo Table Management Plugin Implementation.

This plugin simulates a table reservation and availability system,
returning synthesized table data for demonstration purposes.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import uuid


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


# Synthesized table inventory data
DEMO_TABLES = [
    # VIP Blackjack Tables
    {
        "table_id": "BJ-VIP-01",
        "game": "blackjack",
        "location": "vip_area",
        "min_bet": 100,
        "max_bet": 10000,
        "seats": 6,
        "available_seats": 6,
        "status": "available",
        "dealer": "Jennifer Liu",
        "dealer_rating": 4.8,
        "atmosphere": "quiet",
        "features": ["private", "high_limit", "preferred_dealer", "complimentary_beverages"]
    },
    {
        "table_id": "BJ-VIP-02",
        "game": "blackjack",
        "location": "vip_area",
        "min_bet": 100,
        "max_bet": 10000,
        "seats": 6,
        "available_seats": 4,
        "status": "active",
        "dealer": "Marcus Chen",
        "dealer_rating": 4.9,
        "atmosphere": "quiet",
        "features": ["private", "high_limit", "complimentary_beverages"]
    },
    {
        "table_id": "BJ-VIP-03",
        "game": "blackjack",
        "location": "vip_area",
        "min_bet": 100,
        "max_bet": 10000,
        "seats": 6,
        "available_seats": 6,
        "status": "available",
        "dealer": "Sarah Johnson",
        "dealer_rating": 4.7,
        "atmosphere": "quiet",
        "features": ["private", "high_limit", "preferred_dealer"]
    },
    
    # High-Limit Baccarat Tables
    {
        "table_id": "BAC-HL-01",
        "game": "baccarat",
        "location": "high_limit_room",
        "min_bet": 500,
        "max_bet": 50000,
        "seats": 8,
        "available_seats": 3,
        "status": "active",
        "dealer": "Marcus Wong",
        "dealer_rating": 4.9,
        "atmosphere": "exclusive",
        "features": ["private_room", "high_limit", "complimentary_beverages", "personal_host"]
    },
    {
        "table_id": "BAC-HL-02",
        "game": "baccarat",
        "location": "high_limit_room",
        "min_bet": 500,
        "max_bet": 50000,
        "seats": 8,
        "available_seats": 8,
        "status": "available",
        "dealer": "Elena Rodriguez",
        "dealer_rating": 4.8,
        "atmosphere": "exclusive",
        "features": ["private_room", "high_limit", "complimentary_beverages", "personal_host"]
    },
    {
        "table_id": "BAC-VIP-01",
        "game": "baccarat",
        "location": "vip_area",
        "min_bet": 200,
        "max_bet": 25000,
        "seats": 8,
        "available_seats": 5,
        "status": "active",
        "dealer": "David Kim",
        "dealer_rating": 4.7,
        "atmosphere": "upscale",
        "features": ["high_limit", "complimentary_beverages"]
    },
    
    # Standard Blackjack Tables
    {
        "table_id": "BJ-STD-01",
        "game": "blackjack",
        "location": "main_floor",
        "min_bet": 25,
        "max_bet": 1000,
        "seats": 7,
        "available_seats": 2,
        "status": "active",
        "dealer": "Tom Rodriguez",
        "dealer_rating": 4.5,
        "atmosphere": "lively",
        "features": ["standard"]
    },
    {
        "table_id": "BJ-STD-02",
        "game": "blackjack",
        "location": "main_floor",
        "min_bet": 25,
        "max_bet": 1000,
        "seats": 7,
        "available_seats": 0,
        "status": "full",
        "dealer": "Lisa Martinez",
        "dealer_rating": 4.6,
        "atmosphere": "lively",
        "features": ["standard"]
    },
    {
        "table_id": "BJ-STD-03",
        "game": "blackjack",
        "location": "main_floor",
        "min_bet": 15,
        "max_bet": 500,
        "seats": 7,
        "available_seats": 5,
        "status": "active",
        "dealer": "James Wilson",
        "dealer_rating": 4.4,
        "atmosphere": "casual",
        "features": ["standard", "beginner_friendly"]
    },
    
    # Poker Tables
    {
        "table_id": "PKR-VIP-01",
        "game": "poker",
        "location": "poker_room",
        "min_bet": 100,
        "max_bet": 5000,
        "seats": 9,
        "available_seats": 2,
        "status": "active",
        "dealer": "Robert Chang",
        "dealer_rating": 4.8,
        "atmosphere": "professional",
        "features": ["private_room", "tournament_style", "complimentary_beverages"]
    },
    {
        "table_id": "PKR-STD-01",
        "game": "poker",
        "location": "poker_room",
        "min_bet": 25,
        "max_bet": 1000,
        "seats": 9,
        "available_seats": 4,
        "status": "active",
        "dealer": "Amanda Foster",
        "dealer_rating": 4.6,
        "atmosphere": "social",
        "features": ["standard", "cash_game"]
    },
    {
        "table_id": "PKR-STD-02",
        "game": "poker",
        "location": "poker_room",
        "min_bet": 10,
        "max_bet": 500,
        "seats": 9,
        "available_seats": 6,
        "status": "active",
        "dealer": "Chris Anderson",
        "dealer_rating": 4.5,
        "atmosphere": "casual",
        "features": ["standard", "beginner_friendly", "cash_game"]
    },
    
    # Roulette Tables
    {
        "table_id": "RLT-VIP-01",
        "game": "roulette",
        "location": "vip_area",
        "min_bet": 50,
        "max_bet": 5000,
        "seats": 8,
        "available_seats": 6,
        "status": "available",
        "dealer": "Sophie Laurent",
        "dealer_rating": 4.7,
        "atmosphere": "elegant",
        "features": ["european_wheel", "high_limit", "complimentary_beverages"]
    },
    {
        "table_id": "RLT-STD-01",
        "game": "roulette",
        "location": "main_floor",
        "min_bet": 10,
        "max_bet": 1000,
        "seats": 10,
        "available_seats": 3,
        "status": "active",
        "dealer": "Michael Brown",
        "dealer_rating": 4.5,
        "atmosphere": "energetic",
        "features": ["american_wheel", "standard"]
    },
    
    # Craps Tables
    {
        "table_id": "CRP-STD-01",
        "game": "craps",
        "location": "main_floor",
        "min_bet": 15,
        "max_bet": 2000,
        "seats": 14,
        "available_seats": 8,
        "status": "active",
        "dealer": "Tony Russo",
        "dealer_rating": 4.6,
        "atmosphere": "exciting",
        "features": ["standard", "high_energy"]
    },
    {
        "table_id": "CRP-VIP-01",
        "game": "craps",
        "location": "vip_area",
        "min_bet": 100,
        "max_bet": 10000,
        "seats": 12,
        "available_seats": 10,
        "status": "available",
        "dealer": "Vincent Lee",
        "dealer_rating": 4.8,
        "atmosphere": "upscale",
        "features": ["high_limit", "complimentary_beverages", "private"]
    },
    
    # Slots Area (represented as "tables" for consistency)
    {
        "table_id": "SLOTS-VIP-01",
        "game": "slots",
        "location": "vip_slots_area",
        "min_bet": 5,
        "max_bet": 500,
        "seats": 1,
        "available_seats": 1,
        "status": "available",
        "dealer": "N/A",
        "dealer_rating": 0.0,
        "atmosphere": "luxurious",
        "features": ["high_limit", "private_area", "personal_attendant", "complimentary_beverages"]
    },
    {
        "table_id": "SLOTS-STD-AREA",
        "game": "slots",
        "location": "main_slots_floor",
        "min_bet": 0.25,
        "max_bet": 100,
        "seats": 200,
        "available_seats": 145,
        "status": "active",
        "dealer": "N/A",
        "dealer_rating": 0.0,
        "atmosphere": "lively",
        "features": ["standard", "variety", "progressive_jackpots"]
    }
]


class DemoTableManagementPlugin:
    """Demo plugin simulating table reservation and availability system.
    
    This plugin returns pre-crafted synthesized data for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real table management systems.
    """
    
    name = "demo_table_management"
    version = "1.0.0"
    
    # In-memory reservation storage (simple dict for demo purposes)
    _reservations: Dict[str, Dict[str, Any]] = {}

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
                    "enum": ["list_available", "reserve", "release", "get_status"],
                    "default": "list_available",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "list_available": "List Available Tables",
                            "reserve": "Reserve Table",
                            "release": "Release Reservation",
                            "get_status": "Get Table Status"
                        },
                        "enum_help": {
                            "list_available": "List available tables with optional filtering",
                            "reserve": "Reserve a table for a player",
                            "release": "Release an existing table reservation",
                            "get_status": "Get current status of a specific table"
                        }
                    }
                },
                "game_type": {
                    "type": "string",
                    "enum": ["blackjack", "baccarat", "poker", "roulette", "craps", "slots"],
                    "x-ui": {
                        "help": "Filter by game type (optional)"
                    }
                },
                "min_limit": {
                    "type": "number",
                    "minimum": 0,
                    "x-ui": {
                        "help": "Minimum bet limit filter (optional)"
                    }
                },
                "max_limit": {
                    "type": "number",
                    "minimum": 0,
                    "x-ui": {
                        "help": "Maximum bet limit filter (optional)"
                    }
                },
                "atmosphere": {
                    "type": "string",
                    "enum": ["quiet", "exclusive", "upscale", "lively", "casual", "professional", "social", "elegant", "energetic", "exciting", "luxurious"],
                    "x-ui": {
                        "help": "Filter by table atmosphere (optional)"
                    }
                },
                "table_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Specific table ID (for reserve, release, get_status operations)"
                    }
                },
                "player_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Player ID (for reserve operation)"
                    }
                },
                "duration_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 30,
                    "x-ui": {
                        "help": "Reservation duration in minutes (for reserve operation)"
                    }
                },
                "notes": {
                    "type": "string",
                    "x-ui": {
                        "help": "Reservation notes (for reserve operation)"
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
                "tables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table_id": {"type": "string"},
                            "game": {"type": "string"},
                            "location": {"type": "string"},
                            "min_bet": {"type": "number"},
                            "max_bet": {"type": "number"},
                            "seats": {"type": "integer"},
                            "available_seats": {"type": "integer"},
                            "status": {"type": "string"},
                            "dealer": {"type": "string"},
                            "dealer_rating": {"type": "number"},
                            "atmosphere": {"type": "string"},
                            "features": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                },
                "reservation": {
                    "type": "object",
                    "properties": {
                        "reservation_id": {"type": "string"},
                        "table_id": {"type": "string"},
                        "player_id": {"type": "string"},
                        "reserved_at": {"type": "string"},
                        "reserved_until": {"type": "string"},
                        "status": {"type": "string"},
                        "notes": {"type": "string"}
                    }
                }
            },
            "additionalProperties": False
        }

    def _filter_tables(
        self,
        game_type: Optional[str] = None,
        min_limit: Optional[float] = None,
        max_limit: Optional[float] = None,
        atmosphere: Optional[str] = None
    ) -> list:
        """Filter tables based on provided criteria.
        
        Args:
            game_type: Filter by game type (e.g., "blackjack", "baccarat")
            min_limit: Filter by minimum bet limit
            max_limit: Filter by maximum bet limit
            atmosphere: Filter by table atmosphere (e.g., "quiet", "exclusive")
            
        Returns:
            List of tables matching the filter criteria
        """
        filtered_tables = []
        
        for table in DEMO_TABLES:
            # Apply game type filter
            if game_type and table["game"] != game_type:
                continue
            
            # Apply min limit filter (table's min_bet should be <= requested min_limit)
            # if min_limit is not None and table["min_bet"] > min_limit:
            #     continue
            
            # # Apply max limit filter (table's max_bet should be >= requested max_limit)
            # if max_limit is not None and table["max_bet"] < max_limit:
            #     continue
            
            # # Apply atmosphere filter
            # if atmosphere and table["atmosphere"] != atmosphere:
            #     continue
            
            # Table matches all filters
            filtered_tables.append(table)
        
        return filtered_tables

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
            ToolResult containing synthesized table data or error
        """
        op = params.get("op", "list_available")
        
        if op == "list_available":
            # Extract filter parameters
            game_type = params.get("game_type")
            min_limit = params.get("min_limit")
            max_limit = params.get("max_limit")
            atmosphere = params.get("atmosphere")
            
            # Apply filters to get matching tables
            filtered_tables = self._filter_tables(
                game_type=game_type,
                min_limit=min_limit,
                max_limit=max_limit,
                atmosphere=atmosphere
            )
            
            # Build diagnostic message
            diagnostics = ["Demo mode: using synthesized data"]
            filter_info = []
            if game_type:
                filter_info.append(f"game_type={game_type}")
            if min_limit is not None:
                filter_info.append(f"min_limit={min_limit}")
            if max_limit is not None:
                filter_info.append(f"max_limit={max_limit}")
            if atmosphere:
                filter_info.append(f"atmosphere={atmosphere}")
            
            if filter_info:
                diagnostics.append(f"Filters applied: {', '.join(filter_info)}")
            diagnostics.append(f"Found {len(filtered_tables)} matching tables")
            
            return ToolResult.ok(
                data={"tables": filtered_tables},
                diagnostics=diagnostics
            )
            
        elif op == "reserve":
            # Extract required parameters
            table_id = params.get("table_id")
            player_id = params.get("player_id")
            duration_minutes = params.get("duration_minutes", 30)
            notes = params.get("notes", "")
            
            # Validate required parameters
            if not table_id:
                return ToolResult.err(
                    "table_id is required for reserve operation",
                    code="missing_parameter",
                    details={"parameter": "table_id"}
                )
            
            if not player_id:
                return ToolResult.err(
                    "player_id is required for reserve operation",
                    code="missing_parameter",
                    details={"parameter": "player_id"}
                )
            
            # Find the table
            table = None
            for t in DEMO_TABLES:
                if t["table_id"] == table_id:
                    table = t
                    break
            
            if not table:
                return ToolResult.err(
                    f"Table not found: {table_id}",
                    code="table_not_found",
                    details={"table_id": table_id}
                )
            
            # Check if table is available
            if table["status"] == "full":
                return ToolResult.err(
                    f"Table {table_id} is currently full",
                    code="table_full",
                    details={"table_id": table_id, "available_seats": 0}
                )
            
            # Generate reservation details
            reservation_id = f"RES-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
            reserved_at = datetime.now(timezone.utc)
            reserved_until = reserved_at + timedelta(minutes=duration_minutes)
            
            # Create reservation record
            reservation = {
                "reservation_id": reservation_id,
                "table_id": table_id,
                "player_id": player_id,
                "reserved_at": reserved_at.isoformat(),
                "reserved_until": reserved_until.isoformat(),
                "duration_minutes": duration_minutes,
                "status": "confirmed",
                "notes": notes,
                "table_details": {
                    "game": table["game"],
                    "location": table["location"],
                    "min_bet": table["min_bet"],
                    "max_bet": table["max_bet"],
                    "dealer": table["dealer"],
                    "atmosphere": table["atmosphere"],
                    "features": table["features"]
                }
            }
            
            # Store reservation in memory
            self._reservations[reservation_id] = reservation
            
            # Build diagnostics
            diagnostics = [
                "Demo mode: using synthesized data",
                f"Reserved table {table_id} for player {player_id}",
                f"Reservation duration: {duration_minutes} minutes",
                f"Valid until: {reserved_until.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            ]
            
            return ToolResult.ok(
                data={"reservation": reservation},
                diagnostics=diagnostics
            )
            
        elif op == "release":
            # Placeholder for release operation
            return ToolResult.err(
                "Release operation not yet implemented",
                code="not_implemented"
            )
            
        elif op == "get_status":
            # Placeholder for get_status operation
            return ToolResult.err(
                "Get status operation not yet implemented",
                code="not_implemented"
            )
            
        else:
            return ToolResult.err(
                f"Unknown operation: {op}",
                code="invalid_operation"
            )
