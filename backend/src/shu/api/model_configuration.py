"""
Model Configuration API endpoints for Shu.

This module provides REST API endpoints for managing model configurations -
the foundational abstraction that combines base models + prompts + optional
knowledge bases into user-facing configurations.
"""

import logging
from typing import List, Optional, Dict, Any, Iterable, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.rbac import require_power_user, require_regular_user
from ..auth.models import User, UserRole
from ..core.config import get_config_manager_dependency, ConfigurationManager
from ..core.exceptions import ShuException, LLMConfigurationError
from ..core.response import ShuResponse
from ..services.chat_service import ChatService
from ..schemas.query import RagRewriteMode
from ..services.model_configuration_service import ModelConfigurationService
from ..services.side_call_service import SideCallService
from ..services.ocr_call_service import OcrCallService
from ..schemas.model_configuration import (
    ModelConfigurationCreate,
    ModelConfigurationUpdate,
    ModelConfigurationResponse,
    ModelConfigurationList,
    ModelConfigurationTest,
    ModelConfigurationTestResponse,
    ModelConfigKBPromptAssignment,
    ModelConfigKBPromptResponse,
    ModelConfigKBPromptList
)
from ..schemas.envelope import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-configurations", tags=["Model Configurations"])


def _create_caller_services(db: AsyncSession) -> SideCallService:
    """Instantiate a SideCallService for the current request."""
    return (
        SideCallService(db, get_config_manager_dependency()),
        OcrCallService(db, get_config_manager_dependency()),
    )


async def _get_side_call_model_id(side_call_service: SideCallService) -> Optional[str]:
    """Return the configured side-call model ID, if any."""
    side_call_model = await side_call_service.get_side_call_model()
    return side_call_model.id if side_call_model else None


async def _get_ocr_call_model_id(ocr_call_service: OcrCallService) -> Optional[str]:
    """Return the configured OCR call model ID, if any."""
    ocr_call_model = await ocr_call_service.get_ocr_call_model()
    return ocr_call_model.id if ocr_call_model else None


def _apply_side_call_flag(
    configs: Union[ModelConfigurationResponse, Iterable[ModelConfigurationResponse], None],
    side_call_model_id: Optional[str],
) -> None:
    """Mark configuration response objects with the is_side_call flag."""
    if not configs:
        return

    if isinstance(configs, ModelConfigurationResponse):
        configs.is_side_call = configs.id == side_call_model_id
        return

    for config in configs:
        if config is not None:
            config.is_side_call = config.id == side_call_model_id


def _apply_ocr_call_flag(
    configs: Union[ModelConfigurationResponse, Iterable[ModelConfigurationResponse], None],
    ocr_call_model_id: Optional[str],
) -> None:
    """Mark configuration response objects with the is_ocr_call flag."""
    if not configs:
        return

    if isinstance(configs, ModelConfigurationResponse):
        configs.is_ocr_call = configs.id == ocr_call_model_id
        return

    for config in configs:
        if config is not None:
            config.is_ocr_call = config.id == ocr_call_model_id


def _apply_caller_flags(
    configs: Union[ModelConfigurationResponse, Iterable[ModelConfigurationResponse], None],
    side_call_model_id: Optional[str],
    ocr_call_model_id: Optional[str],
) -> None:
    """Apply all caller designation flags to configuration responses."""
    _apply_side_call_flag(configs, side_call_model_id)
    _apply_ocr_call_flag(configs, ocr_call_model_id)


