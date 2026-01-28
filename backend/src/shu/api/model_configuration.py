"""
Model Configuration API endpoints for Shu.

This module provides REST API endpoints for managing model configurations -
the foundational abstraction that combines base models + prompts + optional
knowledge bases into user-facing configurations.
"""

import logging
from typing import Optional, Dict, Any, Iterable, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status, Form, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import get_db
from ..auth.rbac import require_power_user, require_regular_user
from ..auth.models import User, UserRole
from ..core.config import get_config_manager_dependency, ConfigurationManager
from ..core.exceptions import ShuException, LLMConfigurationError, LLMError
from ..core.response import ShuResponse
from ..services.attachment_service import AttachmentService
from ..services.chat_service import ChatService
from ..schemas.query import RagRewriteMode
from ..services.model_configuration_service import ModelConfigurationService
from ..services.side_call_service import SideCallService
from ..services.error_sanitization import ErrorSanitizer
from ..schemas.model_configuration import (
    ModelConfigurationCreate,
    ModelConfigurationUpdate,
    ModelConfigurationResponse,
    ModelConfigurationList,
    ModelConfigurationTestResponse,
    ModelConfigKBPromptAssignment,
    ModelConfigKBPromptResponse,
)
from ..schemas.envelope import SuccessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-configurations", tags=["Model Configurations"])

# Constants
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024  # 1MB chunks for streaming reads
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/tiff"
}


