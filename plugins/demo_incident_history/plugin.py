"""Demo Incident History Plugin Implementation.

This plugin simulates security and customer service incident tracking for the
La Vision casino demo. It returns pre-crafted synthesized incident histories
that demonstrate what Shu can accomplish when integrated with real security
and customer service systems.
"""

from typing import Any, Dict


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
    
    # VIP Player 2: Sarah Martinez - Poker tournament regular, professional demeanor
    "PLAYER-8901": {
        "player_id": "PLAYER-8901",
        "incidents": [
            {
                "incident_id": "INC-2025-789",
                "date": "2025-09-10T21:15:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "exceptional_behavior",
                "description": "Player noticed dealer error in her favor during high-stakes poker game. Immediately alerted floor manager to correct the mistake.",
                "resolution": "Commended player for integrity. Added note to profile highlighting exceptional character.",
                "resolved_by": "Tournament Director Mike Chen",
                "player_satisfaction": "n/a",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-234",
                "date": "2024-11-05T23:00:00Z",
                "type": "dispute",
                "severity": "low",
                "category": "tournament_ruling",
                "description": "Player disagreed with tournament director's ruling on a hand. Requested review of decision.",
                "resolution": "Reviewed hand with tournament officials. Original ruling upheld but player's concern was valid. Explained reasoning thoroughly. Player accepted decision professionally.",
                "resolved_by": "Tournament Director Mike Chen",
                "resolution_time_minutes": 20,
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "none",
        "self_exclusion": False,
        "regulatory_flags": [],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "professional_and_composed",
            "alcohol_consumption": "light_to_moderate",
            "tipping_behavior": "very_generous",
            "staff_interactions": "professional",
            "other_player_interactions": "respectful_and_competitive"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "very_low",
            "security_risk": "none",
            "overall_risk": "very_low"
        },
        "summary": "Exemplary VIP customer. Professional poker player with exceptional integrity. No behavioral concerns. Highly valued customer with excellent relationship with staff."
    },
    
    # VIP Player 3: James Wilson - Family-oriented, no incidents
    "PLAYER-2345": {
        "player_id": "PLAYER-2345",
        "incidents": [
            {
                "incident_id": "INC-2025-567",
                "date": "2025-12-15T18:30:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "exceptional_behavior",
                "description": "Player's teenage son found lost wallet with $800 cash. Family turned it in to security immediately. Owner identified and wallet returned intact.",
                "resolution": "Sent thank you letter to family and complimentary show tickets. Added positive note to profile.",
                "resolved_by": "Security Manager James Wilson",
                "player_satisfaction": "n/a",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "none",
        "self_exclusion": False,
        "regulatory_flags": [],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "friendly_and_easygoing",
            "alcohol_consumption": "light",
            "tipping_behavior": "appropriate",
            "staff_interactions": "friendly",
            "other_player_interactions": "social_and_friendly"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "low",
            "security_risk": "none",
            "overall_risk": "very_low"
        },
        "summary": "Family-oriented guest with no concerns. Known for bringing family members. Positive interactions with staff and other guests. No incidents or issues."
    },
    
    # VIP Player 4: Elena Volkov - High-stakes player, privacy-focused
    "PLAYER-6789": {
        "player_id": "PLAYER-6789",
        "incidents": [
            {
                "incident_id": "INC-2025-345",
                "date": "2025-10-20T02:15:00Z",
                "type": "dispute",
                "severity": "medium",
                "category": "privacy_concern",
                "description": "Player expressed strong concern about another guest attempting to photograph her at baccarat table. Requested immediate intervention.",
                "resolution": "Security immediately addressed situation. Other guest's phone confiscated temporarily, photos deleted. Guest escorted from high-limit area. Player satisfied with response.",
                "resolved_by": "Security Director James Wilson",
                "resolution_time_minutes": 10,
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-678",
                "date": "2024-06-15T01:30:00Z",
                "type": "service_complaint",
                "severity": "low",
                "category": "beverage_service",
                "description": "Player requested specific vodka brand not available at table. Expressed disappointment with beverage selection.",
                "resolution": "Immediately sourced requested vodka brand from main bar. Added brand to high-limit room permanent inventory. Player appreciated quick response.",
                "resolved_by": "Beverage Manager Lisa Chen",
                "resolution_time_minutes": 12,
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "vip_protection",
        "self_exclusion": False,
        "regulatory_flags": [],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "reserved_and_private",
            "alcohol_consumption": "moderate",
            "tipping_behavior": "extremely_generous",
            "staff_interactions": "minimal_but_respectful",
            "other_player_interactions": "prefers_privacy"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "low",
            "security_risk": "none",
            "overall_risk": "low"
        },
        "summary": "Ultra-high-value customer requiring privacy and discretion. No behavioral concerns. Incidents relate to privacy protection and service preferences. Excellent relationship maintained through attentive service."
    },
    
    # VIP Player 5: Michael Park - Young, social, learning
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
    
    # Flagged Player 1: Robert Blackwell - Banned for violent behavior
    "PLAYER-9999": {
        "player_id": "PLAYER-9999",
        "incidents": [
            {
                "incident_id": "INC-2024-1145",
                "date": "2024-08-15T23:45:00Z",
                "type": "violent_behavior",
                "severity": "critical",
                "category": "physical_altercation",
                "description": "Player became extremely aggressive after significant losses at blackjack table. Verbally abusive to dealer, using profanity and threats. When pit boss intervened, player shoved dealer and attempted to overturn table. Security called immediately.",
                "resolution": "Security and police responded. Player physically restrained. Three staff members injured (minor). Police arrested player for assault and battery. Permanent ban issued. Trespass notice served. Criminal charges filed.",
                "resolved_by": "Security Director James Wilson",
                "resolution_time_minutes": 45,
                "player_satisfaction": "n/a",
                "follow_up_required": True,
                "police_report": "CASE-2024-0815-1145",
                "witnesses": ["Dealer Mike Johnson", "Security Officer Sarah Lee", "Floor Manager Tom Rodriguez", "Pit Boss Jennifer Liu"],
                "injuries": ["Dealer Mike Johnson - bruised arm", "Security Officer Sarah Lee - scratched face", "Security Officer Tom Chen - bruised ribs"]
            },
            {
                "incident_id": "INC-2024-0920",
                "date": "2024-09-20T19:30:00Z",
                "type": "trespass_attempt",
                "severity": "high",
                "category": "banned_player_entry_attempt",
                "description": "Facial recognition system identified banned player attempting entry through side entrance. Security immediately dispatched.",
                "resolution": "Player recognized by security. Informed of trespass status. Player left property without incident. Police notified as per protocol.",
                "resolved_by": "Security Officer David Martinez",
                "resolution_time_minutes": 8,
                "player_satisfaction": "n/a",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-0715",
                "date": "2024-07-15T21:00:00Z",
                "type": "verbal_abuse",
                "severity": "medium",
                "category": "staff_harassment",
                "description": "Player became verbally abusive to cocktail waitress after she informed him of drink limit policy. Used profanity and made inappropriate comments.",
                "resolution": "Floor manager intervened. Player warned about behavior. Player apologized and behavior improved for remainder of visit. Incident documented.",
                "resolved_by": "Floor Manager Tom Rodriguez",
                "resolution_time_minutes": 10,
                "player_satisfaction": "n/a",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2024-0520",
                "date": "2024-05-20T22:30:00Z",
                "type": "dispute",
                "severity": "medium",
                "category": "game_ruling",
                "description": "Player loudly disputed dealer's ruling on blackjack hand. Became argumentative with pit boss. Accused casino of cheating.",
                "resolution": "Surveillance reviewed hand. Ruling was correct. Player shown video evidence. Player reluctantly accepted but remained hostile. Incident documented as pattern of aggressive behavior.",
                "resolved_by": "Pit Boss Jennifer Liu",
                "resolution_time_minutes": 25,
                "player_satisfaction": "dissatisfied",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "banned_permanent",
        "self_exclusion": False,
        "regulatory_flags": ["violent_behavior", "criminal_charges", "staff_assault"],
        "credit_flags": ["suspended_permanently"],
        "behavioral_notes": {
            "temperament": "aggressive_and_volatile",
            "alcohol_consumption": "heavy",
            "tipping_behavior": "poor",
            "staff_interactions": "hostile_and_abusive",
            "other_player_interactions": "confrontational"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "severe_loss_chasing",
            "credit_risk": "n/a_banned",
            "security_risk": "critical",
            "overall_risk": "critical"
        },
        "ban_details": {
            "ban_date": "2024-08-15",
            "ban_type": "permanent",
            "ban_reason": "violent_behavior_staff_assault",
            "appeal_status": "denied",
            "appeal_date": "2024-09-01",
            "trespass_notice_served": True,
            "police_involvement": True,
            "criminal_charges": "assault_and_battery"
        },
        "summary": "CRITICAL SECURITY RISK. Player permanently banned after violent assault on staff. Pattern of escalating aggressive behavior documented. Criminal charges filed. Trespass notice served. DENY ENTRY IMMEDIATELY. Contact security and management if spotted on property."
    },
    
    # Flagged Player 2: Thomas Anderson - Alcohol-restricted player
    "PLAYER-7777": {
        "player_id": "PLAYER-7777",
        "incidents": [
            {
                "incident_id": "INC-2025-0320",
                "date": "2025-03-20T16:00:00Z",
                "type": "administrative",
                "severity": "administrative",
                "category": "restriction_imposed",
                "description": "Alcohol service restriction formally imposed after pattern of alcohol-related behavioral incidents. Player notified by certified mail and in-person meeting with guest services manager.",
                "resolution": "Restriction effective immediately. Player accepted restriction and expressed understanding. Agreed to comply with no-alcohol policy. Review date set for one year.",
                "resolved_by": "Guest Services Manager Amy Chen",
                "resolution_time_minutes": 30,
                "player_satisfaction": "understanding",
                "follow_up_required": True,
                "review_date": "2026-03-20"
            },
            {
                "incident_id": "INC-2025-0310",
                "date": "2025-03-10T21:45:00Z",
                "type": "behavioral_incident",
                "severity": "high",
                "category": "intoxication",
                "description": "Player arrived at casino already intoxicated. Became loud and verbally abusive to other guests at slots area. Knocked over drink, creating mess. Refused to calm down when approached by floor staff.",
                "resolution": "Security called. Player escorted from property. Banned from property for 30 days. Incident triggered review of player's alcohol-related history. Decision made to impose permanent alcohol restriction.",
                "resolved_by": "Security Officer David Martinez",
                "resolution_time_minutes": 20,
                "player_satisfaction": "hostile",
                "follow_up_required": True
            },
            {
                "incident_id": "INC-2025-0215",
                "date": "2025-02-15T20:15:00Z",
                "type": "behavioral_incident",
                "severity": "medium",
                "category": "disruptive_behavior",
                "description": "Player became increasingly loud and disruptive after consuming multiple alcoholic beverages. Singing loudly at blackjack table. Other players complained. Floor staff asked player to quiet down multiple times.",
                "resolution": "Player eventually complied but remained disruptive. Beverage service cut off. Player left property shortly after. Incident documented as part of pattern.",
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
            },
            {
                "incident_id": "INC-2025-0615",
                "date": "2025-06-15T19:30:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "compliance",
                "description": "Player's first visit since alcohol restriction imposed. Player complied fully with restriction. Ordered coffee and soda. Behavior was appropriate throughout visit. No issues.",
                "resolution": "Positive compliance noted. Player thanked staff for allowing him to continue visiting. Expressed appreciation for restriction helping him.",
                "resolved_by": "Floor Manager Tom Rodriguez",
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            },
            {
                "incident_id": "INC-2025-1215",
                "date": "2025-12-15T18:00:00Z",
                "type": "positive_note",
                "severity": "none",
                "category": "continued_compliance",
                "description": "Player continues to comply with alcohol restriction. Regular visitor with no behavioral issues since restriction imposed. Positive attitude and appreciation for being able to continue visiting.",
                "resolution": "Continued positive compliance documented. Player mentioned restriction has been helpful. Recommend favorable review at one-year mark.",
                "resolved_by": "Slot Host David Park",
                "player_satisfaction": "satisfied",
                "follow_up_required": False
            }
        ],
        "watchlist_status": "standard_monitoring",
        "self_exclusion": False,
        "regulatory_flags": ["alcohol_restriction"],
        "credit_flags": [],
        "behavioral_notes": {
            "temperament": "friendly_when_sober",
            "alcohol_consumption": "restricted_none_allowed",
            "tipping_behavior": "appropriate",
            "staff_interactions": "respectful_since_restriction",
            "other_player_interactions": "friendly"
        },
        "risk_assessment": {
            "problem_gambling_indicators": "none",
            "credit_risk": "low",
            "security_risk": "low_with_restriction",
            "overall_risk": "low_with_monitoring"
        },
        "restriction_details": {
            "restriction_type": "alcohol_service_prohibited",
            "effective_date": "2025-03-20",
            "review_date": "2026-03-20",
            "reason": "pattern_of_alcohol_related_incidents",
            "compliance_status": "excellent",
            "incidents_since_restriction": 0,
            "positive_notes_since_restriction": 2
        },
        "summary": "Player with alcohol restriction in place since March 2025. Pattern of alcohol-related behavioral incidents led to restriction. Since restriction imposed, player has shown excellent compliance and positive behavior. No incidents. Player appreciates being able to continue visiting and has expressed that restriction has been helpful. Recommend favorable review at one-year mark."
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
                    "enum": ["get_by_players", "list", "search"],
                    "default": "get_by_players",
                    "x-ui": {
                        "help": "Operation to perform",
                        "enum_labels": {
                            "get_by_players": "Get Incident Histories by Player IDs",
                            "list": "List All Incident Histories",
                            "search": "Search Incident Histories"
                        },
                        "enum_help": {
                            "get_by_players": "Retrieve incident histories for specific player IDs",
                            "list": "List all available incident histories",
                            "search": "Search incident histories by criteria"
                        }
                    }
                },
                "player_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-ui": {
                        "help": "Array of player IDs to retrieve incident histories for (required for get_by_players operation)"
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
            "type": "object",
            "properties": {
                "incident_histories": {
                    "type": "array",
                    "items": incident_history_schema,
                    "description": "Array of incident histories for requested players"
                },
                "requested_count": {
                    "type": "integer",
                    "description": "Number of players requested"
                },
                "found_count": {
                    "type": "integer",
                    "description": "Number of players found"
                },
                "not_found": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Player IDs that were not found"
                }
            },
            "required": ["incident_histories"],
            "additionalProperties": False
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
        op = params.get("op", "get_by_players")
        
        if op == "get_by_players":
            return await self._get_by_players(params)
        
        return {
            "status": "error",
            "data": None,
            "diagnostics": [f"Unknown operation: {op}"],
            "cost": {"api_calls": 0}
        }
    
    async def _get_by_players(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get incident histories for specified player IDs.
        
        Args:
            params: Parameters including player_ids array
            
        Returns:
            Dict containing incident histories in ToolResult format
        """
        player_ids = params.get("player_ids", [])
        
        if not player_ids:
            return {
                "status": "error",
                "data": None,
                "diagnostics": ["No player_ids provided"],
                "cost": {"api_calls": 0}
            }
        
        if not isinstance(player_ids, list):
            return {
                "status": "error",
                "data": None,
                "diagnostics": ["player_ids must be an array"],
                "cost": {"api_calls": 0}
            }
        
        # Retrieve incident histories for requested players
        results = []
        not_found = []
        
        for player_id in player_ids:
            if player_id in DEMO_INCIDENT_HISTORIES:
                results.append(DEMO_INCIDENT_HISTORIES[player_id])
            else:
                not_found.append(player_id)
        
        diagnostics = ["Demo mode: using synthesized data"]
        if not_found:
            diagnostics.append(f"Players not found: {', '.join(not_found)}")
        
        return {
            "status": "success",
            "data": {
                "incident_histories": results,
                "requested_count": len(player_ids),
                "found_count": len(results),
                "not_found": not_found
            },
            "diagnostics": diagnostics,
            "cost": {"api_calls": 0}
        }