@router.post(
    "",
    response_model=SuccessResponse[ModelConfigurationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create Model Configuration",
    description="Create a new model configuration that combines base model + prompt + optional knowledge bases"
)
async def create_model_configuration(
    config_data: ModelConfigurationCreate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new model configuration."""
    try:
        service = ModelConfigurationService(db)
        side_call_service, ocr_call_service = _create_caller_services(db)
        config = await service.create_model_configuration(config_data)

        # If this model is marked for side calls, update the system setting
        if getattr(config_data, "is_side_call_model", False):
            await side_call_service.set_side_call_model(config.id, current_user.id)

        # If this model is marked for OCR calls, update the system setting
        if getattr(config_data, "is_ocr_call_model", False):
            await ocr_call_service.set_ocr_call_model(config.id, current_user.id)

        # Reload with relationships for response serialization
        config_with_relationships = await service.get_model_configuration(
            config.id, include_relationships=True
        )

        side_call_model_id = await _get_side_call_model_id(side_call_service)
        ocr_call_model_id = await _get_ocr_call_model_id(ocr_call_service)

        # Convert to response format
        response_data = service._to_response(config_with_relationships)
        _apply_caller_flags(response_data, side_call_model_id, ocr_call_model_id)

        # Use ShuResponse to ensure single-wrapped JSON envelope
        return ShuResponse.created(response_data)

    except ShuException as e:
        logger.error(f"Failed to create model configuration: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error creating model configuration: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create model configuration"
        )


@router.get(
    "",
    response_model=SuccessResponse[ModelConfigurationList],
    summary="List Model Configurations",
    description="Get a paginated list of model configurations"
)
async def list_model_configurations(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=100, description="Items per page"),
    active_only: bool = Query(True, description="Only return active configurations"),
    is_active: Optional[bool] = Query(None, description="Filter by specific active status (overrides active_only)"),
    created_by: Optional[str] = Query(None, description="Filter by creator user ID"),
    include_relationships: bool = Query(True, description="Include related entities"),
    current_user: User = Depends(require_regular_user),
    db: AsyncSession = Depends(get_db)
):
    """List model configurations with pagination and filtering."""
    try:
        service = ModelConfigurationService(db)
        side_call_service, ocr_call_service = _create_caller_services(db)

        is_power_user = current_user.has_role(UserRole.POWER_USER)
        effective_include_relationships = include_relationships # default to include relationships
        
        # Regular users should only ever see active configurations.
        if not is_power_user:
            is_active = True  # Only set is_active, since it overrides active_only below
            effective_include_relationships = False # regular users should not see relationships

        # Handle is_active parameter (overrides active_only)
        if is_active is not None:
            # When is_active is specified, use it directly
            effective_active_only = is_active
        else:
            # Fall back to active_only parameter
            effective_active_only = active_only

        result = await service.list_model_configurations(
            page=page,
            per_page=per_page,
            active_only=effective_active_only,
            is_active_filter=is_active,
            created_by=created_by,
            include_relationships=effective_include_relationships,
            current_user=current_user
        )

        side_call_model_id = await _get_side_call_model_id(side_call_service)
        ocr_call_model_id = await _get_ocr_call_model_id(ocr_call_service)
        if hasattr(result, "items"):
            _apply_caller_flags(result.items, side_call_model_id, ocr_call_model_id)
            if not is_power_user:
                for cfg in result.items:
                    if cfg is None:
                        continue
                    cfg.knowledge_bases = []
                    cfg.kb_prompts = {}
                    cfg.knowledge_base_count = 0
                    cfg.has_knowledge_bases = False

        return SuccessResponse(data=result)

    except ShuException as e:
        logger.error(f"Failed to list model configurations: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error listing model configurations: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list model configurations"
        )


@router.get(
    "/{config_id}",
    response_model=SuccessResponse[ModelConfigurationResponse],
    summary="Get Model Configuration",
    description="Get a specific model configuration by ID"
)
async def get_model_configuration(
    config_id: str,
    include_relationships: bool = Query(True, description="Include related entities"),
    current_user: User = Depends(require_regular_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a model configuration by ID."""
    try:
        service = ModelConfigurationService(db)
        config = await service.get_model_configuration(config_id, include_relationships, current_user)
        
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )

        if not current_user.has_role(UserRole.POWER_USER) and not getattr(config, "is_active", False):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )
        
        side_call_service, ocr_call_service = _create_caller_services(db)
        response_data = service._to_response(config)
        side_call_model_id = await _get_side_call_model_id(side_call_service)
        ocr_call_model_id = await _get_ocr_call_model_id(ocr_call_service)
        _apply_caller_flags(response_data, side_call_model_id, ocr_call_model_id)
        
        return SuccessResponse(data=response_data)
        
    except HTTPException:
        raise
    except ShuException as e:
        logger.error(f"Failed to get model configuration {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error getting model configuration {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get model configuration"
        )


