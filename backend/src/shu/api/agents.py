"""
Agent endpoints v0 (MVP): Morning Briefing orchestrator.
"""
from __future__ import annotations
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from time import monotonic

from ..core.logging import get_logger

from ..core.response import ShuResponse
from ..schemas.envelope import SuccessResponse
from ..api.dependencies import get_db
from ..auth.rbac import get_current_user
from ..auth.models import User
from ..agent.orchestrator import MorningBriefingOrchestrator
from ..core.config import ConfigurationManager, get_config_manager_dependency

router = APIRouter(prefix="/agents", tags=["agents"])
logger = get_logger(__name__)


class MorningBriefingRequest(BaseModel):
    model_configuration_id: Optional[str] = Field(
        None,
        description="Model configuration to use for briefing (optional; server may fall back)"
    )
    gmail_digest: Optional[Dict[str, Any]] = None
    calendar_events: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parameters for the calendar_events plugin",
    )
    # Back-compat shim so older clients can still send calendar_digest while the frontend is updated.
    calendar_digest: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Deprecated – use calendar_events instead",
    )
    kb_insights: Optional[Dict[str, Any]] = None
    gchat_digest: Optional[Dict[str, Any]] = None


@router.post(
    "/morning-briefing/run",
    response_model=SuccessResponse[Dict[str, Any]],
    summary="Run Morning Briefing (MVP)",
    description="Executes minimal orchestrator: tools + single LLM synthesis using a Model Configuration"
)
async def run_morning_briefing(
    request: MorningBriefingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    start = monotonic()
    logger.info("Morning Briefing requested", extra={"user_id": current_user.id})
    orchestrator = MorningBriefingOrchestrator(db, config_manager)

    # Provide sane defaults for tools: use the logged-in user's email when not specified
    calendar_params = request.calendar_events or request.calendar_digest or {}
    p: Dict[str, Any] = {
        "gmail_digest": request.gmail_digest or {},
        "calendar_events": calendar_params,
        "kb_insights": request.kb_insights or {},
        "gchat_digest": request.gchat_digest or {},
    }
    p["gmail_digest"].setdefault("user_email", current_user.email)
    p["calendar_events"].setdefault("user_email", current_user.email)
    p["gchat_digest"].setdefault("user_email", current_user.email)

    try:
        result = await orchestrator.run(
            user_id=current_user.id,
            model_configuration_id=request.model_configuration_id,
            params=p,
            current_user=current_user,
        )
        duration = monotonic() - start
        logger.info("Morning Briefing completed", extra={"duration_s": round(duration, 3)})
        return ShuResponse.success(result)
    except Exception as e:
        duration = monotonic() - start
        logger.error("Morning Briefing failed", extra={"error": str(e), "duration_s": round(duration, 3)})
        raise
