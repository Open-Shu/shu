"""
Decision Control Step for Experience Workflows.

This module implements a fully deterministic decision control step that evaluates
player data and decides whether to execute subsequent workflow steps. The decision
logic is based on player tier, lifetime value, visit history, and other factors.

This is designed for the La Vision casino demo and uses deterministic rules rather
than LLM calls for predictable demo behavior.

Supported Decision Types:
- table_reservation: Decide whether to proactively reserve tables for VIP players
- vip_host_notification: Decide whether to alert VIP hosts for high-value arrivals
- security_alert: Decide whether to alert security for banned/watchlist players
- alcohol_service: Decide whether to cut off alcohol service based on consumption tracking
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


# Decision thresholds and constants
HIGH_VALUE_THRESHOLD = 100000.00  # Lifetime value threshold for high-value players
RECENT_VISIT_DAYS = 14  # Days threshold for "recent" visit
HIGH_BET_THRESHOLD = 300.00  # Average bet threshold
PLATINUM_DIAMOND_TIERS = ["platinum", "diamond"]
VIP_TIERS = ["gold", "platinum", "diamond"]


class DecisionControlStep:
    """
    Decision control step for experience workflows.
    
    This step evaluates player context and decides whether to execute
    subsequent actions (e.g., table reservations, VIP host notifications).
    
    The decision logic is fully deterministic based on player data:
    - VIP tier (Platinum/Diamond vs Gold vs others)
    - Lifetime value
    - Recent visit history
    - Average bet size
    - Special occasions
    - Incident history
    """
    
    def __init__(self):
        """Initialize the decision control step."""
        pass
    
    async def execute(
        self,
        config: Dict[str, Any],
        context: Dict[str, Any],
        host: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Execute the decision control step.
        
        Args:
            config: Step configuration including decision criteria
            context: Workflow context with player data
            host: Optional host capabilities for logging
            
        Returns:
            Decision result with should_execute, rationale, and metadata
        """
        try:
            # Check for override flag
            override = config.get("override")
            if override is not None:
                return self._handle_override(override, context, host)
            
            # Extract decision type from config
            decision_type = config.get("decision_type", "table_reservation")
            
            # Route to appropriate decision logic
            if decision_type == "table_reservation":
                return await self._decide_table_reservation(context, host)
            elif decision_type == "vip_host_notification":
                return await self._decide_vip_host_notification(context, host)
            elif decision_type == "security_alert":
                return await self._decide_security_alert(context, host)
            elif decision_type == "alcohol_service":
                return await self._decide_alcohol_service(context, host)
            else:
                # Default to table reservation logic
                return await self._decide_table_reservation(context, host)
                
        except Exception as e:
            logger.exception(f"Decision control step failed: {e}")
            if host and hasattr(host, "audit"):
                await host.audit.log({
                    "error": str(e),
                    "step": "decision_control"
                })
            
            return {
                "should_execute": False,
                "rationale": f"Decision evaluation failed: {str(e)}",
                "confidence": 0.0,
                "error": True
            }
    
    def _handle_override(
        self,
        override: bool,
        context: Dict[str, Any],
        host: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Handle override flag for demo control.
        
        Args:
            override: Override value (True/False)
            context: Workflow context
            host: Optional host capabilities
            
        Returns:
            Decision result with override indication
        """
        logger.info(f"Decision control override: {override}")
        
        return {
            "should_execute": override,
            "rationale": f"Decision overridden to {override} for demo control",
            "confidence": 1.0,
            "override_used": True,
            "recommended_table": None
        }
    
    async def _decide_table_reservation(
        self,
        context: Dict[str, Any],
        host: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Decide whether to proactively reserve a table for a VIP player.
        
        Decision criteria:
        - Reserve if: Platinum/Diamond tier AND lifetime value > $100k AND preferred table available
        - Reserve if: Last visit < 14 days ago AND average bet > $300
        - Reserve if: Special occasion within 7 days
        - Do NOT reserve if: Gold tier with low recent activity
        - Do NOT reserve if: Banned or restricted players
        
        Args:
            context: Workflow context with player_profile, table_availability, etc.
            host: Optional host capabilities
            
        Returns:
            Decision result
        """
        # Extract player profile from context
        steps = context.get("steps", {})
        player_profile_step = steps.get("player_profiles", {})
        player_profile_data = player_profile_step.get("data", {})
        player_profile = player_profile_data.get("profile", {})
        
        # Extract table availability from context
        table_availability_step = steps.get("table_availability", {})
        table_availability = table_availability_step.get("data", {})
        
        # Extract incident history from context
        incident_check_step = steps.get("incident_histories", {})
        incident_check_data = incident_check_step.get("data", {})
        incident_check = incident_check_data.get("incident_history", {})
        
        # Get player attributes
        player_name = player_profile.get("name", "Unknown")
        tier = player_profile.get("tier", "").lower()
        
        # Check for banned or restricted players
        restriction_flags = player_profile.get("restriction_flags", {})
        if restriction_flags.get("banned"):
            return {
                "should_execute": False,
                "rationale": f"Player {player_name} is banned. Do not reserve table or provide services.",
                "confidence": 1.0,
                "priority": "critical",
                "recommended_table": None,
                "recommended_actions": ["deny_entry", "alert_security"]
            }
        
        # Get financial data
        financial = player_profile.get("financial", {})
        lifetime_value = financial.get("lifetime_value", 0.0)
        average_bet = financial.get("average_bet", 0.0)
        
        # Get visit history
        visit_history = player_profile.get("visit_history", {})
        last_visit = visit_history.get("last_visit")
        
        # Calculate days since last visit
        days_since_visit = self._calculate_days_since_visit(last_visit)
        
        # Check for special occasions
        has_special_occasion = self._check_special_occasions(player_profile)
        
        # Get preferred game and table
        preferences = player_profile.get("preferences", {})
        games = preferences.get("games", [])
        preferred_game = games[0].get("game") if games else None
        table_preference = preferences.get("table_preference", "")
        
        # Find matching available table
        recommended_table = self._find_matching_table(
            table_availability,
            preferred_game,
            table_preference,
            preferences.get("table_limits", {})
        )
        
        # Decision logic
        should_execute = False
        rationale_parts = []
        priority = "medium"
        
        # Rule 1: Platinum/Diamond tier with high lifetime value
        if tier in PLATINUM_DIAMOND_TIERS and lifetime_value > HIGH_VALUE_THRESHOLD:
            if recommended_table:
                should_execute = True
                rationale_parts.append(
                    f"{tier.capitalize()} tier player with ${lifetime_value:,.0f} lifetime value"
                )
                rationale_parts.append(f"Preferred {preferred_game} table available")
                priority = "high"
            else:
                rationale_parts.append(
                    f"{tier.capitalize()} tier player with ${lifetime_value:,.0f} lifetime value"
                )
                rationale_parts.append(f"No matching {preferred_game} table currently available")
        
        # Rule 2: Recent visitor with high average bet
        elif days_since_visit is not None and days_since_visit < RECENT_VISIT_DAYS and average_bet > HIGH_BET_THRESHOLD:
            if recommended_table:
                should_execute = True
                rationale_parts.append(
                    f"Recent visitor (last visit {days_since_visit} days ago) with ${average_bet:.0f} average bet"
                )
                rationale_parts.append("Proactive service recommended")
                priority = "medium"
            else:
                rationale_parts.append(
                    f"Recent visitor with ${average_bet:.0f} average bet, but no matching table available"
                )
        
        # Rule 3: Special occasion
        elif has_special_occasion:
            if recommended_table:
                should_execute = True
                rationale_parts.append(f"Special occasion approaching for {player_name}")
                rationale_parts.append("Proactive service recommended for celebration")
                priority = "high"
            else:
                rationale_parts.append("Special occasion approaching, but no matching table available")
        
        # Rule 4: Gold tier with lower activity
        elif tier == "gold":
            rationale_parts.append("Gold tier player - allow player to choose own table")
            rationale_parts.append("Proactive reservation not required")
        
        # Rule 5: Other tiers
        else:
            rationale_parts.append(f"Standard service level for {tier} tier")
            rationale_parts.append("Proactive reservation not required")
        
        # Build rationale
        rationale = ". ".join(rationale_parts) + "."
        
        # Calculate confidence
        confidence = 1.0 if should_execute else 0.8
        
        # Log decision
        if host and hasattr(host, "audit"):
            await host.audit.log({
                "decision_type": "table_reservation",
                "player_name": player_name,
                "tier": tier,
                "lifetime_value": lifetime_value,
                "should_execute": should_execute,
                "rationale": rationale
            })
        
        return {
            "should_execute": should_execute,
            "rationale": rationale,
            "confidence": confidence,
            "priority": priority,
            "recommended_table": recommended_table
        }
    
    async def _decide_vip_host_notification(
        self,
        context: Dict[str, Any],
        host: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Decide whether to immediately notify a VIP host.
        
        Decision criteria:
        - Notify if: Diamond tier OR lifetime value > $200k
        - Notify if: Special occasion within 7 days
        - Notify if: Recent incident requiring follow-up
        - Notify if: First visit in > 60 days (re-engagement opportunity)
        
        Args:
            context: Workflow context
            host: Optional host capabilities
            
        Returns:
            Decision result
        """
        # Extract player profile
        steps = context.get("steps", {})
        player_profile_step = steps.get("player_profiles", {})
        player_profile_data = player_profile_step.get("data", {})
        player_profile = player_profile_data.get("profile", {})
        
        # Extract incident history
        incident_check_step = steps.get("incident_histories", {})
        incident_check_data = incident_check_step.get("data", {})
        incident_check = incident_check_data.get("incident_history", {})
        
        # Get player attributes
        player_name = player_profile.get("name", "Unknown")
        tier = player_profile.get("tier", "").lower()
        
        # Get financial data
        financial = player_profile.get("financial", {})
        lifetime_value = financial.get("lifetime_value", 0.0)
        
        # Get visit history
        visit_history = player_profile.get("visit_history", {})
        last_visit = visit_history.get("last_visit")
        days_since_visit = self._calculate_days_since_visit(last_visit)
        
        # Check for special occasions
        has_special_occasion = self._check_special_occasions(player_profile)
        
        # Check for recent incidents
        incidents = incident_check.get("incidents", [])
        recent_incidents = [inc for inc in incidents if inc.get("follow_up_required", False)]
        
        # Decision logic
        should_execute = False
        rationale_parts = []
        priority = "medium"
        
        # Rule 1: Diamond tier or ultra-high value
        if tier == "diamond" or lifetime_value > 200000.00:
            should_execute = True
            rationale_parts.append(
                f"{tier.capitalize()} tier player with ${lifetime_value:,.0f} lifetime value"
            )
            rationale_parts.append("VIP host notification required for premium service")
            priority = "high"
        
        # Rule 2: Special occasion
        elif has_special_occasion:
            should_execute = True
            rationale_parts.append(f"Special occasion approaching for {player_name}")
            rationale_parts.append("VIP host should prepare personalized greeting")
            priority = "high"
        
        # Rule 3: Recent incident requiring follow-up
        elif recent_incidents:
            should_execute = True
            rationale_parts.append("Recent incident requiring follow-up")
            rationale_parts.append("VIP host should address any concerns")
            priority = "high"
        
        # Rule 4: Long absence (re-engagement)
        elif days_since_visit is not None and days_since_visit > 60:
            should_execute = True
            rationale_parts.append(f"First visit in {days_since_visit} days")
            rationale_parts.append("Re-engagement opportunity for VIP host")
            priority = "medium"
        
        # Rule 5: Standard service
        else:
            rationale_parts.append(f"Standard arrival for {tier} tier player")
            rationale_parts.append("VIP host notification not required")
        
        # Build rationale
        rationale = ". ".join(rationale_parts) + "."
        
        # Calculate confidence
        confidence = 1.0 if should_execute else 0.9
        
        # Log decision
        if host and hasattr(host, "audit"):
            await host.audit.log({
                "decision_type": "vip_host_notification",
                "player_name": player_name,
                "tier": tier,
                "should_execute": should_execute,
                "rationale": rationale
            })
        
        return {
            "should_execute": should_execute,
            "rationale": rationale,
            "confidence": confidence,
            "priority": priority
        }
    
    async def _decide_security_alert(
        self,
        context: Dict[str, Any],
        host: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Decide whether to alert security.
        
        Decision criteria:
        - Alert if: Player is banned
        - Alert if: Player on high-priority watchlist
        - Alert if: Recent violent or threatening behavior
        - Do NOT alert for: Alcohol restrictions (different protocol)
        
        Args:
            context: Workflow context
            host: Optional host capabilities
            
        Returns:
            Decision result
        """
        # Extract player profile
        steps = context.get("steps", {})
        player_profile_step = steps.get("player_profiles", {})
        player_profile_data = player_profile_step.get("data", {})
        player_profile = player_profile_data.get("profile", {})
        
        # Extract incident history
        incident_check_step = steps.get("incident_histories", {})
        incident_check_data = incident_check_step.get("data", {})
        incident_check = incident_check_data.get("incident_history", {})
        
        # Get player attributes
        player_name = player_profile.get("name", "Unknown")
        
        # Check restriction flags
        restriction_flags = player_profile.get("restriction_flags", {})
        is_banned = restriction_flags.get("banned", False)
        watchlist = restriction_flags.get("watchlist", "none")
        
        # Check ban details
        ban_details = player_profile.get("ban_details", {})
        ban_reason = ban_details.get("ban_reason", "")
        
        # Decision logic
        should_execute = False
        rationale_parts = []
        priority = "low"
        recommended_actions = []
        
        # Rule 1: Banned player
        if is_banned:
            should_execute = True
            rationale_parts.append(f"Player {player_name} is BANNED")
            rationale_parts.append(f"Ban reason: {ban_reason}")
            rationale_parts.append("DENY ENTRY and alert security immediately")
            priority = "critical"
            recommended_actions = ["deny_entry", "alert_security", "contact_management"]
        
        # Rule 2: High-priority watchlist
        elif watchlist == "high_priority":
            should_execute = True
            rationale_parts.append(f"Player {player_name} on high-priority watchlist")
            rationale_parts.append("Alert security for monitoring")
            priority = "high"
            recommended_actions = ["alert_security", "monitor_behavior"]
        
        # Rule 3: Standard monitoring
        elif watchlist == "standard_monitoring":
            should_execute = False
            rationale_parts.append(f"Player {player_name} on standard monitoring")
            rationale_parts.append("No immediate security alert required")
            priority = "low"
            recommended_actions = ["monitor_behavior"]
        
        # Rule 4: No security concerns
        else:
            rationale_parts.append(f"No security concerns for {player_name}")
            rationale_parts.append("Standard service protocol")
        
        # Build rationale
        rationale = ". ".join(rationale_parts) + "."
        
        # Calculate confidence
        confidence = 1.0
        
        # Log decision
        if host and hasattr(host, "audit"):
            await host.audit.log({
                "decision_type": "security_alert",
                "player_name": player_name,
                "is_banned": is_banned,
                "watchlist": watchlist,
                "should_execute": should_execute,
                "rationale": rationale
            })
        
        return {
            "should_execute": should_execute,
            "rationale": rationale,
            "confidence": confidence,
            "priority": priority,
            "recommended_actions": recommended_actions
        }
    
    async def _decide_alcohol_service(
        self,
        context: Dict[str, Any],
        host: Optional[Any]
    ) -> Dict[str, Any]:
        """
        Decide whether to proactively monitor or intervene with a patron's alcohol consumption.
        
        This demonstrates intelligent risk management based on:
        - Location awareness (player detected at bar)
        - Purchase history tracking (drinks consumed in time period)
        - Historical behavioral patterns (past alcohol-related incidents)
        - Personalized risk assessment (not one-size-fits-all)
        
        Realistic casino behavior:
        - Casinos encourage drinking (loosens inhibitions, increases gambling)
        - BUT must manage legal liability and regulatory compliance
        - Proactive intervention for players with known behavioral patterns
        - Discreet handling to avoid embarrassing high-value customers
        
        Decision criteria:
        - Flag for monitoring if: Player has history of aggression when drinking + currently drinking
        - Proactive intervention if: Pattern detected + 3+ drinks consumed
        - Hard cutoff only if: Player showing actual signs of intoxication/aggression
        - Continue normally if: No behavioral history and moderate consumption
        
        Args:
            context: Workflow context with recognition event, purchase history, incident history
            host: Optional host capabilities
            
        Returns:
            Decision result with monitoring/intervention recommendations
        """
        # Extract recognition event (should show bar location)
        steps = context.get("steps", {})
        recognition_event_step = steps.get("recognition_events", {})
        recognition_event_data = recognition_event_step.get("data", {})
        recognition_event = recognition_event_data.get("event", {})
        
        # Extract purchase history
        purchase_history_step = steps.get("purchase_history", {})
        purchase_history = purchase_history_step.get("data", {})
        
        # Extract player profile
        player_profile_step = steps.get("player_profiles", {})
        player_profile_data = player_profile_step.get("data", {})
        player_profile = player_profile_data.get("profile", {})
        
        # Extract incident history (CRITICAL for behavioral pattern detection)
        incident_check_step = steps.get("incident_histories", {})
        incident_check_data = incident_check_step.get("data", {})
        incident_check = incident_check_data.get("incident_history", {})
        
        # Get player attributes
        player_name = player_profile.get("name", "Unknown")
        player_id = player_profile.get("player_id", "Unknown")
        tier = player_profile.get("tier", "").lower()
        
        # Get location from recognition event
        location = recognition_event.get("location", "unknown")
        
        # Get recent purchases
        recent_purchases = purchase_history.get("recent_purchases", [])
        drinks_count = len([p for p in recent_purchases if p.get("category") == "alcohol"])
        time_window_minutes = purchase_history.get("time_window_minutes", 90)
        
        # CRITICAL: Check incident history for alcohol-related behavioral patterns
        incidents = incident_check.get("incidents", [])
        alcohol_related_incidents = [
            inc for inc in incidents 
            if "alcohol" in inc.get("description", "").lower() 
            or "intoxicated" in inc.get("description", "").lower()
            or "aggressive" in inc.get("description", "").lower()
            or inc.get("type") == "alcohol_related"
        ]
        
        has_alcohol_pattern = len(alcohol_related_incidents) > 0
        
        # Decision logic based on behavioral patterns
        should_execute = False  # should_execute = True means INTERVENE/MONITOR
        rationale_parts = []
        priority = "low"
        recommended_actions = []
        intervention_type = "none"  # none, monitor, discreet_intervention, hard_cutoff
        
        # Rule 1: Player with known alcohol-related behavioral pattern + currently drinking
        if has_alcohol_pattern and drinks_count >= 3:
            should_execute = True
            intervention_type = "discreet_intervention"
            rationale_parts.append(
                f"Player {player_name} has history of {len(alcohol_related_incidents)} alcohol-related incident(s)"
            )
            rationale_parts.append(
                f"Currently at {location} with {drinks_count} drinks consumed in {time_window_minutes} minutes"
            )
            rationale_parts.append(
                "Pattern recognition suggests proactive intervention before escalation"
            )
            priority = "high"
            recommended_actions = [
                "alert_floor_staff",
                "discreet_monitoring",
                "offer_comp_meal",
                "suggest_moving_to_gaming_floor",
                "have_host_engage_player",
                "do_not_embarrass_customer"
            ]
            
            # Add incident details for context
            if alcohol_related_incidents:
                latest_incident = alcohol_related_incidents[0]
                rationale_parts.append(
                    f"Previous incident: {latest_incident.get('description', 'N/A')[:100]}"
                )
        
        # Rule 2: Player with pattern but low current consumption - flag for monitoring
        elif has_alcohol_pattern and drinks_count >= 1:
            should_execute = True
            intervention_type = "monitor"
            rationale_parts.append(
                f"Player {player_name} has history of alcohol-related behavioral issues"
            )
            rationale_parts.append(
                f"Currently at {location} with {drinks_count} drink(s) - flag for monitoring"
            )
            rationale_parts.append(
                "Early awareness allows proactive management before issues arise"
            )
            priority = "medium"
            recommended_actions = [
                "flag_for_monitoring",
                "alert_floor_staff",
                "track_additional_purchases",
                "be_prepared_for_intervention"
            ]
        
        # Rule 3: High consumption without behavioral history - monitor but allow
        elif drinks_count >= 5:
            should_execute = True
            intervention_type = "monitor"
            rationale_parts.append(
                f"Player {player_name} has consumed {drinks_count} drinks in {time_window_minutes} minutes"
            )
            rationale_parts.append(
                "No history of alcohol-related issues, but high consumption warrants monitoring"
            )
            rationale_parts.append(
                "Continue service but watch for signs of intoxication"
            )
            priority = "medium"
            recommended_actions = [
                "monitor_behavior",
                "watch_for_intoxication_signs",
                "continue_service",
                "offer_water_between_drinks"
            ]
        
        # Rule 4: Moderate consumption, no behavioral history - normal service
        elif drinks_count >= 1 and drinks_count <= 4:
            should_execute = False
            intervention_type = "none"
            rationale_parts.append(
                f"Player {player_name} has consumed {drinks_count} drink(s) in {time_window_minutes} minutes"
            )
            rationale_parts.append(
                "No behavioral concerns, moderate consumption - normal service"
            )
            priority = "low"
            recommended_actions = ["continue_normal_service"]
        
        # Rule 5: No drinking detected
        else:
            should_execute = False
            intervention_type = "none"
            rationale_parts.append(
                f"Player {player_name} detected at {location} with minimal/no alcohol consumption"
            )
            rationale_parts.append("No intervention required")
            priority = "low"
            recommended_actions = ["continue_normal_service"]
        
        # Add VIP consideration for discreet handling
        if tier in PLATINUM_DIAMOND_TIERS and should_execute:
            rationale_parts.append(
                f"IMPORTANT: {tier.capitalize()} tier player - handle with maximum discretion"
            )
            rationale_parts.append(
                "Use VIP host for intervention, avoid public embarrassment"
            )
            if "notify_vip_host" not in recommended_actions:
                recommended_actions.insert(0, "notify_vip_host")
            if "maximum_discretion" not in recommended_actions:
                recommended_actions.append("maximum_discretion")
        
        # Build rationale
        rationale = ". ".join(rationale_parts) + "."
        
        # Calculate confidence based on data quality
        confidence = 0.95 if has_alcohol_pattern else 0.85
        
        # Log decision
        if host and hasattr(host, "audit"):
            await host.audit.log({
                "decision_type": "alcohol_service",
                "player_name": player_name,
                "player_id": player_id,
                "location": location,
                "drinks_count": drinks_count,
                "time_window_minutes": time_window_minutes,
                "has_alcohol_pattern": has_alcohol_pattern,
                "alcohol_incidents_count": len(alcohol_related_incidents),
                "intervention_type": intervention_type,
                "should_execute": should_execute,
                "rationale": rationale
            })
        
        return {
            "should_execute": should_execute,
            "rationale": rationale,
            "confidence": confidence,
            "priority": priority,
            "recommended_actions": recommended_actions,
            "intervention_type": intervention_type,
            "drinks_consumed": drinks_count,
            "time_window_minutes": time_window_minutes,
            "has_behavioral_pattern": has_alcohol_pattern,
            "incident_count": len(alcohol_related_incidents)
        }
    
    def _calculate_days_since_visit(self, last_visit: Optional[str]) -> Optional[int]:
        """
        Calculate days since last visit.
        
        Args:
            last_visit: Last visit date string (ISO format)
            
        Returns:
            Number of days since last visit, or None if not available
        """
        if not last_visit:
            return None
        
        try:
            # Parse last visit date
            last_visit_date = datetime.fromisoformat(last_visit.replace("Z", "+00:00"))
            
            # Get current date
            now = datetime.now(timezone.utc)
            
            # Calculate difference
            delta = now - last_visit_date
            return delta.days
        except Exception as e:
            logger.warning(f"Failed to calculate days since visit: {e}")
            return None
    
    def _check_special_occasions(self, player_profile: Dict[str, Any]) -> bool:
        """
        Check if player has a special occasion within 7 days.
        
        Args:
            player_profile: Player profile data
            
        Returns:
            True if special occasion within 7 days, False otherwise
        """
        special_dates = player_profile.get("special_dates", [])
        if not special_dates:
            return False
        
        try:
            now = datetime.now(timezone.utc)
            current_year = now.year
            
            for occasion in special_dates:
                date_str = occasion.get("date", "")
                if not date_str:
                    continue
                
                # Parse date (format: YYYY-MM-DD)
                occasion_date = datetime.fromisoformat(date_str)
                
                # Replace year with current year for comparison
                occasion_this_year = occasion_date.replace(year=current_year)
                
                # Calculate days until occasion
                delta = occasion_this_year - now
                days_until = delta.days
                
                # Check if within 7 days (including past 7 days for recent celebrations)
                if -7 <= days_until <= 7:
                    return True
            
            return False
        except Exception as e:
            logger.warning(f"Failed to check special occasions: {e}")
            return False
    
    def _find_matching_table(
        self,
        table_availability: Dict[str, Any],
        preferred_game: Optional[str],
        table_preference: str,
        table_limits: Dict[str, Any]
    ) -> Optional[str]:
        """
        Find a matching available table based on player preferences.
        
        Args:
            table_availability: Table availability data
            preferred_game: Player's preferred game
            table_preference: Player's table preference (quiet, private, etc.)
            table_limits: Player's min/max bet limits
            
        Returns:
            Table ID if match found, None otherwise
        """
        tables = table_availability.get("tables", [])
        if not tables:
            return None
        
        min_bet = table_limits.get("min", 0)
        max_bet = table_limits.get("max", float("inf"))
        
        # Look for matching tables
        for table in tables:
            # Check if table is available
            if table.get("status") != "available":
                continue
            
            # Check if game matches
            if preferred_game and table.get("game") != preferred_game:
                continue
            
            # Check if limits are compatible
            table_min = table.get("min_bet", 0)
            table_max = table.get("max_bet", float("inf"))
            
            if table_min > max_bet or table_max < min_bet:
                continue
            
            # Check if atmosphere matches preference
            atmosphere = table.get("atmosphere", "")
            if table_preference == "quiet_tables" and atmosphere != "quiet":
                continue
            if table_preference == "private_rooms" and "private" not in atmosphere:
                continue
            if table_preference == "high_limit_private" and atmosphere != "exclusive":
                continue
            
            # Found a match
            return table.get("table_id")
        
        return None
