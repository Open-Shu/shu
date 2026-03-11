"""Policy management API endpoints for Shu PBAC.

This module provides REST API endpoints for managing access policies,
including CRUD operations, access checking, and effective policy resolution.
All endpoints require admin role.
"""

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_admin
from ..core.exceptions import ConflictError, NotFoundError, ShuException, ValidationError
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..schemas.access_policy import PolicyInput, PolicyResponse
from ..services.policy_service import PolicyService
from .dependencies import get_db

logger = get_logger(__name__)
policies_router = APIRouter(prefix="/policies", tags=["policies"])


@policies_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create policy",
    description="Create a new access policy with bindings and statements. Admin only.",
)
async def create_policy(
    policy_data: PolicyInput,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Create a new access policy.

    Admin only. Sets created_by to the current admin user.
    """
    logger.info(
        "API: Create policy",
        extra={"user_id": current_user.id, "policy_name": policy_data.name},
    )

    try:
        service = PolicyService(db)
        result = await service.create_policy(policy_data, created_by=current_user.id)

        logger.info(
            "API: Created policy",
            extra={"policy_id": result.id, "policy_name": result.name},
        )

        return ShuResponse.created(PolicyResponse.model_validate(result).model_dump())

    except ConflictError as e:
        logger.warning("API: Policy name conflict", extra={"error": str(e)})
        return ShuResponse.error(message=str(e), code="POLICY_CONFLICT", status_code=409)
    except ValidationError as e:
        logger.warning("API: Policy validation failed", extra={"error": str(e)})
        return ShuResponse.error(message=e.message, code="POLICY_VALIDATION_ERROR", status_code=400)
    except ShuException as e:
        logger.error("API: Failed to create policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="POLICY_CREATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error creating policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.get(
    "",
    summary="List policies",
    description="List access policies with optional filtering and pagination. Admin only.",
)
async def list_policies(
    offset: int = Query(0, ge=0, description="Number of policies to skip"),
    limit: int = Query(50, ge=1, le=100, description="Number of policies to return"),
    search: str | None = Query(None, description="Search term for policy names"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List access policies with optional filtering and pagination.

    Admin only. Returns a paginated list of policies.
    """
    logger.info(
        "API: List policies",
        extra={
            "user_id": current_user.id,
            "offset": offset,
            "limit": limit,
            "search": search,
        },
    )

    try:
        service = PolicyService(db)
        result = await service.list_policies(offset=offset, limit=limit, search=search)

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to list policies", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="POLICY_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error listing policies", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.get(
    "/check",
    summary="Check access",
    description="Check whether a user has access to perform an action on a resource. Admin only.",
)
async def check_access(
    user_id: str = Query(..., description="User ID to check access for"),
    action: str = Query(..., description="Action to check (e.g., 'experience.read')"),
    resource: str = Query(..., description="Resource to check (e.g., 'experience:*')"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Check whether a user has access to perform an action on a resource.

    Admin only. Returns the access decision with matching policies and reason.
    """
    logger.info(
        "API: Check access",
        extra={
            "user_id": current_user.id,
            "target_user_id": user_id,
            "action": action,
            "resource": resource,
        },
    )

    try:
        service = PolicyService(db)
        result = await service.check_access(user_id=user_id, action=action, resource=resource)

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to check access", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="ACCESS_CHECK_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error checking access", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.get(
    "/effective/{user_id}",
    summary="Get effective policies",
    description="Get all policies that apply to a user through direct bindings and group memberships. Admin only.",
)
async def get_effective_policies(
    user_id: str = Path(..., description="User ID to resolve effective policies for"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get all effective policies for a user.

    Admin only. Resolves policies through direct user bindings and group memberships.
    """
    logger.info(
        "API: Get effective policies",
        extra={"user_id": current_user.id, "target_user_id": user_id},
    )

    try:
        service = PolicyService(db)
        result = await service.get_effective_policies(user_id=user_id)

        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        logger.error("API: Failed to get effective policies", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="EFFECTIVE_POLICIES_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error getting effective policies", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.get(
    "/{policy_id}",
    summary="Get policy",
    description="Get a specific access policy by ID including bindings and statements. Admin only.",
)
async def get_policy(
    policy_id: str = Path(..., description="Policy ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Get a specific access policy by ID.

    Admin only. Returns the full policy including bindings and statements.
    """
    logger.info(
        "API: Get policy",
        extra={"policy_id": policy_id, "user_id": current_user.id},
    )

    try:
        service = PolicyService(db)
        result = await service.get_policy(policy_id)

        if not result:
            return ShuResponse.error(
                message=f"Policy '{policy_id}' not found",
                code="POLICY_NOT_FOUND",
                status_code=404,
            )

        return ShuResponse.success(PolicyResponse.model_validate(result).model_dump())

    except ShuException as e:
        logger.error("API: Failed to get policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="POLICY_GET_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error getting policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.put(
    "/{policy_id}",
    summary="Update policy",
    description="Update an existing access policy. Admin only.",
)
async def update_policy(
    policy_data: PolicyInput,
    policy_id: str = Path(..., description="Policy ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update an existing access policy.

    Admin only. Replaces the full policy document including bindings and statements.
    """
    logger.info(
        "API: Update policy",
        extra={"policy_id": policy_id, "user_id": current_user.id},
    )

    try:
        service = PolicyService(db)
        result = await service.update_policy(policy_id, policy_data)

        logger.info(
            "API: Updated policy",
            extra={"policy_id": policy_id, "policy_name": result.name},
        )

        return ShuResponse.success(PolicyResponse.model_validate(result).model_dump())

    except NotFoundError as e:
        return ShuResponse.error(message=str(e), code="POLICY_NOT_FOUND", status_code=404)
    except ConflictError as e:
        return ShuResponse.error(message=str(e), code="POLICY_CONFLICT", status_code=409)
    except ValidationError as e:
        return ShuResponse.error(message=e.message, code="POLICY_VALIDATION_ERROR", status_code=400)
    except ShuException as e:
        logger.error("API: Failed to update policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="POLICY_UPDATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error updating policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@policies_router.delete(
    "/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete policy",
    description="Delete an access policy and its bindings and statements. Admin only.",
)
async def delete_policy(
    policy_id: str = Path(..., description="Policy ID"),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an access policy and its cascading bindings and statements.

    Admin only. This operation cannot be undone.
    """
    logger.info(
        "API: Delete policy",
        extra={"policy_id": policy_id, "user_id": current_user.id},
    )

    try:
        service = PolicyService(db)
        await service.delete_policy(policy_id)

        logger.info("API: Deleted policy", extra={"policy_id": policy_id})

        return ShuResponse.no_content()

    except NotFoundError as e:
        return ShuResponse.error(message=str(e), code="POLICY_NOT_FOUND", status_code=404)
    except ShuException as e:
        logger.error("API: Failed to delete policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message=str(e), code="POLICY_DELETE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Unexpected error deleting policy", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
