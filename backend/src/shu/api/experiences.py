"""
Experience API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing experiences,
including CRUD operations, run management, and user dashboard data.
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from .dependencies import get_db
from ..auth.rbac import get_current_user, require_admin
from ..auth.models import User
from ..core.exceptions import ShuException, NotFoundError, ConflictError, ValidationError
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..schemas.experience import (
    ExperienceCreate, ExperienceUpdate, ExperienceVisibility, ExperienceRunRequest
)
from ..services.experience_service import ExperienceService

logger = get_logger(__name__)
router = APIRouter(prefix="/experiences", tags=["experiences"])


@router.get(
    "",
    summary="List experiences",
    description="List experiences with optional filtering and pagination."
)
async def list_experiences(
    limit: int = Query(50, ge=1, le=100, description="Number of experiences to return"),
    offset: int = Query(0, ge=0, description="Number of experiences to skip"),
    visibility: Optional[ExperienceVisibility] = Query(None, description="Filter by visibility"),
    search: Optional[str] = Query(None, description="Search term for experience names"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List experiences with optional filtering and pagination.
    
    - Admins see all experiences
    - Non-admins only see published experiences
    """
    logger.info("API: List experiences", extra={
        "user_id": current_user.id,
        "limit": limit,
        "offset": offset,
        "visibility": visibility.value if visibility else None,
        "search": search
    })
    
    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()
        
        result = await service.list_experiences(
            user_id=current_user.id,
            is_admin=is_admin,
            offset=offset,
            limit=limit,
            visibility_filter=visibility,
            search=search
        )
        
        return ShuResponse.success(result.model_dump())
        
    except ShuException as e:
        logger.error("API: Failed to list experiences", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_LIST_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error listing experiences", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.post(
    "",
    summary="Create experience",
    description="Create a new experience. Admin only."
)
async def create_experience(
    experience_data: ExperienceCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new experience.
    
    Admin only. Sets created_by to the current admin user.
    """
    logger.info("API: Create experience", extra={
        "user_id": current_user.id,
        "experience_name": experience_data.name
    })
    
    try:
        service = ExperienceService(db)
        result = await service.create_experience(experience_data, created_by=current_user.id)
        
        logger.info("API: Created experience", extra={
            "experience_id": result.id,
            "experience_name": result.name
        })
        
        return ShuResponse.created(result.model_dump())
        
    except ConflictError as e:
        logger.warning("API: Experience name conflict", extra={"error": str(e)})
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_CONFLICT",
            status_code=409
        )
    except ValidationError as e:
        logger.warning("API: Experience validation failed", extra={"error": str(e)})
        return ShuResponse.error(
            message=e.message,
            code="EXPERIENCE_VALIDATION_ERROR",
            status_code=400
        )
    except ShuException as e:
        logger.error("API: Failed to create experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_CREATE_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error creating experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.get(
    "/my-results",
    summary="Get user's experience results",
    description="Get the current user's latest results for all accessible experiences."
)
async def get_my_results(
    limit: int = Query(50, ge=1, le=100, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current user's latest results for the experience dashboard.
    
    Returns published experiences with the user's latest run result.
    """
    logger.info("API: Get my results", extra={"user_id": current_user.id})
    
    try:
        service = ExperienceService(db)
        result = await service.get_user_results(
            user_id=current_user.id,
            offset=offset,
            limit=limit
        )
        
        return ShuResponse.success(result.model_dump())
        
    except ShuException as e:
        logger.error("API: Failed to get user results", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="USER_RESULTS_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error getting user results", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.get(
    "/runs/{run_id}",
    summary="Get experience run",
    description="Get a specific experience run by ID."
)
async def get_run(
    run_id: str = Path(..., description="Experience run ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific experience run.
    
    Only the run owner or admins can view run details.
    """
    logger.info("API: Get run", extra={"run_id": run_id, "user_id": current_user.id})
    
    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()
        
        result = await service.get_run(
            run_id=run_id,
            user_id=current_user.id,
            is_admin=is_admin
        )
        
        if not result:
            return ShuResponse.error(
                message=f"Run '{run_id}' not found or access denied",
                code="RUN_NOT_FOUND",
                status_code=404
            )
        
        return ShuResponse.success(result.model_dump())
        
    except ShuException as e:
        logger.error("API: Failed to get run", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="RUN_GET_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error getting run", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.get(
    "/{experience_id}",
    summary="Get experience",
    description="Get a specific experience by ID."
)
async def get_experience(
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific experience by ID.
    
    Visibility check is applied based on user role.
    """
    logger.info("API: Get experience", extra={
        "experience_id": experience_id,
        "user_id": current_user.id
    })
    
    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()
        
        result = await service.get_experience(
            experience_id=experience_id,
            user_id=current_user.id,
            is_admin=is_admin
        )
        
        if not result:
            return ShuResponse.error(
                message=f"Experience '{experience_id}' not found or access denied",
                code="EXPERIENCE_NOT_FOUND",
                status_code=404
            )
        
        return ShuResponse.success(result.model_dump())
        
    except ShuException as e:
        logger.error("API: Failed to get experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_GET_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error getting experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.put(
    "/{experience_id}",
    summary="Update experience",
    description="Update an existing experience. Admin only."
)
async def update_experience(
    experience_id: str = Path(..., description="Experience ID"),
    update_data: ExperienceUpdate = ...,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Update an existing experience.
    
    Admin only.
    """
    logger.info("API: Update experience", extra={
        "experience_id": experience_id,
        "user_id": current_user.id
    })
    
    try:
        service = ExperienceService(db)
        result = await service.update_experience(experience_id, update_data)
        
        logger.info("API: Updated experience", extra={
            "experience_id": experience_id,
            "experience_name": result.name
        })
        
        return ShuResponse.success(result.model_dump())
        
    except NotFoundError as e:
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_NOT_FOUND",
            status_code=404
        )
    except ConflictError as e:
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_CONFLICT",
            status_code=409
        )
    except ValidationError as e:
        return ShuResponse.error(
            message=e.message,
            code="EXPERIENCE_VALIDATION_ERROR",
            status_code=400
        )
    except ShuException as e:
        logger.error("API: Failed to update experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_UPDATE_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error updating experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.delete(
    "/{experience_id}",
    summary="Delete experience",
    description="Delete an experience. Admin only."
)
async def delete_experience(
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an experience and all its steps and runs.
    
    Admin only. This operation cannot be undone.
    """
    logger.info("API: Delete experience", extra={
        "experience_id": experience_id,
        "user_id": current_user.id
    })
    
    try:
        service = ExperienceService(db)
        deleted = await service.delete_experience(experience_id)
        
        if not deleted:
            return ShuResponse.error(
                message=f"Experience '{experience_id}' not found",
                code="EXPERIENCE_NOT_FOUND",
                status_code=404
            )
        
        logger.info("API: Deleted experience", extra={"experience_id": experience_id})
        
        return ShuResponse.no_content()
        
    except ShuException as e:
        logger.error("API: Failed to delete experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_DELETE_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error deleting experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )


@router.post(
    "/{experience_id}/run",
    summary="Execute experience",
    description="Execute an experience and stream results via SSE."
)
async def run_experience(
    experience_id: str = Path(..., description="Experience ID"),
    run_request: Optional[ExperienceRunRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Execute an experience with SSE streaming.
    
    Returns a Server-Sent Events stream with execution progress:
    - run_started: Execution has begun
    - step_started/step_completed/step_failed/step_skipped: Step progress
    - synthesis_started: LLM synthesis has begun
    - content_delta: Streaming LLM tokens
    - run_completed: Execution finished successfully
    - error: Execution failed
    """
    import json
    from fastapi.responses import StreamingResponse
    from ..core.config import get_config_manager
    from ..services.experience_executor import ExperienceExecutor
    
    logger.info("API: Run experience", extra={
        "experience_id": experience_id,
        "user_id": current_user.id
    })
    
    # First, verify the experience exists and user has access
    service = ExperienceService(db)
    is_admin = current_user.can_manage_users()
    
    experience = await service.get_experience(
        experience_id=experience_id,
        user_id=current_user.id,
        is_admin=is_admin
    )
    
    if not experience:
        return ShuResponse.error(
            message=f"Experience '{experience_id}' not found or access denied",
            code="EXPERIENCE_NOT_FOUND",
            status_code=404
        )
    
    # Get the actual Experience model for execution
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from ..models.experience import Experience
    result = await db.execute(
        select(Experience)
        .options(
            selectinload(Experience.steps),
            selectinload(Experience.prompt)
        )
        .where(Experience.id == experience_id)
    )
    experience_model = result.scalars().first()
    
    if not experience_model:
        return ShuResponse.error(
            message=f"Experience '{experience_id}' not found",
            code="EXPERIENCE_NOT_FOUND",
            status_code=404
        )
    
    async def event_generator():
        """Generate SSE events from executor."""
        config_manager = get_config_manager()
        executor = ExperienceExecutor(db, config_manager)
        
        try:
            async for event in executor.execute_streaming(
                experience=experience_model,
                user_id=str(current_user.id),
                input_params=run_request.input_params if run_request else {},
                current_user=current_user,
            ):
                yield f"data: {json.dumps(event.to_dict())}\n\n"
        except Exception as e:
            logger.exception("Experience execution error", extra={"experience_id": experience_id})
            error_event = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.get(
    "/{experience_id}/runs",
    summary="List experience runs",
    description="List all runs for a specific experience."
)
async def list_experience_runs(
    experience_id: str = Path(..., description="Experience ID"),
    limit: int = Query(50, ge=1, le=100, description="Number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List runs for a specific experience.
    
    - Admins see all runs
    - Non-admins only see their own runs
    """
    logger.info("API: List experience runs", extra={
        "experience_id": experience_id,
        "user_id": current_user.id
    })
    
    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()
        
        result = await service.list_runs(
            experience_id=experience_id,
            user_id=current_user.id,
            is_admin=is_admin,
            offset=offset,
            limit=limit
        )
        
        return ShuResponse.success(result.model_dump())
        
    except NotFoundError as e:
        return ShuResponse.error(
            message=str(e),
            code="EXPERIENCE_NOT_FOUND",
            status_code=404
        )
    except ShuException as e:
        logger.error("API: Failed to list runs", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message=str(e),
            code="RUNS_LIST_ERROR",
            status_code=e.status_code
        )
    except Exception as e:
        logger.error("API: Unexpected error listing runs", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(
            message="Internal server error",
            code="INTERNAL_SERVER_ERROR",
            status_code=500
        )
