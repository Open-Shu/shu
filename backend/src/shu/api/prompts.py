"""Generalized Prompt API endpoints for Shu.

This module provides REST API endpoints for managing prompts across
different entity types (knowledge bases, LLM models, agents, etc.).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_power_user
from ..core.database import get_db
from ..core.exceptions import ConflictError, ShuException, ValidationError
from ..core.response import ShuResponse
from ..schemas.prompt import (
    EntityTypeEnum,
    PromptAssignmentCreate,
    PromptAssignmentResponse,
    PromptCreate,
    PromptListResponse,
    PromptQueryParams,
    PromptResponse,
    PromptSystemStats,
    PromptUpdate,
)
from ..services.prompt_service import PromptAlreadyExistsError, PromptNotFoundError, PromptService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.post(
    "/",
    response_model=PromptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new prompt",
    description="Create a new prompt for a specific entity type.",
)
async def create_prompt(
    prompt_data: PromptCreate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new prompt."""
    try:
        service = PromptService(db)
        prompt = await service.create_prompt(prompt_data)

        logger.info(f"Created prompt '{prompt.name}' for entity type '{prompt.entity_type}'")
        return ShuResponse.created(prompt.model_dump())

    except PromptAlreadyExistsError as e:
        logger.warning(f"Prompt creation failed: {e}")
        return ShuResponse.error(message=str(e), code="PROMPT_ALREADY_EXISTS", status_code=409)
    except Exception as e:
        logger.error(f"Unexpected error creating prompt: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


# Non-slash root alias for create (tolerate '/prompts' without trailing slash)
@router.post("", include_in_schema=False)
async def create_prompt_no_slash(
    prompt_data: PromptCreate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_prompt(prompt_data, current_user, db)


@router.get(
    "/",
    response_model=PromptListResponse,
    summary="List prompts",
    description="List prompts with filtering and pagination.",
)
async def list_prompts(
    entity_type: EntityTypeEnum | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    search: str | None = Query(None, description="Search in name and description"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """List prompts with filtering and pagination."""
    try:
        service = PromptService(db)
        params = PromptQueryParams(
            entity_type=entity_type,
            entity_id=entity_id,
            is_active=is_active,
            search=search,
            limit=limit,
            offset=offset,
        )

        result = await service.list_prompts(params)
        logger.info(f"Listed {len(result.items)} prompts (total: {result.total})")

        return ShuResponse.success(result.model_dump())

    except Exception as e:
        logger.error(f"Unexpected error listing prompts: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


# Non-slash root alias for list
@router.get("", include_in_schema=False)
async def list_prompts_no_slash(
    entity_type: EntityTypeEnum | None = Query(None, description="Filter by entity type"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    search: str | None = Query(None, description="Search in name and description"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_prompts(entity_type, entity_id, is_active, search, limit, offset, current_user, db)


@router.get(
    "/{prompt_id}",
    response_model=PromptResponse,
    summary="Get prompt by ID",
    description="Retrieve a specific prompt by its ID.",
)
async def get_prompt(
    prompt_id: str = Path(..., description="Prompt ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a prompt by ID."""
    try:
        service = PromptService(db)
        prompt = await service.get_prompt(prompt_id)

        if not prompt:
            return ShuResponse.error(message=f"Prompt {prompt_id} not found", code="PROMPT_NOT_FOUND", status_code=404)

        logger.info(f"Retrieved prompt '{prompt.name}' (ID: {prompt_id})")
        return ShuResponse.success(prompt.model_dump())

    except Exception as e:
        logger.error(f"Unexpected error getting prompt {prompt_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.put(
    "/{prompt_id}",
    response_model=PromptResponse,
    summary="Update prompt",
    description="Update an existing prompt.",
)
async def update_prompt(
    prompt_id: str = Path(..., description="Prompt ID"),
    prompt_data: PromptUpdate = ...,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing prompt."""
    try:
        service = PromptService(db)

        # Check if this is a system default prompt
        existing_prompt = await service.get_prompt(prompt_id)
        if existing_prompt and existing_prompt.is_system_default:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System default prompts cannot be modified",
            )

        prompt = await service.update_prompt(prompt_id, prompt_data)

        logger.info(f"Updated prompt '{prompt.name}' (ID: {prompt_id})")
        return ShuResponse.success(prompt.model_dump())

    except PromptNotFoundError as e:
        logger.warning(f"Prompt update failed: {e}")
        return ShuResponse.error(message=str(e), code="PROMPT_NOT_FOUND", status_code=404)
    except PromptAlreadyExistsError as e:
        logger.warning(f"Prompt update failed: {e}")
        return ShuResponse.error(message=str(e), code="PROMPT_ALREADY_EXISTS", status_code=409)
    except Exception as e:
        logger.error(f"Unexpected error updating prompt {prompt_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete(
    "/{prompt_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete prompt",
    description="Delete a prompt and all its assignments.",
)
async def delete_prompt(
    prompt_id: str = Path(..., description="Prompt ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a prompt and all its assignments."""
    try:
        service = PromptService(db)

        # Check if this is a system default prompt
        existing_prompt = await service.get_prompt(prompt_id)
        if existing_prompt and existing_prompt.is_system_default:
            return ShuResponse.error(
                message="System default prompts cannot be deleted",
                code="SYSTEM_PROMPT_PROTECTED",
                status_code=403,
            )

        deleted = await service.delete_prompt(prompt_id)

        if not deleted:
            return ShuResponse.error(message=f"Prompt {prompt_id} not found", code="PROMPT_NOT_FOUND", status_code=404)

        logger.info(f"Deleted prompt {prompt_id}")
        return ShuResponse.no_content()

    except Exception as e:
        logger.error(f"Unexpected error deleting prompt {prompt_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post(
    "/{prompt_id}/assignments",
    response_model=PromptAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign prompt to entity",
    description="Assign a prompt to a specific entity.",
)
async def assign_prompt(
    prompt_id: str = Path(..., description="Prompt ID"),
    assignment_data: PromptAssignmentCreate = ...,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign a prompt to an entity."""
    try:
        service = PromptService(db)
        assignment = await service.assign_prompt(prompt_id, assignment_data)

        logger.info(f"Assigned prompt {prompt_id} to entity {assignment_data.entity_id}")
        return ShuResponse.created(assignment.model_dump())

    except PromptNotFoundError as e:
        logger.warning(f"Prompt assignment failed: {e}")
        return ShuResponse.error(message=str(e), code="PROMPT_NOT_FOUND", status_code=404)
    except ConflictError as e:
        logger.warning(f"Prompt assignment conflict: {e}")
        return ShuResponse.error(message=str(e), code="ASSIGNMENT_ALREADY_EXISTS", status_code=409)
    except ValidationError as e:
        logger.warning(f"Prompt assignment blocked: {e}")
        return ShuResponse.error(message=str(e), code="VALIDATION_ERROR", status_code=422)
    except ShuException as e:
        logger.warning(f"Prompt assignment blocked: {e}")
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error assigning prompt {prompt_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete(
    "/{prompt_id}/assignments/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign prompt from entity",
    description="Remove a prompt assignment from a specific entity.",
)
async def unassign_prompt(
    prompt_id: str = Path(..., description="Prompt ID"),
    entity_id: str = Path(..., description="Entity ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Unassign a prompt from an entity."""
    try:
        service = PromptService(db)
        unassigned = await service.unassign_prompt(prompt_id, entity_id)

        if not unassigned:
            return ShuResponse.error(
                message=f"Assignment not found for prompt {prompt_id} and entity {entity_id}",
                code="ASSIGNMENT_NOT_FOUND",
                status_code=404,
            )

        logger.info(f"Unassigned prompt {prompt_id} from entity {entity_id}")
        return ShuResponse.no_content()

    except ShuException as e:
        logger.warning(f"Prompt unassignment blocked: {e}")
        return ShuResponse.error(message=str(e), code=e.error_code, status_code=400)
    except Exception as e:
        logger.error(f"Unexpected error unassigning prompt {prompt_id} from entity {entity_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/entities/{entity_id}",
    response_model=list[PromptResponse],
    summary="Get entity prompts",
    description="Get all prompts assigned to a specific entity.",
)
async def get_entity_prompts(
    entity_id: str = Path(..., description="Entity ID"),
    entity_type: EntityTypeEnum = Query(..., description="Entity type"),
    active_only: bool = Query(True, description="Return only active prompts"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all prompts assigned to a specific entity."""
    try:
        service = PromptService(db)
        prompts = await service.get_entity_prompts(entity_id, entity_type.value, active_only)

        logger.info(f"Retrieved {len(prompts)} prompts for entity {entity_id}")
        return ShuResponse.success([prompt.model_dump() for prompt in prompts])

    except Exception as e:
        logger.error(f"Unexpected error getting prompts for entity {entity_id}: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/stats",
    response_model=PromptSystemStats,
    summary="Get system statistics",
    description="Get system-wide prompt statistics.",
)
async def get_system_stats(current_user: User = Depends(require_power_user), db: AsyncSession = Depends(get_db)):
    """Get system-wide prompt statistics."""
    try:
        service = PromptService(db)
        stats = await service.get_system_stats()

        logger.info("Retrieved prompt system statistics")
        return ShuResponse.success(stats.model_dump())

    except Exception as e:
        logger.error(f"Unexpected error getting system stats: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
