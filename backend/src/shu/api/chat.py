"""
Chat API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing chat conversations,
messages, and LLM interactions.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any, List, Optional, Union, Literal
from pydantic import BaseModel, Field
import logging
import json
from datetime import datetime, timezone
from pathlib import Path as PathlibPath

from .dependencies import get_db
from ..auth.rbac import get_current_user
from ..core.config import get_settings_instance, get_config_manager_dependency, ConfigurationManager
from ..core.rate_limiting import get_rate_limit_service, RateLimitResult
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.response import ShuResponse, create_success_response, create_error_response
from ..schemas.envelope import SuccessResponse
from ..schemas.query import RagRewriteMode
from ..services.chat_service import ChatService
from ..services.chat_streaming import ProviderResponseEvent
from ..services.attachment_service import AttachmentService
from ..auth.models import User
from ..models.llm_provider import Message, Conversation
from ..models.attachment import Attachment

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

settings = get_settings_instance()


async def check_llm_rate_limit(user_id: str, estimated_tokens: int = 100) -> RateLimitResult:
    """Check LLM rate limits (RPM and TPM).

    Returns the most restrictive rate limit result (RPM or TPM).

    Args:
        user_id: User identifier
        estimated_tokens: Estimated tokens for this request

    Returns:
        RateLimitResult (most restrictive of RPM or TPM)

    Raises:
        HTTPException: If rate limit exceeded
    """
    rate_limit_service = get_rate_limit_service()

    if not rate_limit_service.enabled:
        return RateLimitResult(allowed=True, remaining=999, limit=999)

    # Check RPM first
    rpm_result = await rate_limit_service.check_llm_rpm_limit(user_id)
    if not rpm_result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "message": "LLM request rate limit exceeded. Please try again later.",
                    "code": "LLM_RPM_LIMIT_EXCEEDED",
                    "details": {"retry_after": rpm_result.retry_after_seconds},
                }
            },
            headers=rpm_result.to_headers(),
        )

    # Check TPM
    tpm_result = await rate_limit_service.check_llm_tpm_limit(user_id, estimated_tokens)
    if not tpm_result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "message": "LLM token rate limit exceeded. Please try again later.",
                    "code": "LLM_TPM_LIMIT_EXCEEDED",
                    "details": {"retry_after": tpm_result.retry_after_seconds},
                }
            },
            headers=tpm_result.to_headers(),
        )

    # Return the more restrictive result
    return rpm_result if rpm_result.remaining < tpm_result.remaining else tpm_result


# Pydantic models for API requests/responses
class ConversationCreate(BaseModel):
    """Schema for creating conversations with model configuration."""
    title: Optional[str] = Field(None, description="Conversation title")
    model_configuration_id: str = Field(..., description="Model configuration ID")


class ConversationUpdate(BaseModel):
    """Schema for updating conversations."""
    title: Optional[str] = None
    is_active: Optional[bool] = None


class ConversationResponse(BaseModel):
    """Schema for conversation responses.
    Note: model_configuration_id can be null for legacy conversations created before
    model configuration was required. Keep Optional to avoid 500s during listing.
    """
    id: str
    user_id: str
    title: Optional[str]
    model_configuration_id: Optional[str] = Field(None, description="Model configuration ID")
    model_configuration: Optional[Dict[str, Any]] = Field(None, description="Model configuration details")
    is_active: bool
    summary_text: Optional[str] = Field(None, description="Stored conversation summary text")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Conversation automation metadata (title locks, summary checkpoints, etc.)")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    """Schema for creating messages."""
    role: str = Field(..., description="Message role (user, assistant, system)")
    content: str = Field(..., description="Message content")
    model_id: Optional[str] = Field(None, description="Model ID for assistant messages")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Message metadata")


class MessageAttachmentInfo(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    file_size: int
    extracted_text_length: Optional[int] = None
    is_ocr: Optional[bool] = None
    expires_at: Optional[datetime]
    expired: bool


class MessageResponse(BaseModel):
    """Schema for message responses."""
    id: str
    conversation_id: str
    role: str
    content: str
    model_id: Optional[str]
    message_metadata: Optional[Dict[str, Any]]
    model_configuration: Optional[Dict[str, Any]] = Field(
        None,
        description="Snapshot of the model configuration used for this assistant message"
    )
    created_at: datetime
    updated_at: Optional[datetime] = None
    parent_message_id: Optional[str] = None
    variant_index: Optional[int] = None
    attachments: List[MessageAttachmentInfo] = []

    class Config:
        from_attributes = True


class SendMessageResponsePayload(BaseModel):
    """Payload returned after sending a message (default + ensemble variants)."""
    message: MessageResponse
    ensemble_alternates: List[MessageResponse] = Field(
        default_factory=list,
        description="Assistant responses generated by additional model configurations"
    )


def _extract_model_configuration(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(metadata, dict):
        return metadata.get("model_configuration")
    return None


def _message_to_response(message: Message) -> MessageResponse:
    atts: List[MessageAttachmentInfo] = []
    now = datetime.now(timezone.utc)
    for a in getattr(message, 'attachments', []) or []:
        exp = getattr(a, 'expires_at', None)
        is_ocr = (getattr(a, 'extraction_method', None) == 'ocr')
        atts.append(MessageAttachmentInfo(
            id=a.id,
            original_filename=a.original_filename,
            mime_type=a.mime_type,
            file_size=a.file_size,
            extracted_text_length=getattr(a, 'extracted_text_length', None),
            is_ocr=is_ocr,
            expires_at=exp,
            expired=(exp is not None and exp <= now)
        ))

    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        model_id=message.model_id,
        message_metadata=message.message_metadata,
        model_configuration=_extract_model_configuration(message.message_metadata),
        created_at=message.created_at,
        updated_at=getattr(message, 'updated_at', None),
        parent_message_id=getattr(message, 'parent_message_id', None),
        variant_index=getattr(message, 'variant_index', None),
        attachments=atts,
    )


class SendMessageRequest(BaseModel):
    """Schema for sending messages with LLM response."""
    message: str = Field(..., description="User message content")
    knowledge_base_id: Optional[str] = Field(
        None,
        description="Optional specific knowledge base for RAG (overrides model config's attached KBs)"
    )
    rag_rewrite_mode: RagRewriteMode = Field(
        RagRewriteMode.RAW_QUERY,
        description="How to prepare the retrieval query (disable, raw, distill, or rewrite)"
    )
    client_temp_id: Optional[str] = Field(
        None,
        description="Client-generated temp id for optimistic user placeholder replacement"
    )
    ensemble_model_configuration_ids: Optional[List[str]] = Field(
        None,
        description="Optional additional model configuration IDs to execute alongside the conversation default"
    )
    attachment_ids: Optional[List[str]] = Field(
        None,
        description="List of attachment IDs to include with this message"
    )

    class Config:
        extra = 'forbid'


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
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    try:
        # Verify conversation ownership
        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.get_conversation_by_id(conversation_id)
        if not conversation:
            return create_error_response(code="CONVERSATION_NOT_FOUND", message=f"Conversation '{conversation_id}' not found", status_code=404)
        if conversation.user_id != current_user.id:
            return create_error_response(code="UNAUTHORIZED", message="You do not have access to this conversation", status_code=403)

        settings = get_settings_instance()

        # Validate filename/type
        filename = file.filename or "upload"
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if ext not in [t.lower() for t in settings.chat_attachment_allowed_types]:
            return create_error_response(code="UNSUPPORTED_TYPE", message=f"Unsupported file type: {ext}", status_code=400)

        # Read bytes and enforce size
        data = await file.read()
        if len(data) > settings.chat_attachment_max_size:
            return create_error_response(code="PAYLOAD_TOO_LARGE", message=f"File too large. Max {settings.chat_attachment_max_size} bytes", status_code=413)

        # Persist (non-blocking OCR for PDFs)
        attachment_service = AttachmentService(db)
        attachment, _ = await attachment_service.save_upload(
            conversation_id=conversation_id,
            user_id=current_user.id,
            filename=filename,
            file_bytes=data,
        )

        resp = AttachmentUploadResponse(
            attachment_id=attachment.id,
            mime_type=attachment.mime_type,
            file_size=attachment.file_size,
            extracted_text_length=attachment.extracted_text_length or 0,
            is_ocr=(attachment.extraction_method == 'ocr')
        )
        return create_success_response(data=resp)
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
    now = datetime.now(timezone.utc)
    stmt = select(Attachment).where(
        Attachment.id == attachment_id,
        (Attachment.expires_at.is_(None)) | (Attachment.expires_at > now)
    )
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        return create_error_response(
            code="ATTACHMENT_NOT_FOUND",
            message=f"Attachment '{attachment_id}' not found",
            status_code=404
        )

    # Verify ownership via conversation
    conv_stmt = select(Conversation).where(Conversation.id == attachment.conversation_id)
    conv_result = await db.execute(conv_stmt)
    conversation = conv_result.scalar_one_or_none()

    if not conversation or conversation.user_id != current_user.id:
        return create_error_response(
            code="UNAUTHORIZED",
            message="You do not have access to this attachment",
            status_code=403
        )


    # Read from disk using storage_path with path traversal protection
    storage_path = getattr(attachment, "storage_path", None)
    if not storage_path:
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404
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
            status_code=404
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
            status_code=404
        )

    # Reject symlinks as an additional security measure
    if path.is_symlink():
        logger.warning(f"Symlink access blocked for attachment {attachment_id}")
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404
        )

    if not resolved_path.is_file():
        return create_error_response(
            code="ATTACHMENT_CONTENT_UNAVAILABLE",
            message="Attachment content is not available",
            status_code=404
        )

    try:
        content = resolved_path.read_bytes()
    except Exception as e:
        logger.error(f"Failed to read attachment {attachment_id} from disk: {e}")
        return create_error_response(
            code="ATTACHMENT_READ_ERROR",
            message="Failed to read attachment content",
            status_code=500
        )

    return Response(
        content=content,
        media_type=attachment.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{attachment.original_filename}"'
        }
    )

# Conversation endpoints
@router.post(
    "/conversations",
    response_model=SuccessResponse[ConversationResponse],
    summary="Create conversation",
    description="Create a new chat conversation with model configuration."
)
async def create_conversation(
    conversation_data: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    """Create a new chat conversation with model configuration."""
    try:
        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.create_conversation(
            user_id=current_user.id,
            model_configuration_id=conversation_data.model_configuration_id,
            title=conversation_data.title,
            current_user=current_user
        )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error creating conversation: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error creating conversation: {e}")
        return create_error_response(
            message="Failed to create conversation",
            code="INTERNAL_ERROR",
            status_code=500
        )



@router.get(
    "/conversations",
    response_model=SuccessResponse[List[ConversationResponse]],
    summary="List conversations",
    description="List user's conversations with pagination."
)
async def list_conversations(
    limit: int = Query(50, ge=1, le=100, description="Number of conversations to return"),
    offset: int = Query(0, ge=0, description="Number of conversations to skip"),
    include_inactive: bool = Query(False, description="Include inactive conversations"),
    summary_query: Optional[str] = Query(
        None,
        description="Keyword filter applied to conversation summary text",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error listing conversations: {e}")
        return create_error_response(
            message="Failed to list conversations",
            code="INTERNAL_ERROR",
            status_code=500
        )




@router.get(
    "/conversations/{conversation_id}",
    response_model=SuccessResponse[ConversationResponse],
    summary="Get conversation",
    description="Get a specific conversation by ID."
)
async def get_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    """Get a specific conversation."""
    try:
        chat_service = ChatService(db, config_manager)
        conversation = await chat_service.get_conversation_by_id(conversation_id)

        if not conversation:
            return create_error_response(
                code="CONVERSATION_NOT_FOUND",
                message=f"Conversation '{conversation_id}' not found",
                status_code=404
            )

        # Check if user owns the conversation
        if conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error getting conversation: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error getting conversation: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


@router.put(
    "/conversations/{conversation_id}",
    response_model=SuccessResponse[ConversationResponse],
    summary="Update conversation",
    description="Update conversation details."
)
async def update_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    conversation_data: ConversationUpdate = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
                status_code=404
            )

        if existing_conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        meta_updates = None
        if conversation_data.title is not None:
            meta_updates = {"title_locked": True}

        conversation = await chat_service.update_conversation(
            conversation_id=conversation_id,
            title=conversation_data.title,
            is_active=conversation_data.is_active,
            meta_updates=meta_updates
        )

        return create_success_response(data=_build_conversation_response(conversation))

    except ShuException as e:
        logger.error(f"Error updating conversation: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error updating conversation: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


@router.delete(
    "/conversations/{conversation_id}",
    summary="Delete conversation",
    description="Delete a conversation (soft delete)."
)
async def delete_conversation(
    conversation_id: str = Path(..., description="Conversation ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
                status_code=404
            )

        if existing_conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        await chat_service.delete_conversation(conversation_id)

        # Return 204 No Content to align with API conventions for delete operations
        return ShuResponse.no_content()

    except ShuException as e:
        logger.error(f"Error deleting conversation: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error deleting conversation: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


# Message endpoints
@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=SuccessResponse[List[MessageResponse]],
    summary="Get conversation messages",
    description="Get messages for a conversation with pagination."
)
async def get_conversation_messages(
    conversation_id: str = Path(..., description="Conversation ID"),
    limit: int = Query(100, ge=1, le=500, description="Number of messages to return"),
    offset: int = Query(0, ge=0, description="Number of messages to skip"),
    order: Literal['asc', 'desc'] = Query(
        'asc',
        description="Sort order for messages based on created_at (oldest first by default)",
    ),
    include_total: bool = Query(
        False,
        description="When true, include total_count of messages for pagination",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
                status_code=404
            )

        if conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        order_desc = order == 'desc'
        messages = await chat_service.get_conversation_messages(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            order_desc=order_desc,
        )

        total_count: Optional[int] = None
        if include_total:
            total_count = await chat_service.count_conversation_messages(conversation_id)

        def to_msg_response(msg):
            atts = []
            now = datetime.now(timezone.utc)
            for a in getattr(msg, 'attachments', []) or []:
                exp = getattr(a, 'expires_at', None)
                is_ocr = (getattr(a, 'extraction_method', None) == 'ocr')
                atts.append(MessageAttachmentInfo(
                    id=a.id,
                    original_filename=a.original_filename,
                    mime_type=a.mime_type,
                    file_size=a.file_size,
                    extracted_text_length=getattr(a, 'extracted_text_length', None),
                    is_ocr=is_ocr,
                    expires_at=exp,
                    expired=(exp is not None and exp <= now)
                ))
            return MessageResponse(
                id=msg.id,
                conversation_id=msg.conversation_id,
                role=msg.role,
                content=msg.content,
                model_id=msg.model_id,
                message_metadata=msg.message_metadata,
                model_configuration=_extract_model_configuration(msg.message_metadata),
                created_at=msg.created_at,
                updated_at=getattr(msg, 'updated_at', None),
                parent_message_id=getattr(msg, 'parent_message_id', None),
                variant_index=getattr(msg, 'variant_index', None),
                attachments=atts
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
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error getting messages: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SuccessResponse[MessageResponse],
    summary="Add message",
    description="Add a message to a conversation."
)
async def add_message(
    conversation_id: str = Path(..., description="Conversation ID"),
    message_data: MessageCreate = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
                status_code=404
            )

        if conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        message = await chat_service.add_message(
            conversation_id=conversation_id,
            role=message_data.role,
            content=message_data.content,
            model_id=message_data.model_id,
            metadata=message_data.metadata,
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
            updated_at=getattr(message, 'updated_at', None),
            parent_message_id=getattr(message, 'parent_message_id', None),
            variant_index=getattr(message, 'variant_index', None)
        )

        return create_success_response(data=response_data)

    except ShuException as e:
        logger.error(f"Error adding message: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error adding message: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


# Core chat functionality
@router.post(
    "/conversations/{conversation_id}/send",
    response_class=StreamingResponse,
    summary="Send message and get LLM response",
    description="Send a message and get an LLM response, with optional RAG context and streaming."
)
async def send_message(
    conversation_id: str = Path(..., description="Conversation ID"),
    request_data: SendMessageRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    """Send a message and get LLM response."""
    try:
        # Check LLM rate limits (RPM and TPM)
        estimated_tokens = len(request_data.message.split()) * 2 if request_data.message else 100
        await check_llm_rate_limit(str(current_user.id), estimated_tokens)
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
                status_code=404
            )

        if conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        # Send message and get response
        async def stream_generator():
            try:
                async for event in await chat_service.send_message(
                    conversation_id=conversation_id,
                    user_message=request_data.message,
                    current_user=current_user,
                    knowledge_base_id=request_data.knowledge_base_id,
                    rag_rewrite_mode=request_data.rag_rewrite_mode,
                    client_temp_id=getattr(request_data, "client_temp_id", None),
                    ensemble_model_configuration_ids=request_data.ensemble_model_configuration_ids,
                    attachment_ids=request_data.attachment_ids,
                ):
                    payload = event.to_dict()
                    yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                logger.exception("Streaming error during send_message")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache"
            }
        )

    except ShuException as e:
        logger.error(f"Error sending message: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code,
            details=e.details
        )
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


class ModelSwitchRequest(BaseModel):
    """Schema for switching conversation models."""
    model_configuration_id: Optional[str] = Field(
        None,
        description="New model configuration ID to associate with the conversation"
    )


def _serialize_model_configuration(model_config: Optional[Any]) -> Optional[Dict[str, Any]]:
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
            "provider_type": getattr(llm_provider, "provider_type", None)
        } if llm_provider else None,
        "model_name": getattr(model_config, "model_name", None),
        "prompt": {
            "id": getattr(prompt, "id", None),
            "name": getattr(prompt, "name", None),
            "content": getattr(prompt, "content", None)
        } if prompt else None,
        "knowledge_bases": [
            {
                "id": getattr(kb, "id", None),
                "name": getattr(kb, "name", None),
                "description": getattr(kb, "description", None)
            }
            for kb in knowledge_bases
        ],
        "has_knowledge_bases": len(knowledge_bases) > 0
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
        summary_text=getattr(conversation, "summary_text", None),
        meta=getattr(conversation, "meta", {}) or {},
        created_at=conversation.created_at,
        updated_at=conversation.updated_at
    )

@router.post(
    "/conversations/{conversation_id}/switch-model",
    response_model=SuccessResponse[ConversationResponse],
    summary="Switch conversation model",
    description="Switch the LLM model for a conversation while preserving context."
)
async def switch_conversation_model(
    conversation_id: str = Path(..., description="Conversation ID"),
    request_data: ModelSwitchRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
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
                status_code=404
            )

        if conversation.user_id != current_user.id:
            return create_error_response(
                code="UNAUTHORIZED",

                status_code=403
            )

        if not request_data.model_configuration_id:
            return create_error_response(
                code="INVALID_MODEL_SWITCH_REQUEST",
                message="A model_configuration_id must be provided to switch models.",
                status_code=400
            )

        updated_conversation = await chat_service.switch_conversation_model(
            conversation_id=conversation_id,
            new_model_configuration_id=request_data.model_configuration_id,
            current_user=current_user
        )

        return create_success_response(data=_build_conversation_response(updated_conversation))

    except ShuException as e:
        logger.error(f"Error switching conversation model: {e}")
        return create_error_response(
            code=e.error_code,
            message=e.message,
            status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error switching conversation model: {e}")
        return create_error_response(
            message="Internal server error",
            code="INTERNAL_ERROR",
            status_code=500
        )


class RegenerateMessageRequest(BaseModel):
    parent_message_id: Optional[str] = Field(None, description="Explicit parent message ID for this variant group")
    rag_rewrite_mode: RagRewriteMode = Field(
        RagRewriteMode.RAW_QUERY,
        description="How to prepare the query for RAG during regeneration"
    )


@router.post(
    "/messages/{message_id}/regenerate",
    summary="Regenerate an assistant message",
    description="Re-run a previous assistant response with the same context and attachments as its preceding user message."
)
async def regenerate_message(
    message_id: str = Path(..., description="Message ID of the assistant message to regenerate"),
    request: RegenerateMessageRequest = ...,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency)
):
    try:
        # Check LLM rate limits
        await check_llm_rate_limit(str(current_user.id), 100)

        chat_service = ChatService(db, config_manager)

        async def stream_generator():
            try:
                async for event in await chat_service.regenerate_message(
                    message_id=message_id,
                    current_user=current_user,
                    parent_message_id=request.parent_message_id,
                    rag_rewrite_mode=request.rag_rewrite_mode,
                ):
                    payload = event.to_dict()
                    yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                logger.exception("Streaming error during regenerate_message")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )

    except ShuException as e:
        logger.error(f"Error regenerating message: {e}")
        return create_error_response(
            code=e.error_code, message=e.message, status_code=e.status_code
        )
    except Exception as e:
        logger.error(f"Unexpected error regenerating message: {e}")
        return create_error_response(
            message="Internal server error", code="INTERNAL_ERROR", status_code=500
        )
