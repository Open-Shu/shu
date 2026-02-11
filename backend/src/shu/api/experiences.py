"""Experience API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing experiences,
including CRUD operations, run management, and user dashboard data.
"""

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth.models import User
from ..auth.rbac import get_current_user, require_admin
from ..core.config import get_config_manager
from ..core.exceptions import ConflictError, NotFoundError, ShuException, ValidationError
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..core.streaming import create_sse_stream_generator
from ..models.experience import Experience
from ..schemas.experience import (
    ExperienceCreate,
    ExperienceRunRequest,
    ExperienceUpdate,
    ExperienceVisibility,
)
from ..services.experience_executor import ExperienceExecutor
from ..services.experience_service import ExperienceService
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/experiences", tags=["experiences"])


@router.get(
    "",
    summary="List experiences",
    description="List experiences with optional filtering and pagination.",
)
async def list_experiences(
    limit: int = Query(50, ge=1, le=100, description="Number of experiences to return"),
    offset: int = Query(0, ge=0, description="Number of experiences to skip"),
    visibility: ExperienceVisibility | None = Query(None, description="Filter by visibility"),
    search: str | None = Query(None, description="Search term for experience names"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List experiences with optional filtering and pagination.

    - Admins see all experiences
    - Non-admins only see published experiences
    """
    logger.info(
        "API: List experiences",
        extra={
            "user_id": current_user.id,
            "limit": limit,
            "offset": offset,
            "visibility": visibility.value if visibility else None,
            "search": search,
        },
    )

    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()

        result = await service.list_experiences(
            user_id=current_user.id,
            is_admin=is_admin,
            offset=offset,
            limit=limit,
            visibility_filter=visibility,
            search=search,
        )

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to list experiences", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error listing experiences", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("", summary="Create experience", description="Create a new experience. Admin only.")
async def create_experience(
    experience_data: ExperienceCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Create a new experience.

    Admin only. Sets created_by to the current admin user.
    """
    logger.info(
        "API: Create experience",
        extra={"user_id": current_user.id, "experience_name": experience_data.name},
    )

    try:
        service = ExperienceService(db)
        result = await service.create_experience(experience_data, created_by=current_user.id, current_user=current_user)

        logger.info(
            "API: Created experience",
            extra={"experience_id": result.id, "experience_name": result.name},
        )

        return ShuResponse.created(result.model_dump())

    except ConflictError as e:
        logger.warning("API: Experience name conflict", extra={"error": str(e)})
        return ShuResponse.error(message=str(e), code="EXPERIENCE_CONFLICT", status_code=409)
    except ValidationError as e:
        logger.warning("API: Experience validation failed", extra={"error": str(e)})
        return ShuResponse.error(message=e.message, code="EXPERIENCE_VALIDATION_ERROR", status_code=400)
    except ShuException as e:
        logger.error("API: Failed to create experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_CREATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error creating experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/my-results",
    summary="Get user's experience results",
    description="Get the current user's latest results for all accessible experiences.",
)
async def get_my_results(
    limit: int = Query(50, ge=1, le=100, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get the current user's latest results for the experience dashboard.

    Returns published experiences with the user's latest run result.
    """
    logger.info("API: Get my results", extra={"user_id": current_user.id})

    try:
        service = ExperienceService(db)
        result = await service.get_user_results(user_id=current_user.id, offset=offset, limit=limit)

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to get user results", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="USER_RESULTS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error getting user results", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/runs/{run_id}",
    summary="Get experience run",
    description="Get a specific experience run by ID.",
)
async def get_run(
    run_id: str = Path(..., description="Experience run ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get a specific experience run.

    Only the run owner or admins can view run details.
    """
    logger.info("API: Get run", extra={"run_id": run_id, "user_id": current_user.id})

    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()

        result = await service.get_run(run_id=run_id, user_id=current_user.id, is_admin=is_admin)

        if not result:
            return ShuResponse.error(
                message=f"Run '{run_id}' not found or access denied",
                code="RUN_NOT_FOUND",
                status_code=404,
            )

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to get run", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="RUN_GET_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error getting run", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{experience_id}", summary="Get experience", description="Get a specific experience by ID.")
async def get_experience(
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get a specific experience by ID.

    Visibility check is applied based on user role.
    """
    logger.info("API: Get experience", extra={"experience_id": experience_id, "user_id": current_user.id})

    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()

        result = await service.get_experience(experience_id=experience_id, user_id=current_user.id, is_admin=is_admin)

        if not result:
            return ShuResponse.error(
                message=f"Experience '{experience_id}' not found or access denied",
                code="EXPERIENCE_NOT_FOUND",
                status_code=404,
            )

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to get experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_GET_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error getting experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.put(
    "/{experience_id}",
    summary="Update experience",
    description="Update an existing experience. Admin only.",
)
async def update_experience(
    update_data: ExperienceUpdate,
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update an existing experience.

    Admin only.
    """
    logger.info("API: Update experience", extra={"experience_id": experience_id, "user_id": current_user.id})

    try:
        service = ExperienceService(db)
        result = await service.update_experience(experience_id, update_data, current_user=current_user)

        logger.info(
            "API: Updated experience",
            extra={"experience_id": experience_id, "experience_name": result.name},
        )

        return ShuResponse.success(result.model_dump())

    except NotFoundError as e:
        return ShuResponse.error(message=str(e), code="EXPERIENCE_NOT_FOUND", status_code=404)
    except ConflictError as e:
        return ShuResponse.error(message=str(e), code="EXPERIENCE_CONFLICT", status_code=409)
    except ValidationError as e:
        return ShuResponse.error(message=e.message, code="EXPERIENCE_VALIDATION_ERROR", status_code=400)
    except ShuException as e:
        logger.error("API: Failed to update experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_UPDATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error updating experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete("/{experience_id}", summary="Delete experience", description="Delete an experience. Admin only.")
async def delete_experience(
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an experience and all its steps and runs.

    Admin only. This operation cannot be undone.
    """
    logger.info("API: Delete experience", extra={"experience_id": experience_id, "user_id": current_user.id})

    try:
        service = ExperienceService(db)
        deleted = await service.delete_experience(experience_id)

        if not deleted:
            return ShuResponse.error(
                message=f"Experience '{experience_id}' not found",
                code="EXPERIENCE_NOT_FOUND",
                status_code=404,
            )

        logger.info("API: Deleted experience", extra={"experience_id": experience_id})

        return ShuResponse.no_content()

    except ShuException as e:
        logger.error("API: Failed to delete experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_DELETE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error deleting experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post(
    "/{experience_id}/run",
    summary="Execute experience",
    description="Execute an experience and stream results via SSE.",
    response_model=None,
)
async def run_experience(
    experience_id: str = Path(..., description="Experience ID"),
    run_request: ExperienceRunRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | JSONResponse:
    """Execute an experience with SSE streaming.

    Returns a Server-Sent Events stream with execution progress:
    - run_started: Execution has begun
    - step_started/step_completed/step_failed/step_skipped: Step progress
    - synthesis_started: LLM synthesis has begun
    - content_delta: Streaming LLM tokens
    - run_completed: Execution finished successfully
    - error: Execution failed
    """
    logger.info("API: Run experience", extra={"experience_id": experience_id, "user_id": current_user.id})

    # First, verify the experience exists and user has access
    service = ExperienceService(db)
    is_admin = current_user.can_manage_users()

    experience = await service.get_experience(experience_id=experience_id, user_id=current_user.id, is_admin=is_admin)

    if not experience:
        return ShuResponse.error(
            message=f"Experience '{experience_id}' not found or access denied",
            code="EXPERIENCE_NOT_FOUND",
            status_code=404,
        )

    # Get the actual Experience model for execution

    result = await db.execute(
        select(Experience)
        .options(selectinload(Experience.steps), selectinload(Experience.prompt))
        .where(Experience.id == experience_id)
    )
    experience_model = result.scalars().first()

    if not experience_model:
        return ShuResponse.error(
            message=f"Experience '{experience_id}' not found",
            code="EXPERIENCE_NOT_FOUND",
            status_code=404,
        )

    config_manager = get_config_manager()
    executor = ExperienceExecutor(db, config_manager)
    event_gen = executor.execute_streaming(
        experience=experience_model,
        user_id=str(current_user.id),
        input_params=run_request.input_params if run_request and run_request.input_params else {},
        current_user=current_user,
    )
    sse_generator = create_sse_stream_generator(
        event_gen,
        error_context="experience_execution",
        error_sanitizer=None,
        include_correlation_id=True,
        error_code="EXPERIENCE_EXECUTION_FAILED",
    )

    return StreamingResponse(
        sse_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/{experience_id}/export",
    summary="Export experience as YAML",
    description="Export an experience configuration as a downloadable YAML file with placeholders for user-specific values.",
    response_model=None,
)
async def export_experience(
    experience_id: str = Path(..., description="Experience ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response | JSONResponse:
    """Export an experience as YAML with placeholders for sharing.

    Converts experience database record to YAML format with placeholders
    for user-specific values like timezone, provider, and model.
    """
    logger.info("API: Export experience", extra={"experience_id": experience_id, "user_id": current_user.id})

    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()

        # Get the experience with visibility check
        experience = await service.get_experience(
            experience_id=experience_id, user_id=current_user.id, is_admin=is_admin
        )

        if not experience:
            return ShuResponse.error(
                message=f"Experience '{experience_id}' not found or access denied",
                code="EXPERIENCE_NOT_FOUND",
                status_code=404,
            )

        # Export to YAML
        yaml_content, file_name = service.export_experience_to_yaml(experience)

        logger.info(
            "API: Exported experience to YAML",
            extra={"experience_id": experience_id, "export_filename": file_name},
        )

        return Response(
            content=yaml_content,
            media_type="application/x-yaml",
            headers={
                "Content-Disposition": f"attachment; filename={file_name}",
                "Content-Type": "application/x-yaml; charset=utf-8",
            },
        )

    except ShuException as e:
        logger.error("API: Failed to export experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EXPERIENCE_EXPORT_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error exporting experience", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{experience_id}/runs",
    summary="List experience runs",
    description="List all runs for a specific experience.",
)
async def list_experience_runs(
    experience_id: str = Path(..., description="Experience ID"),
    limit: int = Query(50, ge=1, le=100, description="Number of runs to return"),
    offset: int = Query(0, ge=0, description="Number of runs to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List runs for a specific experience.

    - Admins see all runs
    - Non-admins only see their own runs
    """
    logger.info(
        "API: List experience runs",
        extra={"experience_id": experience_id, "user_id": current_user.id},
    )

    try:
        service = ExperienceService(db)
        is_admin = current_user.can_manage_users()

        result = await service.list_runs(
            experience_id=experience_id,
            user_id=current_user.id,
            is_admin=is_admin,
            offset=offset,
            limit=limit,
        )

        return ShuResponse.success(result.model_dump())

    except NotFoundError as e:
        return ShuResponse.error(message=str(e), code="EXPERIENCE_NOT_FOUND", status_code=404)
    except ShuException as e:
        logger.error("API: Failed to list runs", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="RUNS_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error listing runs", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
