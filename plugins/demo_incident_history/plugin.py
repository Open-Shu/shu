"""Demo Incident History Plugin Implementation.

This plugin simulates security and customer service incident tracking for the
La Vision casino demo. It returns pre-crafted synthesized incident histories
that demonstrate what Shu can accomplish when integrated with real security
and customer service systems.
"""

import asyncio
import random
from typing import Any, Dict

import logging

logger = logging.getLogger(__name__)


# Synthesized incident histories for all demo players
DEMO_INCIDENT_HISTORIES = {
    # VIP Player 1: David Chen - Minor disputes, positive notes
    "PLAYER-5678": {
        "player_id": "PLAYER-5678",
        "incidents": [
            {
                "incident_id": "INC-2025-456",
                "date": "2025-11-15T22:30:00Z",
                "type": "dispute",
                "severity": "low",
                "category": "comp_calculation",
                "description": "Player questioned comp point calculation for October play. Believed he should have earned additional 500 points.",
                "resolution": "Reviewed play history. Calculation was correct but player was close to next tier threshold. Issued courtesy 500 bonus points to maintain goodwill.",
                "resolved_by": "Floor Manager Sarah Johnson",
                "resolution_time_minutes": 15,
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-892",
                "date": "2024-08-20T19:45:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "exceptional_behavior",
                "description": "Player helped elderly guest who appeared confused at blackjack table. Explained rules patiently and called floor staff for assistance.",
                "resolution": "Sent thank you note and $100 dining credit.",
                "resolved_by": "Pit Boss Michael Chen",
                "player_satisfaction": "n/a",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "none",
        "self_exclusion": False,
        "regulatory_flags": [],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "calm_and_friendly",
            "alcohol_consumption": "moderate",
            "tipping_behavior": "generous",
            "staff_interactions": "respectful",
            "other_player_interactions": "friendly"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "low",
            "security_risk": "none",
            "overall_risk": "low"
        },
        "summary": "Preferred customer with no concerns. One minor comp dispute resolved favorably. Known for courteous behavior and positive interactions with staff and other guests."
    },
    
    # VIP Player 5: Ahmed Al Mansoori - Young, social, learning
    "PLAYER-3456": {
        "player_id": "PLAYER-3456",
        "incidents": [
            {
                "incident_id": "INC-2025-890",
                "date": "2025-11-20T22:45:00Z",
                "type": "minor_incident",
                "severity": "low",
                "category": "noise_complaint",
                "description": "Player and group of friends became loud and boisterous at blackjack table. Other guests complained about noise level.",
                "resolution": "Floor manager politely asked group to lower voices. Player immediately apologized and complied. No further issues. Player sent apology note next day.",
                "resolved_by": "Floor Manager Tom Rodriguez",
                "resolution_time_minutes": 5,
                "player_satisfaction": "apologetic",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2025-234",
                "date": "2025-10-15T20:30:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "exceptional_behavior",
                "description": "Player tipped dealer $500 after winning hand. Shared winnings with entire table, buying drinks for all players.",
                "resolution": "Noted generous behavior. Player creates positive atmosphere at tables.",
                "resolved_by": "Pit Boss Sarah Lee",
                "player_satisfaction": "n/a",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "none",
        "self_exclusion": False,
        "regulatory_flags": [],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "enthusiastic_and_social",
            "alcohol_consumption": "moderate_to_high",
            "tipping_behavior": "very_generous",
            "staff_interactions": "friendly_and_respectful",
            "other_player_interactions": "very_social"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "monitor_spending_velocity",
            "credit_risk": "low",
            "security_risk": "none",
            "overall_risk": "low"
        },
        "summary": "Young, enthusiastic player with rapid tier advancement. One minor noise incident resolved immediately with apology. Known for generous tipping and creating positive atmosphere. Monitor spending patterns for responsible gaming."
    },
    
    # Flagged Player 2: Thomas Anderson - Alcohol-restricted player
    "PLAYER-7777": {
        "player_id": "PLAYER-7777",
        "incidents": [
            {
                "incident_id": "INC-2025-0215",
                "date": "2025-02-15T20:15:00Z",
                "type": "behavioral_incident",
                "severity": "medium",
                "category": "disruptive_behavior",
                "description": "Player became increasingly loud and disruptive after consuming multiple alcoholic beverages. Singing loudly at blackjack table. Other players complained. Floor staff asked player to quiet down multiple times.",
                "resolution": "Player eventually complied but remained disruptive. Beverage service cut off after 5 mixed drinks. Player left property shortly after. Incident documented as part of pattern.",
                "resolved_by": "Floor Manager Tom Rodriguez",
                "resolution_time_minutes": 25,
                "player_satisfaction": "dissatisfied",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-1120",
                "date": "2024-11-20T22:00:00Z",
                "type": "minor_incident",
                "severity": "low",
                "category": "intoxication",
                "description": "Player appeared intoxicated. Stumbling, slurred speech. Beverage service cut off per responsible gaming policy. Player accepted decision without issue.",
                "resolution": "Player offered coffee and water. Player accepted and sobered up. Left property safely via rideshare. No further issues.",
                "resolved_by": "Beverage Manager Lisa Chen",
                "resolution_time_minutes": 15,
                "player_satisfaction": "understanding",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "standard_monitoring",
        "self_exclusion": False,
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "friendly_when_sober",
            "tipping_behavior": "appropriate",
            "other_player_interactions": "friendly"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "low",
            "security_risk": "medium",
            "overall_risk": "low_with_monitoring"
        },
        "summary": "Player has received multiple warnings due to alcohol related disturbances."
    }
}


class DemoIncidentHistoryPlugin:
    """Demo plugin for incident history tracking.
    
    This plugin provides synthesized incident histories for casino players,
    including security notes, disputes, behavioral tracking, and risk assessments.
    All data is pre-crafted for demonstration purposes.
    """
    
    name = "demo_incident_history"
    version = "1.0.0"
    
    def __init__(self):
        """Initialize the demo incident history plugin."""
        pass
    
    def get_schema(self) -> Dict[str, Any]:
        """Return JSON schema for plugin parameters.
        
        Returns:
            Dict containing the JSON schema for plugin operations
        """
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["get_by_player", "list", "search"],
                    "default": "get_by_player",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "get_by_player": "Get Incident History by Player ID",
                            "list": "List All Incident Histories",
                            "search": "Search Incident Histories"
                        },
                        "enum_help": {
                            "get_by_player": "Retrieve incident history for a specific player ID",
                            "list": "List all available incident histories",
                            "search": "Search incident histories by criteria"
                        }
                    }
                },
                "player_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Player ID to retrieve incident history for (required for get_by_player operation)"
                    }
                },
                "include_risk_assessment": {
                    "type": "boolean",
                    "default": True,
                    "x-ui": {
                        "help": "Include risk assessment data in response"
                    }
                },
                "include_behavioral_notes": {
                    "type": "boolean",
                    "default": True,
                    "x-ui": {
                        "help": "Include behavioral notes in response"
                    }
                }
            },
            "required": [],
            "additionalProperties": False
        }
    
    def get_output_schema(self) -> Dict[str, Any]:
        """Return JSON schema for plugin output.
        
        Returns:
            Dict containing the JSON schema for plugin output
        """
        # Define the incident schema
        incident_schema = {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "Unique incident identifier"
                },
                "date": {
                    "type": "string",
                    "format": "date-time",
                    "description": "Incident date and time"
                },
                "type": {
                    "type": "string",
                    "description": "Type of incident (dispute, positive_note, violent_behavior, etc.)"
                },
                "severity": {
                    "type": "string",
                    "enum": ["none", "low", "medium", "high", "critical", "administrative"],
                    "description": "Incident severity level"
                },
                "category": {
                    "type": "string",
                    "description": "Incident category"
                },
                "description": {
                    "type": "string",
                    "description": "Detailed incident description"
                },
                "resolution": {
                    "type": "string",
                    "description": "How the incident was resolved"
                },
                "resolved_by": {
                    "type": "string",
                    "description": "Staff member who resolved the incident"
                },
                "resolution_time_minutes": {
                    "type": "integer",
                    "description": "Time taken to resolve incident in minutes"
                },
                "player_satisfaction": {
                    "type": "string",
                    "description": "Player satisfaction level after resolution"
                },
                "follow_up_required": {
                    "type": "boolean",
                    "description": "Whether follow-up action is required"
                },
                "police_report": {
                    "type": "string",
                    "description": "Police report number (if applicable)"
                },
                "witnesses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of witnesses"
                },
                "injuries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of injuries sustained"
                },
                "review_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date for review (if applicable)"
                }
            },
            "required": ["incident_id", "date", "type", "severity", "category", "description"],
            "description": "Individual incident record"
        }
        
        # Define the behavioral notes schema
        behavioral_notes_schema = {
            "type": "object",
            "properties": {
                "temperament": {
                    "type": "string",
                    "description": "Player's general temperament"
                },
                "alcohol_consumption": {
                    "type": "string",
                    "description": "Alcohol consumption pattern"
                },
                "tipping_behavior": {
                    "type": "string",
                    "description": "Tipping behavior pattern"
                },
                "staff_interactions": {
                    "type": "string",
                    "description": "Quality of interactions with staff"
                },
                "other_player_interactions": {
                    "type": "string",
                    "description": "Quality of interactions with other players"
                }
            },
            "description": "Behavioral observation notes"
        }
        
        # Define the risk assessment schema
        risk_assessment_schema = {
            "type": "object",
            "properties": {
                "problem_gambling_indicators": {
                    "type": "string",
                    "description": "Problem gambling risk indicators"
                },
                "credit_risk": {
                    "type": "string",
                    "description": "Credit risk level"
                },
                "security_risk": {
                    "type": "string",
                    "description": "Security risk level"
                },
                "overall_risk": {
                    "type": "string",
                    "description": "Overall risk assessment"
                }
            },
            "description": "Comprehensive risk assessment"
        }
        
        # Define the ban details schema
        ban_details_schema = {
            "type": "object",
            "properties": {
                "ban_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date ban was imposed"
                },
                "ban_type": {
                    "type": "string",
                    "enum": ["temporary", "permanent"],
                    "description": "Type of ban"
                },
                "ban_reason": {
                    "type": "string",
                    "description": "Reason for ban"
                },
                "appeal_status": {
                    "type": "string",
                    "description": "Status of any appeal"
                },
                "appeal_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date of appeal (if applicable)"
                },
                "trespass_notice_served": {
                    "type": "boolean",
                    "description": "Whether trespass notice has been served"
                },
                "police_involvement": {
                    "type": "boolean",
                    "description": "Whether police were involved"
                },
                "criminal_charges": {
                    "type": "string",
                    "description": "Criminal charges filed (if applicable)"
                }
            },
            "description": "Detailed ban information"
        }
        
        # Define the restriction details schema
        restriction_details_schema = {
            "type": "object",
            "properties": {
                "restriction_type": {
                    "type": "string",
                    "description": "Type of restriction imposed"
                },
                "effective_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date restriction became effective"
                },
                "review_date": {
                    "type": "string",
                    "format": "date",
                    "description": "Date for restriction review"
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for restriction"
                },
                "compliance_status": {
                    "type": "string",
                    "description": "Player's compliance with restriction"
                },
                "incidents_since_restriction": {
                    "type": "integer",
                    "description": "Number of incidents since restriction was imposed"
                },
                "positive_notes_since_restriction": {
                    "type": "integer",
                    "description": "Number of positive compliance notes"
                }
            },
            "description": "Detailed restriction information"
        }
        
        # Define the complete incident history schema
        incident_history_schema = {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "Unique player identifier"
                },
                "incidents": {
                    "type": "array",
                    "items": incident_schema,
                    "description": "Array of incident records"
                },
                "watchlist_status": {
                    "type": "string",
                    "enum": ["none", "standard_monitoring", "vip_protection", "high_priority", "banned_permanent"],
                    "description": "Current watchlist status"
                },
                "self_exclusion": {
                    "type": "boolean",
                    "description": "Whether player has self-excluded"
                },
                "regulatory_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Regulatory compliance flags"
                },
                "credit_flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Credit-related flags"
                },
                "behavioral_notes": {
                    **behavioral_notes_schema,
                    "description": "Behavioral observation notes"
                },
                "risk_assessment": {
                    **risk_assessment_schema,
                    "description": "Comprehensive risk assessment"
                },
                "ban_details": {
                    **ban_details_schema,
                    "description": "Ban details (if player is banned)"
                },
                "restriction_details": {
                    **restriction_details_schema,
                    "description": "Restriction details (if player has restrictions)"
                },
                "summary": {
                    "type": "string",
                    "description": "Summary of incident history and player status"
                }
            },
            "required": ["player_id", "incidents", "watchlist_status", "self_exclusion"],
            "description": "Complete incident history for a player"
        }
        
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "incident_history": incident_history_schema,
                        "found": {
                            "type": "boolean",
                            "description": "Whether the player was found"
                        }
                    },
                    "required": ["found"],
                    "additionalProperties": False,
                    "description": "Single player incident history result"
                },
                {
                    "type": "object",
                    "properties": {
                        "incident_histories": {
                            "type": "array",
                            "items": incident_history_schema,
                            "description": "Array of incident histories for all players"
                        }
                    },
                    "required": ["incident_histories"],
                    "additionalProperties": False,
                    "description": "Multiple player incident histories result (for list operation)"
                }
            ]
        }
    
    async def execute(
        self,
        params: Dict[str, Any],
        context: Any,
        host: Any
    ) -> Dict[str, Any]:
        """Execute the plugin operation.
        
        Args:
            params: Operation parameters including operation name and arguments
            context: Execution context
            host: Host capabilities interface
            
        Returns:
            Dict containing the operation result in ToolResult format
        """
        op = params.get("op", "get_by_player")
        
        if op == "get_by_player":
            res = await self._get_by_player(params)
            logger.info(res)
            return res
        
        return {
            "status": "error",
            "data": None,
            "diagnostics": [f"Unknown operation: {op}"],
            "cost": {"api_calls": 0}
        }
    
    async def _get_by_player(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get incident history for a specified player ID.
        
        Args:
            params: Parameters including player_id string
            
        Returns:
            Dict containing incident history in ToolResult format
        """
        # Simulate realistic API delay
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        player_id = params.get("player_id")
        
        if not player_id:
            return {
                "status": "error",
                "data": None,
                "diagnostics": ["No player_id provided"],
                "cost": {"api_calls": 0}
            }
        
        if not isinstance(player_id, str):
            return {
                "status": "error",
                "data": None,
                "diagnostics": ["player_id must be a string"],
                "cost": {"api_calls": 0}
            }
        
        # Retrieve incident history for requested player
        diagnostics = ["Demo mode: using synthesized data"]

        if player_id in DEMO_INCIDENT_HISTORIES:
            return {
                "status": "success",
                "data": {
                    "incident_history": DEMO_INCIDENT_HISTORIES[player_id],
                    "found": True
                },
                "diagnostics": diagnostics,
                "cost": {"api_calls": 0}
            }
        else:
            diagnostics.append(f"Player not found: {player_id}")
            return {
                "status": "success",
                "data": {
                    "found": False
                },
                "diagnostics": diagnostics,
                "cost": {"api_calls": 0}
            }
