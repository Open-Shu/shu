"""Demo Player Profiles Plugin Implementation.

This plugin simulates a comprehensive player management system,
returning synthesized player profiles for demonstration purposes.
"""

from __future__ import annotations
import asyncio
import random
from typing import Any, Dict, Optional


# Synthesized player profiles for demo purposes
DEMO_PLAYER_PROFILES = {
    # VIP Player 1: David Chen - High-roller, blackjack specialist
    "CUST-5678": {
        "customer_id": "CUST-5678",
        "portrait": "/david_chen.png",
        "name": "David Chen",
        "tier": "platinum",
        "member_since": "2022-03-15",
        "contact": {
            "email": "d.chen@example.com",
            "phone": "+1-555-0123",
            "preferred_contact": "text"
        },
        "hotel_preferences": {
            "room_type": "executive_suite",
            "floor": "high_floor",
            "view": "burj_khalifa_view",
            "bed": "king",
            "pillow": "firm",
            "temperature": 20,  # Celsius
            "minibar": "premium_stocked",
            "newspaper": "financial_times"
        },
        "transportation_preferences": {
            "type": "rental",
            "preferred_vehicle": "mercedes_s_class",
            "vehicle_color": "black",
            "special_requirements": ["spacious", "quiet", "wifi"],
            "driver_language": "english"
        },
        "preferences": {
            "beverage": "green_tea",
            "dining": [
                {"restaurant": "Jade Palace", "cuisine": "asian_fusion", "visits": 12},
                {"restaurant": "The Steakhouse", "cuisine": "steakhouse", "visits": 5}
            ],
            "entertainment": ["live_music", "no_shows"],
            "room_preference": "suite_with_view"
        },
        "dining_preferences": {
            "favorite_restaurant": "Jade Palace",
            "cuisine_preferences": ["asian_fusion", "modern_european", "seafood"],
            "favorite_table": "JP-VIP-02",
            "favorite_beverage": "green_tea",
            "dietary_restrictions": [],
            "meal_times": {
                "breakfast": "07:00",
                "dinner": "20:00"
            }
        },
        "service_preferences": {
            "tailor_interest": True,
            "spa_interest": False,
            "spa_preferences": [],
            "concierge_services": ["restaurant_reservations", "event_tickets"],
            "activities": ["golf", "yacht_charter"]
        },
        "companion_info": {
            "traveling_with": "spouse",
            "companion_name": "Lisa Chen",
            "companion_preferences": {
                "spa_interest": True,
                "shopping_interest": True,
                "dining_preferences": ["italian", "french"]
            }
        },
        "comp_history": [
            {
                "date": "2025-12-20",
                "type": "suite",
                "restaurant": "",
                "nights": 2,
                "guests": 0,
                "value": 3000.00,
                "reason": "holiday_visit",
                "show": "",
            },
            {
                "date": "2026-01-05",
                "type": "dinner",
                "restaurant": "Jade Palace",
                "guests": 2,
                "value": 450.00,
                "reason": "birthday",
                "show": "",
                "nights": 1,
            },
            {
                "date": "2026-01-10",
                "type": "show_tickets",
                "show": "Cirque Performance",
                "restaurant": "",
                "value": 600.00,
                "guests": 1,
                "reason": "anniversary",
                "nights": 1,
            }
        ],
        "financial": {
            "lifetime_value": 125000.00,
            "ytd_value": 15000.00,
            "last_30_days_value": 8000.00
        },
        "visit_history": {
            "total_visits": 47,
            "last_visit": "2026-01-10",
            "average_visit_duration_days": 4.5,
            "preferred_days": ["friday", "saturday"],
            "preferred_times": ["evening", "night"]
        },
        "special_dates": [
            {"type": "birthday", "date": "1978-06-15"},
            {"type": "anniversary", "date": "2005-09-22"}
        ],
        "notes": [
            {
                "date": "2025-12-20",
                "author": "Host Manager Lisa Wong",
                "note": "Prefers personal greeting, dislikes crowds."
            },
            {
                "date": "2026-01-05",
                "author": "Floor Manager Tom Rodriguez",
                "note": "Celebrating birthday with wife. Appreciated the complimentary champagne."
            },
            {
                "date": "2026-01-10",
                "author": "Spa Manager Fatima Al-Rashid",
                "note": "Guest was offered complimentary spa treatment during last visit. Politely declined, mentioning that he does not enjoy spa services. Prefers to spend time at gaming tables or dining. Note: Do not offer spa services to this guest in the future."
            },
            {
                "date": "2026-01-15",
                "author": "Executive Host Sarah Al-Mansouri",
                "note": "Guest mentioned interest in visiting hotel tailor for custom suits. Prefers quiet, professional service. Traveling with spouse who enjoys spa and shopping."
            }
        ]
    },

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
        error = {"message": message, "code": code}
        if details:
            error["details"] = details
        return cls(status="error", error=error)


class DemoPlayerProfilesPlugin:
    """Demo plugin simulating comprehensive player management system.
    
    This plugin returns pre-crafted synthesized player profiles for demonstration purposes,
    showcasing what Shu can accomplish when integrated with real player management systems.
    """
    
    name = "demo_player_profiles"
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
                    "enum": ["get", "list", "search", "get_by_players"],
                    "default": "list",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "get": "Get Player Profile",
                            "list": "List Player Profiles",
                            "search": "Search Players",
                            "get_by_players": "Get Multiple Players"
                        },
                        "enum_help": {
                            "get": "Retrieve a specific player profile by ID",
                            "list": "List all player profiles",
                            "search": "Search for players by name or criteria",
                            "get_by_players": "Retrieve multiple player profiles by IDs"
                        }
                    }
                },
                "customer_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Player ID for get operation"
                    }
                },
                "customer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-ui": {
                        "help": "Array of player IDs for get_by_players operation"
                    }
                },
                "query": {
                    "type": "string",
                    "x-ui": {
                        "help": "Search query for search operation"
                    }
                },
                "include_analytics": {
                    "type": "boolean",
                    "default": True,
                    "x-ui": {
                        "help": "Include analytics data in response"
                    }
                },
                "include_notes": {
                    "type": "boolean",
                    "default": True,
                    "x-ui": {
                        "help": "Include staff notes in response"
                    }
                },
                "include_questions": {
                    "type": "boolean",
                    "default": True,
                    "x-ui": {
                        "help": "Include pre-generated questions in response"
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
        # Define the comprehensive player profile schema
        player_profile_schema = {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Unique customer identifier"
                },
                "name": {
                    "type": "string",
                    "description": "Player full name"
                },
                "tier": {
                    "type": "string",
                    "enum": ["diamond", "platinum", "gold", "silver", "banned"],
                    "description": "Player tier level"
                },
                "member_since": {
                    "type": "string",
                    "format": "date",
                    "description": "Membership start date"
                },
                "ban_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date of ban (if applicable)"
                },
                "ban_reason": {
                    "type": "string",
                    "description": "Reason for ban (if applicable)"
                },
                "restriction_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date restriction was imposed (if applicable)"
                },
                "restriction_reason": {
                    "type": "string",
                    "description": "Reason for restriction (if applicable)"
                },
                "contact": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                        "preferred_contact": {"type": "string"}
                    },
                    "description": "Contact information"
                },
                "restriction_flags": {
                    "type": "object",
                    "properties": {
                        "banned": {
                            "type": "boolean",
                            "description": "Player is banned from property"
                        },
                        "self_excluded": {
                            "type": "boolean",
                            "description": "Player has self-excluded"
                        },
                        "alcohol_restricted": {
                            "type": "boolean",
                            "description": "Alcohol service is restricted"
                        },
                        "credit_suspended": {
                            "type": "boolean",
                            "description": "Credit line is suspended"
                        },
                        "watchlist": {
                            "type": "string",
                            "description": "Watchlist status (e.g., high_priority, standard_monitoring)"
                        }
                    },
                    "description": "Player restriction and monitoring flags"
                },
                "ban_details": {
                    "type": "object",
                    "description": "Detailed information about ban (if applicable)"
                },
                "restriction_details": {
                    "type": "object",
                    "description": "Detailed information about restrictions (if applicable)"
                },
                "preferences": {
                    "type": "object",
                    "properties": {
                        "games": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Game preferences with skill levels"
                        },
                        "table_preference": {
                            "type": "string",
                            "description": "Preferred table atmosphere"
                        },
                        "table_limits": {
                            "type": "object",
                            "description": "Preferred betting limits"
                        },
                        "beverage": {
                            "type": "string",
                            "description": "Preferred beverage"
                        },
                        "alternative_beverages": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Alternative beverage options (for restricted players)"
                        },
                        "dining": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Dining preferences and history"
                        },
                        "entertainment": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Entertainment preferences"
                        },
                        "room_preference": {
                            "type": "string",
                            "description": "Preferred room type"
                        }
                    },
                    "description": "Player preferences and favorites"
                },
                "comp_history": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Complimentary items and services history"
                },
                "financial": {
                    "type": "object",
                    "properties": {
                        "credit_line": {"type": "number"},
                        "credit_used": {"type": "number"},
                        "average_bet": {"type": "number"},
                        "average_session_buy_in": {"type": "number"},
                        "lifetime_value": {"type": "number"},
                        "ytd_value": {"type": "number"},
                        "last_30_days_value": {"type": "number"}
                    },
                    "description": "Financial information and metrics"
                },
                "visit_history": {
                    "type": "object",
                    "properties": {
                        "total_visits": {"type": "integer"},
                        "last_visit": {"type": "string", "format": "date"},
                        "average_visit_duration_hours": {"type": "number"},
                        "preferred_days": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "preferred_times": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "description": "Visit patterns and history"
                },
                "analytics": {
                    "type": "object",
                    "properties": {
                        "win_loss_ratio": {"type": "number"},
                        "volatility": {"type": "string"},
                        "churn_risk": {"type": "string"},
                        "upsell_potential": {"type": "string"},
                        "social_influence": {"type": "string"}
                    },
                    "description": "Player analytics and predictions"
                },
                "special_dates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Important dates (birthdays, anniversaries)"
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Staff notes and observations"
                },
                "security_notes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Security-related notes (for flagged players)"
                },
                "compliance_notes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Compliance and restriction notes"
                },
                "incident_history": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Incident history (for restricted players)"
                },
                "handling_instructions": {
                    "type": "object",
                    "description": "Staff handling instructions (for flagged/restricted players)"
                },
                "pre_generated_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Pre-generated questions about the player"
                }
            },
            "required": ["customer_id", "name", "tier"],
            "description": "Comprehensive player profile with preferences, history, and restrictions"
        }
        
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "profile": {
                    **player_profile_schema,
                    "description": "Single player profile"
                },
                "profiles": {
                    "type": "array",
                    "items": player_profile_schema,
                    "description": "Array of player profiles"
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
            ToolResult containing synthesized player profile data or error
        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        op = params.get("op", "list")
        include_analytics = params.get("include_analytics", True)
        include_notes = params.get("include_notes", True)
        include_questions = params.get("include_questions", True)
        
        def filter_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
            """Filter profile fields based on include parameters.
            
            Args:
                profile: Full player profile dictionary
                
            Returns:
                Filtered profile dictionary
            """
            filtered = profile.copy()
            
            if not include_analytics and "analytics" in filtered:
                del filtered["analytics"]
            
            if not include_notes and "notes" in filtered:
                del filtered["notes"]
            
            if not include_questions and "pre_generated_questions" in filtered:
                del filtered["pre_generated_questions"]
            
            return filtered
        
        if op == "get":
            customer_id = params.get("customer_id")
            if not customer_id:
                return ToolResult.err(
                    "player_id is required for get operation",
                    code="missing_parameter"
                )
            
            # Get player profile from synthesized data
            profile = DEMO_PLAYER_PROFILES.get(customer_id)
            if profile is None:
                return ToolResult.err(
                    f"Customer not found: {customer_id}",
                    code="customer_not_found"
                )
            
            filtered_profile = filter_profile(profile)
            
            return ToolResult.ok(
                data={"profile": filtered_profile},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "list":
            # Return all player profiles
            profiles = [
                filter_profile(profile)
                for profile in DEMO_PLAYER_PROFILES.values()
            ]
            
            return ToolResult.ok(
                data={"profiles": profiles},
                diagnostics=["Demo mode: using synthesized data"]
            )
            
        elif op == "search":
            query = params.get("query")
            if not query:
                return ToolResult.err(
                    "query is required for search operation",
                    code="missing_parameter"
                )
            
            # Simple search by name (case-insensitive)
            query_lower = query.lower()
            matching_profiles = [
                filter_profile(profile)
                for profile in DEMO_PLAYER_PROFILES.values()
                if query_lower in profile["name"].lower()
            ]
            
            return ToolResult.ok(
                data={"profiles": matching_profiles},
                diagnostics=["Demo mode: using synthesized data"]
            )

            
        else:
            return ToolResult.err(
                f"Unknown operation: {op}",
                code="invalid_operation"
            )
