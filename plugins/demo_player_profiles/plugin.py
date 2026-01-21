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
    "PLAYER-5678": {
        "player_id": "PLAYER-5678",
        "portrait": "/david_chen.png",
        "name": "David Chen",
        "tier": "platinum",
        "member_since": "2022-03-15",
        "contact": {
            "email": "d.chen@example.com",
            "phone": "+1-555-0123",
            "preferred_contact": "text"
        },
        "preferences": {
            "games": [
                {"game": "blackjack", "skill_level": "expert", "preference_score": 10},
                {"game": "baccarat", "skill_level": "intermediate", "preference_score": 7},
                {"game": "poker", "skill_level": "advanced", "preference_score": 5}
            ],
            "table_preference": "quiet_tables",
            "table_limits": {"min": 100, "max": 5000},
            "beverage": "green_tea",
            "dining": [
                {"restaurant": "Jade Palace", "cuisine": "asian_fusion", "visits": 12},
                {"restaurant": "The Steakhouse", "cuisine": "steakhouse", "visits": 5}
            ],
            "entertainment": ["live_music", "no_shows"],
            "room_preference": "suite_with_view"
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
            "credit_line": 50000.00,
            "credit_used": 0.00,
            "average_bet": 500.00,
            "average_session_buy_in": 10000.00,
            "lifetime_value": 125000.00,
            "ytd_value": 15000.00,
            "last_30_days_value": 8000.00
        },
        "visit_history": {
            "total_visits": 47,
            "last_visit": "2026-01-10",
            "average_visit_duration_hours": 4.5,
            "preferred_days": ["friday", "saturday"],
            "preferred_times": ["evening", "night"]
        },
        "analytics": {
            "win_loss_ratio": 0.45,
            "volatility": "medium",
            "churn_risk": "low",
            "upsell_potential": "high",
            "social_influence": "medium"
        },
        "special_dates": [
            {"type": "birthday", "date": "1978-06-15"},
            {"type": "anniversary", "date": "2005-09-22"}
        ],
        "notes": [
            {
                "date": "2025-12-20",
                "author": "Host Manager Lisa Wong",
                "note": "Prefers personal greeting, dislikes crowds. Mentioned interest in private blackjack tables."
            },
            {
                "date": "2026-01-05",
                "author": "Floor Manager Tom Rodriguez",
                "note": "Celebrating birthday with wife. Appreciated the complimentary champagne."
            }
        ],
        "pre_generated_questions": [
            "What comps has David Chen received in the last 3 months?",
            "What are David Chen's preferred games and table limits?",
            "When was David Chen's last visit and what was his play pattern?",
            "What dining preferences does David Chen have?",
            "Are there any special occasions coming up for David Chen?",
            "What is David Chen's lifetime value and current tier status?"
        ]
    },
    
    # VIP Player 5: Ahmed Al Mansoori - New VIP, rapid ascent
    "PLAYER-3456": {
        "player_id": "PLAYER-3456",
        "portrait": "/ahmed-al-mansoori.png",
        "name": "Ahmed Al Mansoori",
        "tier": "gold",
        "member_since": "2025-09-01",
        "contact": {
            "email": "a.mansoori@example.com",
            "phone": "+1-555-0567",
            "preferred_contact": "email"
        },
        "preferences": {
            "games": [
                {"game": "blackjack", "skill_level": "intermediate", "preference_score": 8},
                {"game": "poker", "skill_level": "intermediate", "preference_score": 7},
                {"game": "craps", "skill_level": "beginner", "preference_score": 6},
                {"game": "slots", "skill_level": "casual", "preference_score": 5}
            ],
            "table_preference": "social_atmosphere",
            "table_limits": {"min": 100, "max": 2500},
            "beverage": "craft_cocktails",
            "dining": [
                {"restaurant": "Fusion Kitchen", "cuisine": "modern_american", "visits": 8},
                {"restaurant": "Sushi Bar", "cuisine": "japanese", "visits": 5}
            ],
            "entertainment": ["nightclub", "pool_parties", "live_music"],
            "room_preference": "modern_suite"
        },
        "comp_history": [
            {
                "date": "2025-11-10",
                "type": "room_upgrade",
                "nights": 2,
                "value": 800.00,
                "reason": "rapid_tier_advancement"
            },
            {
                "date": "2025-12-20",
                "type": "nightclub_vip",
                "value": 1500.00,
                "reason": "gold_tier_benefit"
            },
            {
                "date": "2026-01-05",
                "type": "dinner",
                "restaurant": "Fusion Kitchen",
                "guests": 4,
                "value": 600.00,
                "reason": "new_year_celebration"
            }
        ],
        "financial": {
            "credit_line": 25000.00,
            "credit_used": 5000.00,
            "average_bet": 250.00,
            "average_session_buy_in": 5000.00,
            "lifetime_value": 45000.00,
            "ytd_value": 45000.00,
            "last_30_days_value": 12000.00
        },
        "visit_history": {
            "total_visits": 18,
            "last_visit": "2026-01-05",
            "average_visit_duration_hours": 5.0,
            "preferred_days": ["friday", "saturday"],
            "preferred_times": ["evening", "night"]
        },
        "analytics": {
            "win_loss_ratio": 0.42,
            "volatility": "medium",
            "churn_risk": "low",
            "upsell_potential": "very_high",
            "social_influence": "high"
        },
        "special_dates": [
            {"type": "birthday", "date": "1992-04-18"}
        ],
        "notes": [
            {
                "date": "2025-11-10",
                "author": "Host Manager Lisa Wong",
                "note": "Young tech entrepreneur. Very social, often brings groups of friends. Rapid spending increase - potential platinum candidate."
            },
            {
                "date": "2026-01-05",
                "author": "Floor Manager Tom Rodriguez",
                "note": "Celebrating successful business deal with colleagues. Interested in learning more games. Suggested poker lessons."
            }
        ],
        "pre_generated_questions": [
            "What is Michael Park's tier progression timeline?",
            "What games is Michael Park learning or interested in?",
            "When does Michael Park typically visit with groups?",
            "What entertainment venues does Michael Park prefer?",
            "What is Michael Park's upsell potential for platinum tier?",
            "What special events would appeal to Michael Park?"
        ]
    },

    # Flagged Player: Thomas Anderson - Alcohol-restricted player
    "PLAYER-7777": {
        "player_id": "PLAYER-7777",
        "portrait": "/thomas_anderson.png",
        "name": "Thomas Anderson",
        "tier": "silver",
        "member_since": "2021-06-15",
        "contact": {
            "email": "t.anderson@example.com",
            "phone": "+1-555-0777",
            "preferred_contact": "email"
        },
        "preferences": {
            "games": [
                {"game": "slots", "skill_level": "casual", "preference_score": 8},
                {"game": "blackjack", "skill_level": "beginner", "preference_score": 6},
                {"game": "roulette", "skill_level": "beginner", "preference_score": 4}
            ],
            "table_preference": "standard_tables",
            "table_limits": {"min": 25, "max": 500},
            "beverage": "coffee",
            "alternative_beverages": ["coffee", "soda", "energy_drinks", "juice"],
            "dining": [
                {"restaurant": "The Buffet", "cuisine": "buffet", "visits": 10},
                {"restaurant": "Coffee Shop", "cuisine": "cafe", "visits": 8}
            ],
            "entertainment": ["slots", "casual_gaming"],
            "room_preference": "standard_room"
        },
        "comp_history": [
            {
                "date": "2025-11-10",
                "type": "buffet_voucher",
                "value": 50.00,
                "reason": "loyalty_reward"
            },
            {
                "date": "2025-12-15",
                "type": "slot_play_credit",
                "value": 100.00,
                "reason": "tier_benefit"
            }
        ],
        "financial": {
            "credit_line": 2000.00,
            "credit_used": 0.00,
            "average_bet": 25.00,
            "average_session_buy_in": 300.00,
            "lifetime_value": 8500.00,
            "ytd_value": 1200.00,
            "last_30_days_value": 400.00
        },
        "visit_history": {
            "total_visits": 42,
            "last_visit": "2026-01-10",
            "average_visit_duration_hours": 2.5,
            "preferred_days": ["saturday", "sunday"],
            "preferred_times": ["afternoon", "evening"]
        },
        "analytics": {
            "win_loss_ratio": 0.40,
            "volatility": "low",
            "churn_risk": "medium",
            "upsell_potential": "low",
            "social_influence": "low"
        },
        "notes": [
            {
                "date": "2025-11-10",
                "author": "Floor Manager Tom Rodriguez",
                "note": "They can get agitated if they consume excessive amounts of alcohol."
            }
        ],
        "special_dates": [
            {"type": "birthday", "date": "1975-08-22"}
        ],
        "pre_generated_questions": [
            "What beverages can be offered to Thomas Anderson?",
            "What is the protocol for serving Thomas Anderson?",
            "What should staff do when Thomas Anderson arrives?"
        ]
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
                "player_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Player ID for get operation"
                    }
                },
                "player_ids": {
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
                "player_id": {
                    "type": "string",
                    "description": "Unique player identifier"
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
            "required": ["player_id", "name", "tier"],
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
            player_id = params.get("player_id")
            if not player_id:
                return ToolResult.err(
                    "player_id is required for get operation",
                    code="missing_parameter"
                )
            
            # Get player profile from synthesized data
            profile = DEMO_PLAYER_PROFILES.get(player_id)
            if profile is None:
                return ToolResult.err(
                    f"Player not found: {player_id}",
                    code="player_not_found"
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
            
        elif op == "get_by_players":
            player_ids = params.get("player_ids")
            if player_ids is None:
                return ToolResult.err(
                    "player_ids is required for get_by_players operation",
                    code="missing_parameter"
                )
            
            if not isinstance(player_ids, list):
                return ToolResult.err(
                    "player_ids must be an array",
                    code="invalid_parameter_type"
                )
            
            # Retrieve profiles for all requested player IDs
            profiles = []
            not_found_ids = []
            
            for player_id in player_ids:
                profile = DEMO_PLAYER_PROFILES.get(player_id)
                if profile is not None:
                    profiles.append(filter_profile(profile))
                else:
                    not_found_ids.append(player_id)
            
            # Build diagnostics
            diagnostics = ["Demo mode: using synthesized data"]
            if not_found_ids:
                diagnostics.append(f"Players not found: {', '.join(not_found_ids)}")
            
            return ToolResult.ok(
                data={"profiles": profiles},
                diagnostics=diagnostics
            )
            
        else:
            return ToolResult.err(
                f"Unknown operation: {op}",
                code="invalid_operation"
            )
