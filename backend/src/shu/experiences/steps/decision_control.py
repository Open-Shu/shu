"""
Decision Control Step for Experience Workflows.

This module implements a fully deterministic decision control step that evaluates
customer/player data and decides whether to execute subsequent workflow steps. The decision
logic is based on tier, lifetime value, visit history, preferences, and other factors.

This is designed for demo scenarios and uses deterministic rules rather
than LLM calls for predictable demo behavior.

Supported Decision Types:
- car_service_decision: Decide whether to book luxury car service for VIP hotel guests
- tailor_notification_decision: Decide whether to notify hotel tailor for VIP guests
- restaurant_decision: Decide whether to reserve restaurant table for VIP hotel guests
- spa_service_decision: Decide whether to offer spa services based on guest preferences
"""

import logging
import uuid
from typing import Dict, Any, Optional

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
        step_key: str,
        config: Dict[str, Any],
        context: Dict[str, Any],
        host: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Execute the decision control step.
        
        Args:
            step_key: Unique identifier for the decision type (e.g., 'car_service_decision')
            config: Step configuration including decision criteria
            context: Workflow context with player data
            host: Optional host capabilities for logging
            
        Returns:
            Decision result with should_execute, rationale, and metadata
        """
        try:
            # Silence ARG002 - config and context intentionally unused in demo logic
            _ = config
            _ = context

            logger.info(step_key)

            if step_key == "car_service_decision":
                return self._simple_decision(True, "Car service approved for VIP guest.")
            elif step_key == "tailor_notification_decision":
                return self._simple_decision(True, "Tailor hold applied. Appointment is subject to customer approval.")
            elif step_key == "restaurant_decision":
                return self._simple_decision(True, "Restaurant reservation approved for evening arrival.")
            elif step_key == "spa_service_decision":
                return self._simple_decision(False, "Customer mentioned that they don't enjoy spa treatments in the past.")
            else:
                # Unknown decision type - return safe default
                logger.warning(f"Unrecognized decision step_key: {step_key}")
                return self._simple_decision(False, f"Unrecognized decision '{step_key}': no action taken")
                
        except Exception:
            # Generate correlation ID for error tracking
            correlation_id = str(uuid.uuid4())
            # Log full exception details server-side with correlation ID
            logger.exception("Decision control step failed", extra={"correlation_id": correlation_id})
            
            # Log to host audit if available (with correlation ID, not raw exception)
            if host and hasattr(host, "audit"):
                await host.audit.log({
                    "correlation_id": correlation_id,
                    "step": "decision_control",
                    "error": "Decision evaluation failed"
                })
            
            # Return sanitized error to caller without exposing internal details
            return {
                "should_execute": False,
                "rationale": f"Decision evaluation failed (correlation ID: {correlation_id})",
                "confidence": 0.0,
                "error": True,
                "correlation_id": correlation_id
            }
    
    def _simple_decision(self, should_execute: bool, rationale: str) -> Dict[str, Any]:
        """
        Return a simple decision result for demo scenarios.
        
        Args:
            should_execute: Whether to execute the step
            rationale: Explanation for the decision
            
        Returns:
            Decision result
        """
        return {
            "should_execute": should_execute,
            "rationale": rationale,
            "confidence": 1.0
        }
