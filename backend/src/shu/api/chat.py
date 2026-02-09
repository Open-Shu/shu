"""Chat API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing chat conversations,
messages, and LLM interactions.
"""

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path as PathlibPath
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import get_current_user
from ..core.config import ConfigurationManager, get_config_manager_dependency, get_settings_instance
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.response import ShuResponse, create_error_response, create_success_response
from ..models.attachment import Attachment
from ..models.llm_provider import Conversation, Message
from ..schemas.chat import ConversationFromExperienceRequest
from ..schemas.envelope import SuccessResponse
from ..schemas.query import RagRewriteMode
from ..services.attachment_service import AttachmentService
from ..services.chat_service import ChatService
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

settings = get_settings_instance()


def _sanitize_chat_error_message(error_content: str) -> str:
    """Sanitize error messages for chat endpoints while preserving important backend errors.

    This function provides selective error sanitization:
    - Rate limit errors are preserved so users understand throttling
    - Timeout errors are preserved so users know the request took too long
    - Service unavailable errors are preserved so users know the backend is down
    - All other errors (API keys, auth, config, DB, etc.) are replaced with generic messages

    Args:
        error_content: The original error message from the provider or backend

    Returns:
        Either the original error message (for allowed errors) or a sanitized generic message

    """
    if not error_content:
        return "The request failed. You may want to try another model."

    error_lower = error_content.lower()

    # Only preserve these specific error types
    if "rate limit" in error_lower or "too many requests" in error_lower:
        return error_content

    if "timeout" in error_lower or "timed out" in error_lower:
        return error_content

    if "service unavailable" in error_lower or "temporarily unavailable" in error_lower:
        return error_content

    # Sanitize all other errors (API keys, auth, config, DB, malformed requests, etc.)
    return "The request failed. You may want to try another model."


async def create_sse_stream_generator(event_generator, error_context: str = "streaming"):
    """Wrap an async event generator with robust error handling for SSE streaming.

    Args:
        event_generator: Async generator that yields events with to_dict() method
        error_context: Context string for error messages (e.g., "send_message", "regenerate_message")

    Yields:
        SSE-formatted data strings

    """
    try:
        async for event in event_generator:
            try:
                # Sanitize error messages for regular chat users
                if event.type == "error":
                    event.content = _sanitize_chat_error_message(event.content or "")

                payload = event.to_dict()
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:
                logger.exception(f"Error serializing event during {error_context}")
                # Continue to next event rather than breaking the stream
                continue
    except GeneratorExit:
        # Client disconnected - log but don't treat as error
        logger.info(f"Client disconnected from {error_context} stream")
    except Exception:
        # Log full exception details server-side for debugging
        logger.exception(f"Streaming error during {error_context}")
        # Send sanitized error to client without exposing internal details
        error_payload = {"type": "error", "code": "STREAM_ERROR", "message": "An internal streaming error occurred"}
        try:
            yield f"data: {json.dumps(error_payload)}\n\n"
        except Exception:
            # Log with traceback when failing to send error event
            logger.exception(f"Failed to send error event to client during {error_context}")
    finally:
        # Always send DONE marker to properly close the stream
        try:
            yield "data: [DONE]\n\n"
        except Exception:
            logger.debug(f"Could not send DONE marker during {error_context} - connection likely closed")


# Pydantic models for API requests/responses
class ConversationCreate(BaseModel):
    """Schema for creating conversations with model configuration."""

    title: str | None = Field(None, description="Conversation title")
    model_configuration_id: str = Field(..., description="Model configuration ID")


class ConversationUpdate(BaseModel):
    """Schema for updating conversations."""

    title: str | None = None
    is_active: bool | None = None
    is_favorite: bool | None = None