@router.put(
    "/{config_id}",
    response_model=SuccessResponse[ModelConfigurationResponse],
    summary="Update Model Configuration",
    description="Update a model configuration"
)
async def update_model_configuration(
    config_id: str,
    update_data: ModelConfigurationUpdate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a model configuration."""
    try:
        service = ModelConfigurationService(db)
        side_call_service, ocr_call_service = _create_caller_services(db)

        # Capture current caller models before applying updates so we can
        # decide whether this update should clear the designations.
        existing_side_call_model_id = await _get_side_call_model_id(side_call_service)
        existing_ocr_call_model_id = await _get_ocr_call_model_id(ocr_call_service)

        config = await service.update_model_configuration(config_id, update_data)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )

        # Handle side-call designation
        is_side_call_flag = getattr(update_data, "is_side_call_model", None)
        if is_side_call_flag is True:
            await side_call_service.set_side_call_model(config_id, current_user.id)
        elif (
            is_side_call_flag is False
            and existing_side_call_model_id is not None
            and existing_side_call_model_id == config_id
        ):
            await side_call_service.clear_side_call_model(current_user.id)

        # Handle OCR-call designation
        is_ocr_call_flag = getattr(update_data, "is_ocr_call_model", None)
        if is_ocr_call_flag is True:
            await ocr_call_service.set_ocr_call_model(config_id, current_user.id)
        elif (
            is_ocr_call_flag is False
            and existing_ocr_call_model_id is not None
            and existing_ocr_call_model_id == config_id
        ):
            await ocr_call_service.clear_ocr_call_model(current_user.id)

        # Reload with relationships to ensure they're available for serialization
        config_with_relationships = await service.get_model_configuration(
            config.id, include_relationships=True
        )

        response_data = service._to_response(config_with_relationships)
        side_call_model_id = await _get_side_call_model_id(side_call_service)
        ocr_call_model_id = await _get_ocr_call_model_id(ocr_call_service)
        _apply_caller_flags(response_data, side_call_model_id, ocr_call_model_id)

        return SuccessResponse(data=response_data)

    except HTTPException:
        raise
    except ShuException as e:
        logger.error(f"Failed to update model configuration {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error updating model configuration {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update model configuration"
        )


@router.delete(
    "/{config_id}",
    status_code=204,
    summary="Delete Model Configuration",
    description="Delete a model configuration"
)
async def delete_model_configuration(
    config_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a model configuration."""
    try:
        service = ModelConfigurationService(db)
        deleted = await service.delete_model_configuration(config_id)
        
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )
        
        return ShuResponse.no_content()
        
    except HTTPException:
        raise
    except ShuException as e:
        logger.error(f"Failed to delete model configuration {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error deleting model configuration {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete model configuration"
        )