async def _validate_and_read_upload(
    file: UploadFile,
    allowed_mime_types: set[str] = ALLOWED_IMAGE_MIME_TYPES,
    max_size_bytes: int = MAX_UPLOAD_SIZE_BYTES,
    chunk_size: int = UPLOAD_CHUNK_SIZE_BYTES
) -> bytes:
    """
    Validate and read an uploaded file with MIME type and size limits.
    
    This function implements comprehensive file validation:
    1. MIME type validation: Checks file.content_type against allowed types
    2. Pre-read size validation: Checks file.size metadata if available (most efficient)
    3. During-read size validation: Validates size while streaming in chunks (fallback)
    
    The two-stage size validation is necessary because file.size may not always be
    available (depends on client sending Content-Length header). Streaming reads
    prevent memory exhaustion even for permitted large files.
    
    Args:
        file: The FastAPI UploadFile to validate and read
        allowed_mime_types: Set of allowed MIME types (default: image types)
        max_size_bytes: Maximum allowed file size in bytes (default: 10MB)
        chunk_size: Size of chunks for streaming reads (default: 1MB)
        
    Returns:
        bytes: The complete file content
        
    Raises:
        HTTPException: If file type is invalid or exceeds size limit at any validation stage
    """
    # Validate MIME type before reading
    if file.content_type not in allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {file.content_type}. Allowed types: {', '.join(sorted(allowed_mime_types))}."
        )
    
    # Stage 1: Pre-read size validation using metadata (if available)
    # This is the most efficient check - prevents reading oversized files entirely
    if file.size is not None and file.size > max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large: {file.size} bytes. Maximum size is {max_size_bytes} bytes ({max_size_bytes // (1024 * 1024)}MB)."
        )
    
    # Stage 2: Stream file in chunks with size validation during read
    # This catches oversized files even if metadata was unavailable
    file_content = bytearray()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        file_content.extend(chunk)
        
        # Validate accumulated size doesn't exceed limit
        if len(file_content) > max_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large: exceeds {max_size_bytes} bytes ({max_size_bytes // (1024 * 1024)}MB)."
            )
    
    return bytes(file_content)


def _format_test_error_with_suggestions(error_message: Any) -> str:
    """Format error message with suggestions for the LLM Tester.

    This function enhances error messages with helpful suggestions for
    common LLM configuration errors. It's used only in the /test endpoint
    where detailed error guidance is appropriate.

    TODO: This error type detection should be moved to provider adapters.
    Each adapter should return structured error information with proper
    error types/codes instead of us guessing from the message text.
    This would be more reliable and allow provider-specific guidance.

    Args:
        error_message: The original error message from the LLM client (can be Any type).

    Returns:
        Enhanced error message with suggestions on separate lines.
    """
    # Coerce input to string at the start to handle non-string types
    error_text = "" if error_message is None else str(error_message)
    
    # Build a minimal details dict for ErrorSanitizer
    # We extract what we can from the error message
    details: Dict[str, Any] = {"provider_message": error_text}

    # Detect error type from message content
    error_lower = error_text.lower()
    if "authentication" in error_lower or "api key" in error_lower or "unauthorized" in error_lower:
        details["status"] = 401
    elif "rate limit" in error_lower or "too many requests" in error_lower:
        details["status"] = 429
    elif "invalid" in error_lower or "malformed" in error_lower or "required" in error_lower:
        details["status"] = 400

    # Use ErrorSanitizer to get suggestions
    sanitized = ErrorSanitizer.sanitize_error(details)

    if not sanitized.suggestions:
        return error_text

    suggestions_text = "\n".join(f"  • {s}" for s in sanitized.suggestions)
    return f"{error_text}\n\nSuggestions:\n{suggestions_text}"


def _create_side_call_service(db: AsyncSession) -> SideCallService:
    """Instantiate a SideCallService for the current request."""
    return SideCallService(db, get_config_manager_dependency())


async def _get_side_call_model_id(side_call_service: SideCallService) -> Optional[str]:
    """Return the configured side-call model ID, if any."""
    side_call_model = await side_call_service.get_side_call_model()
    return side_call_model.id if side_call_model else None


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
        side_call_service = _create_side_call_service(db)
        config = await service.create_model_configuration(config_data, created_by=current_user.id)

        # If this model is marked for side calls, update the system setting
        if getattr(config_data, "is_side_call_model", False):
            await side_call_service.set_side_call_model(config.id, current_user.id)

        # Reload with relationships for response serialization
        config_with_relationships = await service.get_model_configuration(
            config.id, include_relationships=True
        )

        side_call_model_id = await _get_side_call_model_id(side_call_service)

        # Convert to response format
        response_data = service._to_response(config_with_relationships)
        _apply_side_call_flag(response_data, side_call_model_id)

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
        side_call_service = _create_side_call_service(db)

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
        if hasattr(result, "items"):
            _apply_side_call_flag(result.items, side_call_model_id)
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
        
        side_call_service = _create_side_call_service(db)
        response_data = service._to_response(config)
        side_call_model_id = await _get_side_call_model_id(side_call_service)
        _apply_side_call_flag(response_data, side_call_model_id)
        
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
        side_call_service = _create_side_call_service(db)

        # Capture current side-call model before applying updates so we can
        # decide whether this update should clear the designation.
        existing_side_call_model_id = await _get_side_call_model_id(side_call_service)

        config = await service.update_model_configuration(config_id, update_data)

        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model configuration {config_id} not found"
            )

        is_side_call_flag = getattr(update_data, "is_side_call_model", None)

        # If this model is marked for side calls, update the system setting
        if is_side_call_flag is True:
            await side_call_service.set_side_call_model(config_id, current_user.id)
        # If the flag is explicitly False and this config is currently the
        # side-call model, clear the designation.
        elif (
            is_side_call_flag is False
            and existing_side_call_model_id is not None
            and existing_side_call_model_id == config_id
        ):
            await side_call_service.clear_side_call_model(current_user.id)

        # Reload with relationships to ensure they're available for serialization
        config_with_relationships = await service.get_model_configuration(
            config.id, include_relationships=True
        )

        response_data = service._to_response(config_with_relationships)
        side_call_model_id = await _get_side_call_model_id(side_call_service)
        _apply_side_call_flag(response_data, side_call_model_id)

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
    description="Test a model configuration with a sample message (non-streaming for better error messages)"
)
async def test_model_configuration(
    config_id: str,
    test_message: str = Form(..., description="Test message to send"),
    include_knowledge_bases: bool = Form(True, description="Whether to include KB context"),
    file: Optional[UploadFile] = File(None, description="Optional image file for vision testing"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """
    Test a model configuration with a sample message and optional image attachment.
    
    Uses non-streaming mode to ensure providers return detailed error messages.
    Some providers only return useful configuration errors in non-streaming requests.
    
    Supports multipart/form-data for image uploads to test vision capabilities.
    """
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

        # Handle file upload if provided
        attachment_ids = []
        if file:
            try:
                # Validate and read file (MIME type, size limits, streaming)
                file_content = await _validate_and_read_upload(file)
                
                attachment_service = AttachmentService(db)
                attachment, _ = await attachment_service.save_upload(
                    conversation_id=conversation.id,
                    user_id=current_user.id,
                    filename=file.filename,
                    file_bytes=file_content
                )
                attachment_ids.append(attachment.id)
            except HTTPException:
                # Re-raise HTTP exceptions (including validation errors)
                await file.close()
                await chat_service.delete_conversation(conversation.id)
                raise
            except Exception as e:
                logger.error(f"Failed to create attachment for test: {e}")
                # Clean up conversation
                await file.close()
                await chat_service.delete_conversation(conversation.id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to process image attachment: {str(e)}"
                )
            finally:
                await file.close()

        response = ""
        error = None
        message_metadata: Dict[str, Any] = {}

        try:
            async for event in await chat_service.send_message(
                conversation_id=conversation.id,
                user_message=test_message,
                current_user=current_user,
                rag_rewrite_mode=RagRewriteMode.NO_RAG,
                force_no_streaming=True,
                attachment_ids=attachment_ids,
            ):
                if event.type == "final_message":
                    response = event.content.get("content")
                    # Extract message_metadata which contains usage and timing info
                    message_metadata = event.content.get("message_metadata", {}) or {}
                if event.type == "error":
                    error = event.content
        finally:
            await chat_service.delete_conversation(conversation.id)

        # Extract usage and timing from message_metadata
        usage = message_metadata.get("usage", {}) or {}
        response_time_ms_raw = message_metadata.get("response_time_ms")
        response_time_ms = int(response_time_ms_raw) if response_time_ms_raw is not None else None
        
        # Build token usage dict with proper field names
        token_usage = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
        }

        # Handle error case - return the error message instead of raising
        if error:
            # Enhance error with suggestions for the LLM Tester
            enhanced_error = _format_test_error_with_suggestions(error)
            response_data = ModelConfigurationTestResponse(
                success=False,
                response=None,
                error=enhanced_error,
                model_used=f"{config.llm_provider.name}/{config.model_name}",
                prompt_applied=config.prompt is not None,
                knowledge_bases_used=[kb.name for kb in config.knowledge_bases] if include_knowledge_bases else [],
                response_time_ms=response_time_ms,
                token_usage=token_usage,
                metadata={"streaming": False}
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
            knowledge_bases_used=[kb.name for kb in config.knowledge_bases] if include_knowledge_bases else [],
            response_time_ms=response_time_ms,
            token_usage=token_usage,
            metadata={"streaming": False}
        )

        return SuccessResponse(data=response_data)

    except HTTPException:
        raise
    except LLMConfigurationError as e:
        logger.error("Failed to test model configuration %s: %s", config_id, e)
        # Enhance error with suggestions for the LLM Tester
        enhanced_error = _format_test_error_with_suggestions(str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=enhanced_error)
    except LLMError as e:
        logger.error("LLM error testing model configuration %s: %s", config_id, e)
        # Enhance error with suggestions for the LLM Tester
        enhanced_error = _format_test_error_with_suggestions(str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=enhanced_error)
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