class ConversationResponse(BaseModel):
    """Schema for conversation responses.
    Note: model_configuration_id can be null for legacy conversations created before
    model configuration was required. Keep Optional to avoid 500s during listing.
    """

    id: str
    user_id: str
    title: str | None
    model_configuration_id: str | None = Field(None, description="Model configuration ID")
    model_configuration: dict[str, Any] | None = Field(None, description="Model configuration details")
    is_active: bool
    is_favorite: bool = Field(default=False, description="Whether conversation is favorited")
    summary_text: str | None = Field(None, description="Stored conversation summary text")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Conversation automation metadata (title locks, summary checkpoints, etc.)",
    )
    created_at: datetime
    updated_at: datetime

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class MessageCreate(BaseModel):
    """Schema for creating messages."""

    role: str = Field(..., description="Message role (user, assistant, system)")
    content: str = Field(..., description="Message content")
    model_id: str | None = Field(None, description="Model ID for assistant messages")
    metadata: dict[str, Any] | None = Field(None, description="Message metadata")
    attachment_ids: list[str] | None = Field(None, description="List of attachment IDs to link to this message")


class MessageAttachmentInfo(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    file_size: int
    extracted_text_length: int | None = None
    is_ocr: bool | None = None
    expires_at: datetime | None
    expired: bool


class MessageResponse(BaseModel):
    """Schema for message responses."""

    id: str
    conversation_id: str
    role: str
    content: str
    model_id: str | None
    message_metadata: dict[str, Any] | None
    model_configuration: dict[str, Any] | None = Field(
        None, description="Snapshot of the model configuration used for this assistant message"
    )
    created_at: datetime
    updated_at: datetime | None = None
    parent_message_id: str | None = None
    variant_index: int | None = None
    attachments: list[MessageAttachmentInfo] = []

    class Config:
        """Configure Pydantic to work with ORM objects."""

        from_attributes = True


class SendMessageResponsePayload(BaseModel):
    """Payload returned after sending a message (default + ensemble variants)."""

    message: MessageResponse
    ensemble_alternates: list[MessageResponse] = Field(
        default_factory=list,
        description="Assistant responses generated by additional model configurations",
    )


def _extract_model_configuration(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(metadata, dict):
        return metadata.get("model_configuration")
    return None


def _message_to_response(message: Message) -> MessageResponse:
    atts: list[MessageAttachmentInfo] = []
    now = datetime.now(UTC)
    for a in getattr(message, "attachments", []) or []:
        exp = getattr(a, "expires_at", None)
        is_ocr = getattr(a, "extraction_method", None) == "ocr"
        atts.append(
            MessageAttachmentInfo(
                id=a.id,
                original_filename=a.original_filename,
                mime_type=a.mime_type,
                file_size=a.file_size,
                extracted_text_length=getattr(a, "extracted_text_length", None),
                is_ocr=is_ocr,
                expires_at=exp,
                expired=(exp is not None and exp <= now),
            )
        )

    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        model_id=message.model_id,
        message_metadata=message.message_metadata,
        model_configuration=_extract_model_configuration(message.message_metadata),
        created_at=message.created_at,
        updated_at=getattr(message, "updated_at", None),
        parent_message_id=getattr(message, "parent_message_id", None),
        variant_index=getattr(message, "variant_index", None),
        attachments=atts,
    )


class SendMessageRequest(BaseModel):
    """Schema for sending messages with LLM response."""

    message: str = Field(..., description="User message content")
    knowledge_base_id: str | None = Field(
        None,
        description="Optional specific knowledge base for RAG (overrides model config's attached KBs)",
    )
    rag_rewrite_mode: RagRewriteMode = Field(
        RagRewriteMode.RAW_QUERY,
        description="How to prepare the retrieval query (disable, raw, distill, or rewrite)",
    )
    client_temp_id: str | None = Field(
        None, description="Client-generated temp id for optimistic user placeholder replacement"
    )
    ensemble_model_configuration_ids: list[str] | None = Field(
        None,
        description="Optional additional model configuration IDs to execute alongside the conversation default",
    )
    attachment_ids: list[str] | None = Field(None, description="List of attachment IDs to include with this message")

    class Config:
        """No extra fields accepted."""

        extra = "forbid"


class AttachmentUploadResponse(BaseModel):
    attachment_id: str

    mime_type: str
    file_size: int
    extracted_text_length: int = 0
    is_ocr: bool = False


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=SuccessResponse[AttachmentUploadResponse],
    summary="Upload attachment",
    description="Upload a file attachment for a conversation; text is extracted for context.",
)
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    try:
        # Verify conversation ownership
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

        # Save attachment (validation handled by AttachmentService)
        attachment_service = AttachmentService(db)
        attachment, _ = await attachment_service.save_upload(
            conversation_id=conversation_id,
            user_id=current_user.id,
            upload_file=file,
        )

        resp = AttachmentUploadResponse(
            attachment_id=attachment.id,
            mime_type=attachment.mime_type,
            file_size=attachment.file_size,
            extracted_text_length=attachment.extracted_text_length or 0,
            is_ocr=(attachment.extraction_method == "ocr"),
        )
        return create_success_response(data=resp)
    except ValueError as e:
        # ValueError from AttachmentService contains user-friendly validation messages
        logger.error(f"Attachment validation failed: {e}")
        return create_error_response(code="INVALID_ATTACHMENT", message=str(e), status_code=400)
    except Exception as e:
        logger.error(f"Attachment upload failed: {e}")
        return create_error_response(code="ATTACHMENT_UPLOAD_FAILED", message=str(e), status_code=500)