@router.post(
    "/{config_id}/test",
    response_model=SuccessResponse[ModelConfigurationTestResponse],
    summary="Test Model Configuration",
    description="Test a model configuration with a sample message"
)
async def test_model_configuration(
    config_id: str,
    test_data: ModelConfigurationTest,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Test a model configuration with a sample message."""
    try:

        service = ModelConfigurationService(db)
        config = await service.get_model_configuration(config_id, include_relationships=True)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )

        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.create_conversation(current_user.id, config.id)

        response = ""
        error = None
        async for event in await chat_service.send_message(
            conversation_id=conversation.id,
            user_message=test_data.test_message,
            current_user=current_user,
            rag_rewrite_mode=RagRewriteMode.NO_RAG,
        ):
            if event.type == "final_message":
                logger.info(event.content)
                response = event.content.get("content")
            if event.type == "error":
                error = event.content

        await chat_service.delete_conversation(conversation.id)

        # Handle error case - return the error message instead of raising
        if error:
            response_data = ModelConfigurationTestResponse(
                success=False,
                response=None,
                error=error,
                model_used=f"{config.llm_provider.name}/{config.model_name}",
                prompt_applied=config.prompt is not None,
                knowledge_bases_used=[kb.name for kb in config.knowledge_bases] if test_data.include_knowledge_bases else [],
                response_time_ms=0,
                token_usage={"input_tokens": 0, "output_tokens": 0},
                metadata={}
            )
            return SuccessResponse(data=response_data)

        if not response:
            raise HTTPException(status_code=500, detail="No response generated for model configuration test")

        response_data = ModelConfigurationTestResponse(
            success=True,
            response=response,
            error=None,
            model_used=f"{config.llm_provider.name}/{config.model_name}",
            prompt_applied=config.prompt is not None,
            knowledge_bases_used=[kb.name for kb in config.knowledge_bases] if test_data.include_knowledge_bases else [],
            response_time_ms=150,
            token_usage={"input_tokens": 25, "output_tokens": 15},
            metadata={}
        )

        return SuccessResponse(data=response_data)

    except HTTPException:
        # Re-raise HTTPException as-is so FastAPI can handle it
        raise
    except LLMConfigurationError as e:
        logger.error("Failed to test model configuration %s: %s", config_id, e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e) )
    except ShuException as e:
        logger.error("Failed to test model configuration %s: %s", config_id, e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error testing model configuration %s: %s", config_id, e, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to test model configuration")


# KB Prompt Assignment Endpoints

@router.get(
    "/{config_id}/kb-prompts",
    response_model=SuccessResponse[Dict[str, Dict[str, Any]]],
    summary="Get KB Prompts for Model Configuration",
    description="Get all KB-specific prompt assignments for a model configuration"
)
async def get_model_config_kb_prompts(
    config_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all KB prompt assignments for a model configuration."""
    try:
        service = ModelConfigurationService(db)
        kb_prompts = await service.get_kb_prompts(config_id)

        return SuccessResponse(data=kb_prompts)

    except ShuException as e:
        logger.error(f"Failed to get KB prompts for model config {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error getting KB prompts for model config {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get KB prompts"
        )


@router.post(
    "/{config_id}/kb-prompts",
    response_model=SuccessResponse[ModelConfigKBPromptResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Assign KB Prompt",
    description="Assign a prompt to a specific knowledge base for a model configuration"
)
async def assign_kb_prompt(
    config_id: str,
    assignment: ModelConfigKBPromptAssignment,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Assign a prompt to a KB for a model configuration."""
    try:
        service = ModelConfigurationService(db)
        kb_prompt_assignment = await service.assign_kb_prompt(
            model_config_id=config_id,
            knowledge_base_id=assignment.knowledge_base_id,
            prompt_id=assignment.prompt_id
        )

        # Convert to response schema manually to avoid async relationship issues
        response_data = ModelConfigKBPromptResponse(
            id=kb_prompt_assignment.id,
            model_configuration_id=kb_prompt_assignment.model_configuration_id,
            knowledge_base_id=kb_prompt_assignment.knowledge_base_id,
            prompt_id=kb_prompt_assignment.prompt_id,
            is_active=kb_prompt_assignment.is_active,
            assigned_at=kb_prompt_assignment.assigned_at,
            created_at=kb_prompt_assignment.created_at,
            updated_at=kb_prompt_assignment.updated_at,
            # Skip relationships to avoid async issues - they can be populated separately if needed
            knowledge_base=None,
            prompt=None
        )
        return SuccessResponse(data=response_data)

    except ShuException as e:
        logger.error(f"Failed to assign KB prompt for model config {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error assigning KB prompt for model config {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign KB prompt"
        )


@router.delete(
    "/{config_id}/kb-prompts/{knowledge_base_id}",
    response_model=SuccessResponse[Dict[str, bool]],
    summary="Remove KB Prompt Assignment",
    description="Remove a KB prompt assignment from a model configuration"
)
async def remove_kb_prompt(
    config_id: str,
    knowledge_base_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove a KB prompt assignment from a model configuration."""
    try:
        service = ModelConfigurationService(db)
        removed = await service.remove_kb_prompt(
            model_config_id=config_id,
            knowledge_base_id=knowledge_base_id
        )

        return SuccessResponse(data={"removed": removed})

    except ShuException as e:
        logger.error(f"Failed to remove KB prompt for model config {config_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error removing KB prompt for model config {config_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove KB prompt"
        )
