"""API endpoints for LLM Side-Call configuration and operations."""

import logging

from fastapi import APIRouter, Body, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user, require_admin
from ..core.config import ConfigurationManager, get_config_manager_dependency
from ..core.database import get_db
from ..core.response import ShuResponse, create_error_response, create_success_response
from ..schemas.envelope import SuccessResponse
from ..schemas.side_call import (
    AutoRenameLockStatus,
    ConversationAutomationRequest,
    ConversationRenamePayload,
    ConversationSummaryPayload,
    SideCallConfigRequest,
    SideCallConfigResponse,
    SideCallModelResponse,
)
from ..services.chat_service import ChatService
from ..services.conversation_automation_service import ConversationAutomationService
from ..services.side_call_service import SideCallService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/side-calls", tags=["side-calls"])


def get_side_call_service(
    db: AsyncSession = Depends(get_db),
    config_manager=Depends(get_config_manager_dependency),
) -> SideCallService:
    """Dependency injection for SideCallService."""
    return SideCallService(db, config_manager)


def _build_config_response(model_config, message: str) -> SideCallConfigResponse:
    """Build a consistent config response."""
    if not model_config:
        return SideCallConfigResponse(configured=False, side_call_model_config=None, message=message)

    return SideCallConfigResponse(
        configured=True,
        side_call_model_config=SideCallModelResponse(
            id=model_config.id,
            name=model_config.name,
            description=model_config.description,
            provider_name=(model_config.llm_provider.name if model_config.llm_provider else None),
            model_name=model_config.model_name,
            functionalities=getattr(model_config, "functionalities", {}) or {},
        ),
        message=message,
    )


@router.get("/config", response_model=SideCallConfigResponse)
async def get_side_call_config(
    current_user: User = Depends(require_admin),
    side_call_service: SideCallService = Depends(get_side_call_service),
):
    """Get the current side-call configuration."""
    try:
        # Get the designated side-call model
        model_config = await side_call_service.get_side_call_model()

        return ShuResponse.success(
            _build_config_response(
                model_config,
                ("No side-call model is currently configured" if not model_config else "Side-call model is configured"),
            )
        )

    except Exception as e:
        logger.error(f"Failed to get side-call config: {e}")
        return create_error_response(
            code="INTERNAL_ERROR",
            message="Failed to retrieve side-call configuration",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.post("/config", response_model=SideCallConfigResponse)
async def set_side_call_config(
    request: SideCallConfigRequest,
    current_user: User = Depends(require_admin),
    side_call_service: SideCallService = Depends(get_side_call_service),
    db: AsyncSession = Depends(get_db),
):
    """Set the designated side-call model configuration.

    Requires admin privileges.
    """
    try:
        # Set the side-call model
        success = await side_call_service.set_side_call_model(
            model_config_id=request.model_config_id, user_id=current_user.id
        )

        if not success:
            return create_error_response(
                code="VALIDATION_ERROR",
                message=("Failed to set side-call model. Verify the model exists and is designated for side-calls."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Get the updated configuration
        model_config = await side_call_service.get_side_call_model()

        return ShuResponse.success(_build_config_response(model_config, "Side-call model configured successfully"))

    except Exception as e:
        logger.error(f"Failed to set side-call config: {e}")
        return create_error_response(
            code="INTERNAL_ERROR",
            message="Failed to configure side-call model",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


async def _run_conversation_automation(
    *,
    conversation_id: str,
    request: ConversationAutomationRequest | None,
    current_user: User,
    db: AsyncSession,
    config_manager: ConfigurationManager,
    executor,
    action_name: str,
):
    """Shared helper to load a conversation, validate ownership, and execute automation."""
    chat_service = ChatService(db, config_manager)
    conversation = await chat_service.get_conversation_by_id(conversation_id)
    if not conversation:
        return create_error_response(
            code="CONVERSATION_NOT_FOUND",
            message=f"Conversation '{conversation_id}' not found",
            status_code=404,
        )
    if conversation.user_id != current_user.id:
        return create_error_response(
            code="UNAUTHORIZED",
            message="You do not have access to this conversation",
            status_code=403,
        )

    automation_service = ConversationAutomationService(db, config_manager)
    payload = request or ConversationAutomationRequest()

    try:
        result = await executor(
            automation_service,
            conversation,
            payload.timeout_ms,
            current_user.id,
            payload.fallback_user_message,
        )
        return create_success_response(data=result)

    except RuntimeError as exc:
        logger.error(
            "%s side-call failed for conversation %s: %s",
            action_name,
            conversation_id,
            exc,
        )
        return create_error_response(
            code="SIDE_CALL_FAILED",
            message=str(exc),
            status_code=502,
        )
    except Exception:
        logger.exception("Unexpected error during %s for conversation %s", action_name, conversation_id)
        return create_error_response(
            code="INTERNAL_ERROR",
            message=f"Failed to {action_name.replace('_', ' ')} conversation",
            status_code=500,
        )


@router.post(
    "/summary/{conversation_id}",
    response_model=SuccessResponse[ConversationSummaryPayload],
    summary="Generate or refresh conversation summary",
    description="Runs the side-call summarization flow for a conversation and persists the result.",
)
async def generate_conversation_summary(
    conversation_id: str = Path(..., description="Conversation ID"),
    request: ConversationAutomationRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    return await _run_conversation_automation(
        conversation_id=conversation_id,
        request=request,
        current_user=current_user,
        db=db,
        config_manager=config_manager,
        action_name="generate_summary",
        executor=lambda svc, conv, timeout_ms, user_id, _: svc.generate_summary(
            conv,
            timeout_ms=timeout_ms,
            current_user_id=user_id,
        ),
    )


@router.post(
    "/auto-rename/{conversation_id}",
    response_model=SuccessResponse[ConversationRenamePayload],
    summary="Automatically rename a conversation",
    description="Generates a concise title for the conversation using the side-call model.",
)
async def auto_rename_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    request: ConversationAutomationRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    return await _run_conversation_automation(
        conversation_id=conversation_id,
        request=request,
        current_user=current_user,
        db=db,
        config_manager=config_manager,
        action_name="auto_rename",
        executor=lambda svc, conv, timeout_ms, user_id, fallback: svc.auto_rename(
            conv,
            timeout_ms=timeout_ms,
            current_user_id=user_id,
            fallback_user_message=fallback,
        ),
    )


@router.post(
    "/auto-rename/{conversation_id}/unlock",
    response_model=SuccessResponse[AutoRenameLockStatus],
    summary="Unlock auto-rename for a conversation",
    description="Clears the manual title lock so subsequent auto-rename calls can proceed.",
)
async def unlock_auto_rename(
    conversation_id: str = Path(..., description="Conversation ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    chat_service = ChatService(db, config_manager)
    conversation = await chat_service.get_conversation_by_id(conversation_id)
    if not conversation:
        return create_error_response(
            code="CONVERSATION_NOT_FOUND",
            message=f"Conversation '{conversation_id}' not found",
            status_code=404,
        )
    if conversation.user_id != current_user.id:
        return create_error_response(
            code="UNAUTHORIZED",
            message="You do not have access to this conversation",
            status_code=403,
        )

    meta = dict(conversation.meta or {})
    if meta.get("title_locked"):
        meta["title_locked"] = False
        conversation.meta = meta
        await db.commit()
        await db.refresh(conversation)

    return create_success_response(data=AutoRenameLockStatus(title_locked=bool(conversation.meta.get("title_locked"))))