@router.get(
    "/attachments/{attachment_id}/view",
    summary="View attachment",
    description="Retrieve attachment content for viewing or download.",
)
async def view_attachment(
    attachment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return raw attachment content with appropriate Content-Type."""
    # Fetch non-expired attachment
    now = datetime.now(UTC)
    stmt = select(Attachment).where(
        Attachment.id == attachment_id,
        (Attachment.expires_at.is_(None)) | (Attachment.expires_at > now),
    )
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        return create_error_response(
            code="ATTACHMENT_NOT_FOUND",
            message=f"Attachment '{attachment_id}' not found",
            status_code=404,
        )

    # Verify ownership via conversation
    conv_stmt = select(Conversation).where(Conversation.id == attachment.conversation_id)
    conv_result = await db.execute(conv_stmt)
    conversation = conv_result.scalar_one_or_none()

    if not conversation or conversation.user_id != current_user.id:
        return create_error_response(
            code="UNAUTHORIZED",
            message="You do not have access to this attachment",
            status_code=403,
        )

    # Read from disk using storage_path with path traversal protection
    storage_path = getattr(attachment, "storage_path", None)
    if not storage_path:
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404,
        )

    path = PathlibPath(storage_path)

    # Resolve to absolute path and verify it stays within the configured storage directory
    # This prevents path traversal attacks via tampered attachment records
    try:
        resolved_path = path.resolve(strict=True)  # strict=True raises if path doesn't exist
    except (OSError, ValueError):
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404,
        )

    # Get the configured attachment storage directory and resolve it
    storage_dir = PathlibPath(settings.chat_attachment_storage_dir).resolve()

    # Verify the resolved path is within the storage directory (prevents symlink escapes)
    try:
        resolved_path.relative_to(storage_dir)
    except ValueError:
        logger.warning(f"Path traversal attempt blocked for attachment {attachment_id}: {resolved_path}")
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404,
        )

    # Reject symlinks as an additional security measure
    if path.is_symlink():
        logger.warning(f"Symlink access blocked for attachment {attachment_id}")
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404,
        )

    if not resolved_path.is_file():
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404,
        )

    try:
        content = resolved_path.read_bytes()
    except Exception as e:
        logger.error(f"Failed to read attachment {attachment_id} from disk: {e}")
        return create_error_response(
            code="ATTACHMENT_READ_ERROR",
            message="Failed to read attachment content",
            status_code=500,
        )

    return Response(
        content=content,
        media_type=attachment.mime_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.original_filename}"'},
    )


# Conversation endpoints
@router.post(
    "/conversations",
    response_model=SuccessResponse[ConversationResponse],
    summary="Create conversation",
    description="Create a new chat conversation with model configuration.",
)
async def create_conversation(
    conversation_data: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Create a new chat conversation with model configuration."""
    try:
        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.create_conversation(
            user_id=current_user.id,
            model_configuration_id=conversation_data.model_configuration_id,
            title=conversation_data.title,
            current_user=current_user,
        )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error creating conversation: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error creating conversation: {e}")
        return create_error_response(message="Failed to create conversation", code="INTERNAL_ERROR", status_code=500)


@router.post(
    "/conversations/from-experience/{run_id}",
    response_model=SuccessResponse[ConversationResponse],
    summary="Create conversation from experience run",
    description="Create a new conversation from an experience run with the result pre-filled as the first assistant message.",
)
async def create_conversation_from_experience(
    run_id: str = Path(..., description="Experience run ID"),
    request_data: ConversationFromExperienceRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Create a new conversation from an experience run.

    The conversation will be pre-filled with the experience result as the
    first assistant message, allowing the user to ask follow-up questions.

    Args:
        run_id: Experience run ID
        request_data: Optional request data (title override)
        current_user: Current authenticated user
        db: Database session
        config_manager: Configuration manager

    Returns:
        Created conversation with pre-filled message

    Raises:
        404: Experience run not found or access denied
        400: Experience run has no result content
        403: User does not have access to the experience run

    """
    try:
        chat_service = ChatService(db, config_manager)

        # Create conversation from experience run
        conversation = await chat_service.create_conversation_from_experience_run(
            run_id=run_id,
            user_id=current_user.id,
            title_override=request_data.title if request_data else None,
        )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as exc:
        logger.error(f"Error creating conversation from experience: {exc}")
        return create_error_response(code=exc.error_code, message=exc.message, status_code=exc.status_code)
    except HTTPException as exc:
        # Handle HTTPExceptions from service layer
        logger.error(f"HTTP error creating conversation from experience: {exc.detail}")

        # Map status codes to appropriate error codes
        error_code_map = {
            404: "EXPERIENCE_RUN_NOT_FOUND",
            400: "NO_RESULT_CONTENT",
            403: "UNAUTHORIZED",
        }

        return create_error_response(
            code=error_code_map.get(exc.status_code, "INTERNAL_ERROR"),
            message=exc.detail,
            status_code=exc.status_code,
        )
    except Exception as exc:
        # Convert exception to string to avoid lazy-loading issues with SQLAlchemy objects
        error_msg = str(exc)
        error_type = type(exc).__name__
        tb = traceback.format_exc()
        logger.error(
            f"Unexpected error creating conversation from experience: {error_type}: {error_msg}\n{tb}",
            extra={"run_id": run_id, "error_type": error_type},
        )
        return create_error_response(
            message="Failed to create conversation from experience",
            code="INTERNAL_ERROR",
            status_code=500,
        )


@router.get(
    "/conversations",
    response_model=SuccessResponse[list[ConversationResponse]],
    summary="List conversations",
    description="List user's conversations with pagination.",
)
async def list_conversations(
    limit: int = Query(50, ge=1, le=100, description="Number of conversations to return"),
    offset: int = Query(0, ge=0, description="Number of conversations to skip"),
    include_inactive: bool = Query(False, description="Include inactive conversations"),
    summary_query: str | None = Query(
        None,
        description="Keyword filter applied to conversation summary text",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """List user's conversations."""
    try:
        chat_service = ChatService(db, config_manager)
        summary_terms = ChatService.normalize_summary_query(summary_query)
        conversations = await chat_service.get_user_conversations(
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            include_inactive=include_inactive,
            summary_terms=summary_terms,
        )

        response_data = [_build_conversation_response(conv) for conv in conversations]

        return create_success_response(data=response_data)

    except ShuException as e:
        logger.error(f"Error listing conversations: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error listing conversations: {e}")
        return create_error_response(message="Failed to list conversations", code="INTERNAL_ERROR", status_code=500)


@router.get(
    "/conversations/{conversation_id}",
    response_model=SuccessResponse[ConversationResponse],
    summary="Get conversation",
    description="Get a specific conversation by ID.",
)
async def get_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Get a specific conversation."""
    try:
        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.get_conversation_by_id(conversation_id)

        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        # Check if user owns the conversation
        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error getting conversation: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error getting conversation: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


@router.put(
    "/conversations/{conversation_id}",
    response_model=SuccessResponse[ConversationResponse],
    summary="Update conversation",
    description="Update conversation details.",
)
async def update_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    conversation_data: ConversationUpdate = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Update conversation details."""
    try:
        chat_service = ChatService(db, config_manager)

        # Check if conversation exists and user owns it
        existing_conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not existing_conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if existing_conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        meta_updates = None
        if conversation_data.title is not None:
            meta_updates = {"title_locked": True}

        conversation = await chat_service.update_conversation(
            conversation_id=conversation_id,
            title=conversation_data.title,
            is_active=conversation_data.is_active,
            is_favorite=conversation_data.is_favorite,
            meta_updates=meta_updates,
        )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error updating conversation: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error updating conversation: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


@router.delete(
    "/conversations/{conversation_id}",
    summary="Delete conversation",
    description="Delete a conversation (soft delete).",
)
async def delete_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Delete a conversation."""
    try:
        chat_service = ChatService(db, config_manager)

        # Check if conversation exists and user owns it
        existing_conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not existing_conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if existing_conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        await chat_service.delete_conversation(conversation_id)

        # Return 204 No Content to align with API conventions for delete operations
        return ShuResponse.no_content()

    except ShuException as e:
        logger.error(f"Error deleting conversation: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error deleting conversation: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


# Message endpoints
@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=SuccessResponse[list[MessageResponse]],
    summary="Get conversation messages",
    description="Get messages for a conversation with pagination.",
)
async def get_conversation_messages(
    conversation_id: str = Path(..., description="Conversation ID"),
    limit: int = Query(100, ge=1, le=500, description="Number of messages to return"),
    offset: int = Query(0, ge=0, description="Number of messages to skip"),
    order: Literal["asc", "desc"] = Query(
        "asc",
        description="Sort order for messages based on created_at (oldest first by default)",
    ),
    include_total: bool = Query(
        False,
        description="When true, include total_count of messages for pagination",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Get messages for a conversation."""
    try:
        chat_service = ChatService(db, config_manager)

        # Check if conversation exists and user owns it
        conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        order_desc = order == "desc"
        messages = await chat_service.get_conversation_messages(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            order_desc=order_desc,
        )

        total_count: int | None = None
        if include_total:
            total_count = await chat_service.count_conversation_messages(conversation_id)

        def to_msg_response(msg):
            atts = []
            now = datetime.now(UTC)
            for a in getattr(msg, "attachments", []) or []:
                exp = getattr(a, "expires_at", None)
                is_ocr = getattr(a, "extraction_method", None) == "ocr"
                atts.append(
                    MessageAttachmentInfo(
                        id=a.id,
                        original_filename=a.original_filename,
                        mime_type=a.mime_type,
                        file_size=a.file_size,
                        extracted_text_length=getattr(a, "extracted_text_length", None),
                        is_ocr=is_ocr,
                        expires_at=exp,
                        expired=(exp is not None and exp <= now),
                    )
                )
            return MessageResponse(
                id=msg.id,
                conversation_id=msg.conversation_id,
                role=msg.role,
                content=msg.content,
                model_id=msg.model_id,
                message_metadata=msg.message_metadata,
                model_configuration=_extract_model_configuration(msg.message_metadata),
                created_at=msg.created_at,
                updated_at=getattr(msg, "updated_at", None),
                parent_message_id=getattr(msg, "parent_message_id", None),
                variant_index=getattr(msg, "variant_index", None),
                attachments=atts,
            )

        serialized_messages = [to_msg_response(m) for m in messages]

        if include_total:
            payload = {
                "messages": serialized_messages,
                "total_count": total_count or 0,
                "order": order,
                "limit": limit,
                "offset": offset,
            }
            return create_success_response(data=payload)

        return create_success_response(data=serialized_messages)

    except ShuException as e:
        logger.error(f"Error getting messages: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error getting messages: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SuccessResponse[MessageResponse],
    summary="Add message",
    description="Add a message to a conversation.",
)
async def add_message(
    conversation_id: str = Path(..., description="Conversation ID"),
    message_data: MessageCreate = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Add a message to a conversation."""
    try:
        chat_service = ChatService(db, config_manager)

        # Check if conversation exists and user owns it
        conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        message = await chat_service.add_message(
            conversation_id=conversation_id,
            role=message_data.role,
            content=message_data.content,
            model_id=message_data.model_id,
            metadata=message_data.metadata,
            attachment_ids=message_data.attachment_ids,
        )

        # Build response manually to avoid SQLAlchemy relationship issues
        response_data = MessageResponse(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
            model_id=message.model_id,
            message_metadata=message.message_metadata,
            model_configuration=_extract_model_configuration(message.message_metadata),
            created_at=message.created_at,
            updated_at=getattr(message, "updated_at", None),
            parent_message_id=getattr(message, "parent_message_id", None),
            variant_index=getattr(message, "variant_index", None),
        )

        return create_success_response(data=response_data)

    except ShuException as e:
        logger.error(f"Error adding message: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error adding message: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


# Core chat functionality
@router.post(
    "/conversations/{conversation_id}/send",
    response_class=StreamingResponse,
    summary="Send message and get LLM response",
    description="Send a message and get an LLM response, with optional RAG context and streaming.",
)
async def send_message(
    conversation_id: str = Path(..., description="Conversation ID"),
    request_data: SendMessageRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Send a user message to a conversation and stream LLM response events as server-sent events (SSE).

    Parameters
    ----------
        conversation_id (str): ID of the conversation to send the message to.
        request_data (SendMessageRequest): Payload containing the user message and optional parameters (knowledge_base_id, rag_rewrite_mode, client_temp_id, ensemble_model_configuration_ids, attachment_ids).

    Returns
    -------
        StreamingResponse | Response: A StreamingResponse that yields SSE payloads where each event is a JSON object representing LLM or system events; the stream always concludes with a terminal `data: [DONE]` event. If validation or permission checks fail, returns a standardized error response with an error code and HTTP status.

    """
    try:
        # Note: LLM rate limiting is now per-provider, enforced in chat_streaming.py
        chat_service = ChatService(db, config_manager)

        if not request_data.message:
            return create_error_response(
                code="INVALID_REQUEST",
                message="The user message can not be empty.",
                status_code=400,
            )

        # Check if conversation exists and user owns it
        conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        # Send message and get response
        async def stream_generator():
            event_gen = await chat_service.send_message(
                conversation_id=conversation_id,
                user_message=request_data.message,
                current_user=current_user,
                knowledge_base_id=request_data.knowledge_base_id,
                rag_rewrite_mode=request_data.rag_rewrite_mode,
                client_temp_id=getattr(request_data, "client_temp_id", None),
                ensemble_model_configuration_ids=request_data.ensemble_model_configuration_ids,
                attachment_ids=request_data.attachment_ids,
            )
            async for data in create_sse_stream_generator(event_gen, "send_message"):
                yield data

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    except ShuException as e:
        logger.error(f"Error sending message: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code, details=e.details)
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


class ModelSwitchRequest(BaseModel):
    """Schema for switching conversation models."""

    model_configuration_id: str | None = Field(
        None, description="New model configuration ID to associate with the conversation"
    )


def _serialize_model_configuration(model_config: Any | None) -> dict[str, Any] | None:
    """Convert a model configuration ORM object into a plain dictionary for API responses."""
    if not model_config:
        return None

    llm_provider = getattr(model_config, "llm_provider", None)
    prompt = getattr(model_config, "prompt", None)
    knowledge_bases = getattr(model_config, "knowledge_bases", []) or []

    return {
        "id": getattr(model_config, "id", None),
        "name": getattr(model_config, "name", None),
        "description": getattr(model_config, "description", None),
        "llm_provider_id": getattr(model_config, "llm_provider_id", None),
        "llm_provider": {
            "id": getattr(llm_provider, "id", None),
            "name": getattr(llm_provider, "name", None),
            "provider_type": getattr(llm_provider, "provider_type", None),
        }
        if llm_provider
        else None,
        "model_name": getattr(model_config, "model_name", None),
        "prompt": {
            "id": getattr(prompt, "id", None),
            "name": getattr(prompt, "name", None),
            "content": getattr(prompt, "content", None),
        }
        if prompt
        else None,
        "knowledge_bases": [
            {
                "id": getattr(kb, "id", None),
                "name": getattr(kb, "name", None),
                "description": getattr(kb, "description", None),
            }
            for kb in knowledge_bases
        ],
        "has_knowledge_bases": len(knowledge_bases) > 0,
    }


def _build_conversation_response(conversation) -> ConversationResponse:
    """Construct a ConversationResponse populated with serialized relationships."""
    return ConversationResponse(
        id=conversation.id,
        user_id=conversation.user_id,
        title=conversation.title,
        model_configuration_id=conversation.model_configuration_id,
        model_configuration=_serialize_model_configuration(getattr(conversation, "model_configuration", None)),
        is_active=conversation.is_active,
        is_favorite=getattr(conversation, "is_favorite", False),
        summary_text=getattr(conversation, "summary_text", None),
        meta=getattr(conversation, "meta", {}) or {},
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.post(
    "/conversations/{conversation_id}/switch-model",
    response_model=SuccessResponse[ConversationResponse],
    summary="Switch conversation model",
    description="Switch the LLM model for a conversation while preserving context.",
)
async def switch_conversation_model(
    conversation_id: str = Path(..., description="Conversation ID"),
    request_data: ModelSwitchRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Switch the LLM model for a conversation."""
    try:
        chat_service = ChatService(db, config_manager)

        # Check if conversation exists and user owns it
        conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404,
            )

        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", status_code=403)

        if not request_data.model_configuration_id:
            return create_error_response(
                code="INVALID_MODEL_SWITCH_REQUEST",
                message="A model_configuration_id must be provided to switch models.",
                status_code=400,
            )

        updated_conversation = await chat_service.switch_conversation_model(
            conversation_id=conversation_id,
            new_model_configuration_id=request_data.model_configuration_id,
            current_user=current_user,
        )

        return create_success_response(data=_build_conversation_response(updated_conversation))

    except ShuException as e:
        logger.error(f"Error switching conversation model: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error switching conversation model: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)


class RegenerateMessageRequest(BaseModel):
    parent_message_id: str | None = Field(None, description="Explicit parent message ID for this variant group")
    rag_rewrite_mode: RagRewriteMode = Field(
        RagRewriteMode.RAW_QUERY, description="How to prepare the query for RAG during regeneration"
    )


@router.post(
    "/messages/{message_id}/regenerate",
    summary="Regenerate an assistant message",
    description="Re-run a previous assistant response with the same context and attachments as its preceding user message.",
)
async def regenerate_message(
    message_id: str = Path(..., description="Message ID of the assistant message to regenerate"),
    request: RegenerateMessageRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Stream a regenerated assistant message as Server-Sent Events (SSE).

    Streams JSON-encoded event payloads produced during regeneration of the assistant message identified by `message_id`, then emits a final "data: [DONE]" event when the stream completes. If a streaming error occurs, a JSON error payload is emitted before the final DONE event.

    Parameters
    ----------
        message_id (str): ID of the assistant message to regenerate.
        request (RegenerateMessageRequest): Optional regeneration options (e.g., `parent_message_id`, `rag_rewrite_mode`).

    Returns
    -------
        StreamingResponse: An SSE stream where each event is prefixed with `data: ` and contains a JSON payload; the stream ends with `data: [DONE]`.

    """
    try:
        # Note: LLM rate limiting is now per-provider, enforced in chat_streaming.py
        chat_service = ChatService(db, config_manager)

        async def stream_generator():
            event_gen = await chat_service.regenerate_message(
                message_id=message_id,
                current_user=current_user,
                parent_message_id=request.parent_message_id,
                rag_rewrite_mode=request.rag_rewrite_mode,
            )
            async for data in create_sse_stream_generator(event_gen, "regenerate_message"):
                yield data

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    except ShuException as e:
        logger.error(f"Error regenerating message: {e}")
        return create_error_response(code=e.error_code, message=e.message, status_code=e.status_code)
    except Exception as e:
        logger.error(f"Unexpected error regenerating message: {e}")
        return create_error_response(message="Internal server error", code="INTERNAL_ERROR", status_code=500)
