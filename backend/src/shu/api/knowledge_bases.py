"""Knowledge Base API endpoints for Shu RAG Backend.

This module provides REST API endpoints for managing knowledge bases,
including CRUD operations, statistics, and multi-source support.
"""

import mimetypes
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import (
    get_current_user,
    require_admin,
    require_kb_write_access,
    require_power_user,
)
from ..core.config import get_settings_instance
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.queue_backend import get_queue_backend
from ..core.response import ShuResponse
from ..ingestion.filetypes import MAGIC_BYTES
from ..schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate, RAGConfig
from ..services.document_service import DocumentService
from ..services.ingestion_service import ingest_document as ingest_document_service
from ..services.kb_import_export_service import KBImportExportService
from ..services.knowledge_base_service import (
    KnowledgeBaseService,
    resolve_personal_kb_name,
    resolve_personal_kb_slug_token,
)
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])

settings = get_settings_instance()


@router.get(
    "",
    summary="List knowledge bases",
    description="List all knowledge bases with optional filtering and pagination.",
)
async def list_knowledge_bases(
    limit: int = Query(50, ge=1, le=100, description="Number of knowledge bases to return"),
    offset: int = Query(0, ge=0, description="Number of knowledge bases to skip"),
    search: str | None = Query(None, description="Search term for knowledge base names"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List knowledge bases with optional filtering and pagination.

    Args:
        limit: Maximum number of knowledge bases to return
        offset: Number of knowledge bases to skip for pagination
        search: Optional search term for filtering by name
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing knowledge base list

    Raises:
        HTTPException: If database error occurs

    """
    logger.info("Listing knowledge bases", extra={"limit": limit, "offset": offset, "search": search})

    try:
        kb_service = KnowledgeBaseService(db)
        knowledge_bases, total_count = await kb_service.list_knowledge_bases(
            user_id=str(current_user.id),
            limit=limit,
            offset=offset,
            search=search,
        )

        # Format response using denormalized stats (no per-KB COUNT queries)
        kb_items = []
        for kb in knowledge_bases:
            kb_items.append(
                {
                    "id": kb.id,
                    "slug": kb.slug,
                    "name": kb.name,
                    "description": kb.description,
                    "sync_enabled": kb.sync_enabled,
                    "embedding_model": kb.embedding_model,
                    "chunk_size": kb.chunk_size,
                    "chunk_overlap": kb.chunk_overlap,
                    "status": kb.status or "active",
                    "embedding_status": kb.embedding_status or "current",
                    "re_embedding_progress": kb.re_embedding_progress,
                    "import_progress": kb.import_progress,
                    "document_count": kb.document_count,
                    "total_chunks": kb.total_chunks,
                    "is_personal": kb.is_personal,
                    "owner_id": kb.owner_id,
                    "last_sync_at": kb.last_sync_at.isoformat() if kb.last_sync_at is not None else None,
                    "created_at": kb.created_at.isoformat(),
                    "updated_at": kb.updated_at.isoformat(),
                }
            )

        response_data = {
            "items": kb_items,
            "total": total_count,
            "page": (offset // limit) + 1,
            "size": limit,
            "pages": (total_count + limit - 1) // limit,
        }

        return ShuResponse.success(response_data)

    except ShuException as e:
        logger.error("Failed to list knowledge bases", extra={"error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error listing knowledge bases", extra={"error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/stats",
    summary="Get knowledge base statistics",
    description="Get overall statistics for all knowledge bases.",
)
async def get_knowledge_base_stats(current_user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Get overall statistics for all knowledge bases.

    Args:
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing statistics

    Raises:
        HTTPException: If database error occurs

    """
    logger.info("Getting knowledge base statistics")

    try:
        kb_service = KnowledgeBaseService(db)
        stats = await kb_service.get_overall_knowledge_base_stats()

        return ShuResponse.success(stats)

    except ShuException as e:
        logger.error("Failed to get knowledge base statistics", extra={"error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_STATS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error getting knowledge base statistics", extra={"error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific knowledge base by ID.

    Returns detailed information about a knowledge base including
    its configuration, statistics, and sync settings.
    """
    logger.info("API: Get knowledge base", extra={"kb_id": kb_id})

    try:
        service = KnowledgeBaseService(db)
        result = await service.get_knowledge_base(kb_id, str(current_user.id))

        # Get actual statistics
        stats = await service.get_knowledge_base_stats(kb_id)

        # Convert SQLAlchemy model to dictionary
        response_data = {
            "id": result.id,
            "slug": result.slug,
            "name": result.name,
            "description": result.description,
            "sync_enabled": result.sync_enabled,
            "embedding_model": result.embedding_model,
            "chunk_size": result.chunk_size,
            "chunk_overlap": result.chunk_overlap,
            "status": result.status,
            "document_count": stats["document_count"],
            "total_chunks": stats["total_chunks"],
            "is_personal": result.is_personal,
            "owner_id": result.owner_id,
            "last_sync_at": result.last_sync_at.isoformat() if result.last_sync_at is not None else None,
            "created_at": result.created_at.isoformat(),
            "updated_at": result.updated_at.isoformat(),
        }

        logger.info("API: Retrieved knowledge base", extra={"kb_id": kb_id, "kb_name": result.name})

        return ShuResponse.success(response_data)

    except ShuException as e:
        logger.error("API: Failed to get knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_GET_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to get knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


def _serialize_kb_create_response(result) -> dict:
    """Shape a KnowledgeBase row into the create-endpoint envelope payload."""
    return {
        "id": result.id,
        "slug": result.slug,
        "name": result.name,
        "description": result.description,
        "sync_enabled": result.sync_enabled,
        "embedding_model": result.embedding_model,
        "chunk_size": result.chunk_size,
        "chunk_overlap": result.chunk_overlap,
        "status": result.status,
        "document_count": result.document_count or 0,
        "total_chunks": result.total_chunks or 0,
        "is_personal": result.is_personal,
        "owner_id": result.owner_id,
        "last_sync_at": result.last_sync_at.isoformat() if result.last_sync_at is not None else None,
        "created_at": result.created_at.isoformat(),
        "updated_at": result.updated_at.isoformat(),
    }


@router.post(
    "",
    summary="Create a knowledge base (power user / admin only)",
    description=(
        "Create a new non-personal knowledge base. Restricted to power_user and "
        "admin roles. Regular users provision their Personal Knowledge KB via "
        "POST /knowledge-bases/personal."
    ),
)
async def create_knowledge_base(
    kb_data: KnowledgeBaseCreate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new non-personal knowledge base."""
    logger.info("API: Create knowledge base", extra={"kb_name": kb_data.name})

    try:
        service = KnowledgeBaseService(db)
        result = await service.create_knowledge_base(kb_data, owner_id=current_user.id)
        logger.info("API: Created knowledge base", extra={"kb_id": result.id, "kb_name": result.name})
        return ShuResponse.created(_serialize_kb_create_response(result))

    except ShuException as e:
        logger.error("API: Failed to create knowledge base", extra={"kb_name": kb_data.name, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_CREATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to create knowledge base", extra={"kb_name": kb_data.name, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post(
    "/personal",
    summary="Ensure the caller's Personal Knowledge KB exists",
    description=(
        "Idempotently provision the caller's Personal Knowledge KB. Returns the "
        "existing row if one already exists (heal-on-flag-missing if needed), "
        "otherwise creates a new owner-scoped KB. Available to any authenticated "
        "user; the slug is owner-scoped so each user has exactly one Personal KB. "
        "The display name is derived from the user's identity server-side."
    ),
)
async def ensure_personal_knowledge_base(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ensure the caller's Personal Knowledge KB exists (idempotent)."""
    logger.info("API: Ensure personal knowledge base", extra={"user_id": current_user.id})

    try:
        service = KnowledgeBaseService(db)
        display_name = resolve_personal_kb_name(current_user)
        slug_token = resolve_personal_kb_slug_token(current_user)
        result = await service.ensure_personal_knowledge_base(
            owner_id=current_user.id, display_name=display_name, slug_token=slug_token
        )
        logger.info(
            "API: Resolved personal knowledge base",
            extra={"kb_id": result.id, "user_id": current_user.id},
        )
        return ShuResponse.created(_serialize_kb_create_response(result))

    except ShuException as e:
        logger.error(
            "API: Failed to ensure personal knowledge base",
            extra={"user_id": current_user.id, "error": str(e)},
        )
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_CREATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(
            "API: Failed to ensure personal knowledge base",
            extra={"user_id": current_user.id, "error": str(e)},
        )
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.put("/{kb_id}")
async def update_knowledge_base(
    kb_id: str,
    update_data: KnowledgeBaseUpdate,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing knowledge base.

    Updates the configuration of an existing knowledge base.
    Changes to processing parameters (chunk size, embedding model)
    will only affect newly processed documents.
    """
    logger.info("API: Update knowledge base", extra={"kb_id": kb_id})

    try:
        service = KnowledgeBaseService(db)
        result = await service.update_knowledge_base(kb_id, update_data)

        # Get actual statistics
        stats = await service.get_knowledge_base_stats(kb_id)

        # Convert SQLAlchemy model to dictionary
        response_data = {
            "id": result.id,
            "slug": result.slug,
            "name": result.name,
            "description": result.description,
            "sync_enabled": result.sync_enabled,
            "embedding_model": result.embedding_model,
            "chunk_size": result.chunk_size,
            "chunk_overlap": result.chunk_overlap,
            "status": result.status,
            "document_count": stats["document_count"],
            "total_chunks": stats["total_chunks"],
            "is_personal": result.is_personal,
            "owner_id": result.owner_id,
            "last_sync_at": result.last_sync_at.isoformat() if result.last_sync_at is not None else None,
            "created_at": result.created_at.isoformat(),
            "updated_at": result.updated_at.isoformat(),
        }

        logger.info("API: Updated knowledge base", extra={"kb_id": kb_id, "kb_name": result.name})

        return ShuResponse.success(response_data)

    except ShuException as e:
        logger.error("API: Failed to update knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_UPDATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to update knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a knowledge base.

    Deletes a knowledge base and all its documents and chunks.
    This operation cannot be undone.
    """
    logger.info("API: Delete knowledge base", extra={"kb_id": kb_id})

    try:
        service = KnowledgeBaseService(db)
        await service.delete_knowledge_base(kb_id)

        logger.info("API: Deleted knowledge base", extra={"kb_id": kb_id})

        return ShuResponse.no_content()

    except ShuException as e:
        logger.error("API: Failed to delete knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_DELETE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to delete knowledge base", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/summary")
async def get_knowledge_base_summary(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get summary information for a knowledge base.

    Returns a summary including basic information, distinct source_type labels used,
    and document/chunk counts.
    """
    logger.info("API: Get knowledge base summary", extra={"kb_id": kb_id})

    try:
        service = KnowledgeBaseService(db)
        result = await service.get_knowledge_base_summary(kb_id, user_id=str(current_user.id))

        logger.info("API: Retrieved knowledge base summary", extra={"kb_id": kb_id})

        return ShuResponse.success(result)

    except ShuException as e:
        logger.error("API: Failed to get knowledge base summary", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_SUMMARY_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to get knowledge base summary", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("/{kb_id}/status")
async def set_knowledge_base_status(
    kb_id: str,
    new_status: dict[str, Any],
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Set the status of a knowledge base.

    Updates the status of a knowledge base (active, inactive, error).
    """
    logger.info("API: Set knowledge base status", extra={"kb_id": kb_id, "status": new_status})

    try:
        service = KnowledgeBaseService(db)
        result = await service.set_knowledge_base_status(kb_id, new_status)

        logger.info("API: Set knowledge base status", extra={"kb_id": kb_id, "status": result.status})

        return ShuResponse.success(result)

    except ShuException as e:
        logger.error("API: Failed to set knowledge base status", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_STATUS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to set knowledge base status", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/validate")
async def validate_knowledge_base_config(
    kb_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate knowledge base configuration.

    Checks the knowledge base configuration for any issues or warnings.
    """
    logger.info("API: Validate knowledge base config", extra={"kb_id": kb_id})

    try:
        service = KnowledgeBaseService(db)
        result = await service.validate_knowledge_base_config(kb_id)

        logger.info(
            "API: Validated knowledge base config",
            extra={
                "kb_id": kb_id,
                "is_valid": result["is_valid"],
                "error_count": len(result["errors"]),
                "warning_count": len(result["warnings"]),
            },
        )

        return ShuResponse.success(result)

    except ShuException as e:
        logger.error("API: Failed to validate knowledge base config", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="KNOWLEDGE_BASE_VALIDATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to validate knowledge base config", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{kb_id}/rag-config",
    summary="Get RAG configuration",
    description="Get RAG configuration settings for a knowledge base.",
)
async def get_rag_config(
    kb_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Get RAG configuration for a knowledge base.

    Args:
        kb_id: Knowledge base ID
        db: Database session

    Returns:
        JSONResponse with RAG configuration in envelope format

    Raises:
        HTTPException: If knowledge base not found or database error occurs

    """
    logger.info("Getting RAG configuration", extra={"kb_id": kb_id})

    try:
        kb_service = KnowledgeBaseService(db)
        await kb_service.get_knowledge_base(kb_id, str(current_user.id))
        rag_config = await kb_service.get_rag_config(kb_id)

        return ShuResponse.success(rag_config)

    except ShuException as e:
        logger.error("Failed to get RAG configuration", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="RAG_CONFIG_GET_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error getting RAG configuration", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/rag-config/templates",
    summary="Get default RAG templates",
    description="Get default RAG configuration templates for different use cases.",
)
async def get_rag_templates(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get default RAG configuration templates.

    Args:
        db: Database session

    Returns:
        JSONResponse with default templates in envelope format

    Raises:
        HTTPException: If database error occurs

    """
    logger.info("Getting default RAG templates")

    try:
        kb_service = KnowledgeBaseService(db)
        templates = await kb_service.get_default_templates()

        return ShuResponse.success(templates)

    except Exception as e:
        logger.error("Unexpected error getting RAG templates", extra={"error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.put(
    "/{kb_id}/rag-config",
    summary="Update RAG configuration",
    description="Update RAG configuration settings for a knowledge base.",
)
async def update_rag_config(
    rag_config: RAGConfig,
    kb_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Update RAG configuration for a knowledge base.

    Args:
        rag_config: New RAG configuration
        kb_id: Knowledge base ID
        db: Database session

    Returns:
        JSONResponse with updated RAG configuration in envelope format

    Raises:
        HTTPException: If knowledge base not found or database error occurs

    """
    logger.info(
        "Updating RAG configuration",
        extra={"kb_id": kb_id, "prompt_template": rag_config.prompt_template},
    )

    try:
        kb_service = KnowledgeBaseService(db)
        updated_config = await kb_service.update_rag_config(kb_id, rag_config)

        return ShuResponse.success(updated_config)

    except ShuException as e:
        logger.error("Failed to update RAG configuration", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="RAG_CONFIG_UPDATE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error updating RAG configuration", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/documents/{document_id}/preview")
async def get_document_preview(
    kb_id: str,
    document_id: str,
    max_chars: int = Query(1000, description="Maximum characters to preview"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a preview of document content with extraction metadata."""
    try:
        kb_service = KnowledgeBaseService(db)
        document = await kb_service.get_document(kb_id, document_id, user_id=str(current_user.id))

        # Create preview with metadata
        # Normalize content_text for length calculation
        content_text = str(document.content) if document.content is not None else ""
        if max_chars == 0:
            # Return full content when max_chars is 0
            preview_text = content_text
        else:
            # Return truncated content with ellipsis
            preview_text = content_text[:max_chars] + "..." if len(content_text) > max_chars else content_text

        return ShuResponse.success(
            {
                "id": document.id,
                "title": document.title,
                "knowledge_base_id": kb_id,
                "file_type": document.file_type,
                "source_url": document.source_url,
                "source_id": document.source_id,
                "source_type": document.source_type,
                "preview": preview_text,
                "full_content_length": len(content_text),
                "extraction_metadata": {
                    "method": document.extraction_method,
                    "engine": document.extraction_engine,
                    "confidence": document.extraction_confidence,
                    "duration": document.extraction_duration,
                    "metadata": document.extraction_metadata,
                },
                "processing_info": {
                    "status": document.processing_status,
                    "processed_at": document.processed_at.isoformat() if document.processed_at is not None else None,
                    "word_count": document.word_count,
                    "character_count": document.character_count,
                    "chunk_count": document.chunk_count,
                },
            }
        )
    except ShuException as e:
        return ShuResponse.error(message=str(e), code="DOCUMENT_PREVIEW_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error getting document preview: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/documents/{document_id}/extraction-details")
async def get_document_extraction_details(
    kb_id: str,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed extraction information for a document."""
    try:
        kb_service = KnowledgeBaseService(db)
        document = await kb_service.get_document(kb_id, document_id, user_id=str(current_user.id))

        return ShuResponse.success(
            {
                "id": document.id,
                "title": document.title,
                "file_type": document.file_type,
                "file_size": document.file_size,
                "extraction_method": document.extraction_method,
                "extraction_engine": document.extraction_engine,
                "extraction_confidence": document.extraction_confidence,
                "extraction_duration": document.extraction_duration,
                "extraction_metadata": document.extraction_metadata,
                "source_metadata": document.source_metadata,
                "processing_status": document.processing_status,
                "processed_at": document.processed_at.isoformat() if document.processed_at is not None else None,
                "content_stats": {
                    "word_count": document.word_count,
                    "character_count": document.character_count,
                    "chunk_count": document.chunk_count,
                },
            }
        )
    except ShuException as e:
        return ShuResponse.error(message=str(e), code="EXTRACTION_DETAILS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error getting extraction details: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{kb_id}/documents/{document_id}/chunks",
    summary="Get document chunks",
    description="Get all chunks for a document with full content. Supports pagination.",
)
async def get_document_chunks(
    kb_id: str,
    document_id: str,
    limit: int = Query(20, ge=1, le=100, description="Number of chunks to return"),
    offset: int = Query(0, ge=0, description="Number of chunks to skip"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get chunks for a document with full content.

    Returns paginated chunks ordered by chunk_index, including full content,
    summary, and character offsets.

    Args:
        kb_id: Knowledge base ID
        document_id: Document ID
        limit: Maximum number of chunks to return (default 20, max 100)
        offset: Number of chunks to skip for pagination
        current_user: Authenticated user
        db: Database session

    Returns:
        Paginated list of chunks with full content

    """
    try:
        # Verify KB access and document ownership
        kb_service = KnowledgeBaseService(db)
        await kb_service.get_knowledge_base(kb_id, str(current_user.id))

        doc_service = DocumentService(db)
        document = await doc_service.get_document(document_id)
        if str(document.knowledge_base_id) != str(kb_id):
            return ShuResponse.error(
                message="Document does not belong to this knowledge base",
                code="DOCUMENT_KB_MISMATCH",
                status_code=404,
            )

        # Get paginated chunks (server-side limit/offset)
        chunks, total = await doc_service.get_document_chunks_paginated(document_id, limit=limit, offset=offset)

        items = [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "summary": getattr(chunk, "summary", None),
                "char_count": chunk.char_count,
                "word_count": chunk.word_count,
                "token_count": chunk.token_count,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "has_embedding": chunk.has_embedding,
                "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
            }
            for chunk in chunks
        ]

        return ShuResponse.success({"items": items, "total": total, "limit": limit, "offset": offset})

    except ShuException as e:
        return ShuResponse.error(message=str(e), code="DOCUMENT_CHUNKS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error getting document chunks: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    limit: int = Query(50, description="Number of documents to return"),
    offset: int = Query(0, description="Number of documents to skip"),
    search_query: str | None = Query(None, description="Document title to search by"),
    filter_by: str = Query("all", description="Document filter to apply to search"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get list of documents for a knowledge base."""
    try:
        kb_service = KnowledgeBaseService(db)
        documents, total = await kb_service.get_documents(
            kb_id,
            limit=limit,
            offset=offset,
            search_query=search_query,
            filter_by=filter_by,
            user_id=str(current_user.id),
        )

        # Use lightweight serialization to exclude heavy fields (content, embeddings, etc.)
        items = [doc.to_list_dict() for doc in documents]

        return ShuResponse.success({"items": items, "total": total, "limit": limit, "offset": offset})
    except ShuException as e:
        return ShuResponse.error(message=str(e), code="DOCUMENT_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.delete(
    "/{kb_id}/documents/{document_id}",
    summary="Delete a document from knowledge base",
    description="Delete a manually uploaded document. Power user or admin only. Feed-sourced documents cannot be deleted via this endpoint.",
)
async def delete_document(
    kb_id: str,
    document_id: str,
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document from a knowledge base.

    Only manually uploaded documents (source_type='plugin:manual_upload') can be deleted.
    Feed-sourced documents must be managed through their respective feeds.

    Requires power_user or admin role. Access to the specific KB is still enforced via PBAC.
    """
    try:
        kb_service = KnowledgeBaseService(db)
        document = await kb_service.get_document(kb_id, document_id, user_id=str(current_user.id))

        # Only allow deletion of manually uploaded documents
        if document.source_type != "plugin:manual_upload":
            return ShuResponse.error(
                message="Only manually uploaded documents can be deleted. Feed-sourced documents are managed through their feeds.",
                code="DOCUMENT_DELETE_NOT_ALLOWED",
                status_code=403,
            )

        # Capture chunk count before deletion for stats adjustment
        chunk_count = document.chunk_count or 0

        # Delete the document
        doc_service = DocumentService(db)
        await doc_service.delete_document(document_id)

        # Adjust KB stats (decrement by 1 doc and its chunks)
        await kb_service.adjust_document_stats(kb_id, doc_delta=-1, chunk_delta=-chunk_count)

        logger.info(
            "Deleted document",
            extra={"kb_id": kb_id, "document_id": document_id, "deleted_by": current_user.id},
        )

        return ShuResponse.no_content()

    except ShuException as e:
        return ShuResponse.error(message=str(e), code="DOCUMENT_DELETE_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error deleting document: {e}", exc_info=True)
        return ShuResponse.error(message="Failed to delete document", code="DOCUMENT_DELETE_ERROR", status_code=500)


@router.get("/{kb_id}/documents/extraction-summary")
async def get_extraction_summary(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get summary of extraction methods and accuracy across all documents."""
    try:
        kb_service = KnowledgeBaseService(db)
        documents, _ = await kb_service.get_documents(kb_id, user_id=str(current_user.id))

        # Analyze extraction methods
        extraction_stats = {}
        total_documents = len(documents)

        for doc in documents:
            method = doc.extraction_method or "unknown"
            engine = doc.extraction_engine or "unknown"

            if method not in extraction_stats:
                extraction_stats[method] = {
                    "count": 0,
                    "engines": {},
                    "avg_confidence": 0.0,
                    "total_duration": 0.0,
                }

            extraction_stats[method]["count"] += 1

            if engine not in extraction_stats[method]["engines"]:
                extraction_stats[method]["engines"][engine] = 0
            extraction_stats[method]["engines"][engine] += 1

            if doc.extraction_confidence is not None:
                current_avg = extraction_stats[method]["avg_confidence"]
                count = extraction_stats[method]["count"]
                extraction_stats[method]["avg_confidence"] = (
                    current_avg * (count - 1) + doc.extraction_confidence
                ) / count

            if doc.extraction_duration is not None:
                extraction_stats[method]["total_duration"] += doc.extraction_duration

        return ShuResponse.success(
            {
                "knowledge_base_id": kb_id,
                "total_documents": total_documents,
                "extraction_summary": extraction_stats,
            }
        )
    except ShuException as e:
        return ShuResponse.error(message=str(e), code="EXTRACTION_SUMMARY_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(f"Error getting extraction summary: {e}")
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


def _check_content_type_mismatch(ext: str, file_bytes: bytes) -> str | None:
    """Check if file content matches the declared extension via magic bytes.

    Returns an error message string if a mismatch is detected, None if content
    looks valid.  Magic-byte signatures are defined centrally in
    :data:`shu.ingestion.filetypes.MAGIC_BYTES`.
    """
    dotted = f".{ext}"
    expected = MAGIC_BYTES.get(dotted)
    if expected is None:
        return None  # No magic-byte check for this type (text files, etc.)

    if len(file_bytes) < 4:
        return None  # Too short to check signatures

    header = file_bytes[:8]
    if any(header[: len(sig)] == sig for sig in expected):
        return None  # Content matches an expected signature

    return f"File content does not match declared type .{ext}"


@router.post(
    "/{kb_id}/documents/upload",
    summary="Upload documents to knowledge base",
    description=(
        "Upload one or more documents directly to a knowledge base. "
        "Allowed when the caller is a KB owner, has power_user/admin role, "
        "or has a PBAC kb.write grant on the target KB."
    ),
)
async def upload_documents(
    kb_id: str,
    files: list[UploadFile] = File(..., description="Files to upload"),
    current_user: User = Depends(require_kb_write_access),
    db: AsyncSession = Depends(get_db),
):
    """Upload documents directly to a knowledge base.

    Accepts multiple files via multipart/form-data. Each file is validated
    against the application's upload restrictions (allowed types, max size)
    and ingested using the standard document processing pipeline.

    Returns results for each file indicating success or failure.

    Write access is gated by ``require_kb_write_access``: power_user/admin can
    upload to any KB; regular users can upload to KBs they own; PBAC policies
    can grant cross-user write on a per-KB basis.
    """
    try:
        # require_kb_write_access has already verified KB existence and write
        # permission; re-running get_knowledge_base here would redundantly query
        # the DB and incorrectly gate write on the kb.read PBAC check.
        kb_service = KnowledgeBaseService(db)

        # Get upload restrictions from KB-specific settings
        allowed_types = [t.lower() for t in settings.kb_upload_allowed_types]
        max_size = settings.kb_upload_max_size

        results = []

        for file in files:
            filename = file.filename or "upload"
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            # Validate file type
            if ext not in allowed_types:
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": f"Unsupported file type: .{ext}. Allowed: {', '.join('.' + t for t in allowed_types)}",
                    }
                )
                continue

            # Read file bytes
            try:
                file_bytes = await file.read()
            except Exception as e:
                logger.error(f"Failed to read file '{filename}': {e}", exc_info=True)
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": "Failed to read file",
                    }
                )
                continue

            # Validate file is not empty
            if len(file_bytes) == 0:
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": "File is empty (0 bytes)",
                    }
                )
                continue

            # Validate file size
            if len(file_bytes) > max_size:
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": f"File too large: {len(file_bytes)} bytes. Maximum: {max_size} bytes",
                    }
                )
                continue

            # Validate file content matches declared extension (magic bytes check).
            # Catches files renamed to bypass extension validation (e.g. a ZIP named .pdf).
            content_mismatch = _check_content_type_mismatch(ext, file_bytes)
            if content_mismatch:
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": content_mismatch,
                    }
                )
                continue

            # Determine MIME type from filename (extension-based)
            mime_type, _ = mimetypes.guess_type(filename)
            mime_type = mime_type or "application/octet-stream"

            # Generate unique source_id for manual uploads
            source_id = f"manual-upload-{uuid.uuid4().hex[:12]}"

            try:
                result = await ingest_document_service(
                    db,
                    kb_id,
                    plugin_name="manual_upload",
                    user_id=current_user.id,
                    file_bytes=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                    source_id=source_id,
                    source_url=None,
                    attributes={"uploaded_by": current_user.email or current_user.id},
                )

                results.append(
                    {
                        "filename": filename,
                        "success": True,
                        "document_id": result.get("document_id"),
                        "word_count": result.get("word_count", 0),
                        "character_count": result.get("character_count", 0),
                        "chunk_count": result.get("chunk_count", 0),
                        "extraction_method": result.get("extraction", {}).get("method"),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to ingest document '{filename}': {e}", exc_info=True)
                results.append(
                    {
                        "filename": filename,
                        "success": False,
                        "error": "Failed to process file",
                    }
                )

        # Summary
        successful = sum(1 for r in results if r.get("success"))
        failed = len(results) - successful

        # Adjust KB stats once at the end for all successful uploads
        # Note: We only adjust doc_delta here because chunks are created asynchronously
        # by the worker. The chunk count will be updated when:
        # 1. The worker finishes and updates Document.chunk_count
        # 2. A feed sync runs recalculate_kb_stats()
        if successful > 0:
            await kb_service.adjust_document_stats(kb_id, doc_delta=successful, chunk_delta=0)

        return ShuResponse.success(
            {
                "knowledge_base_id": kb_id,
                "total_files": len(files),
                "successful": successful,
                "failed": failed,
                "results": results,
            }
        )
    except ShuException as e:
        logger.error("Failed to upload documents", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="DOCUMENT_UPLOAD_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error("Unexpected error uploading documents", extra={"kb_id": kb_id, "error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post(
    "/{kb_id}/re-embed",
    summary="Trigger re-embedding for a knowledge base",
    description="Enqueue a re-embedding job for a KB whose embeddings are stale.",
)
async def trigger_re_embedding(
    kb_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger re-embedding of all vectors in a knowledge base.

    The KB must have embedding_status='stale'. The job re-embeds all chunks,
    synopses, and queries using the currently configured embedding model.
    """
    from ..core.embedding_service import get_embedding_service

    try:
        embedding_service = await get_embedding_service()
        queue_backend = await get_queue_backend()

        service = KnowledgeBaseService(db)
        result = await service.trigger_re_embedding(
            kb_id,
            embedding_service=embedding_service,
            queue_backend=queue_backend,
        )
        return ShuResponse.success(result)

    except ShuException as e:
        logger.error("API: Failed to trigger re-embedding", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message=e.message, code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to trigger re-embedding", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{kb_id}/re-embed/status",
    summary="Get re-embedding status",
    description="Get the current embedding status and re-embedding progress for a KB.",
)
async def get_re_embedding_status(
    kb_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the embedding status and re-embedding progress for a knowledge base."""
    try:
        kb_service = KnowledgeBaseService(db)
        kb = await kb_service.get_knowledge_base(kb_id, str(current_user.id))

        return ShuResponse.success(
            {
                "knowledge_base_id": kb_id,
                "embedding_status": kb.embedding_status,
                "embedding_model": kb.embedding_model,
                "re_embedding_progress": kb.re_embedding_progress,
            }
        )

    except ShuException as e:
        return ShuResponse.error(message=e.message, code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to get re-embedding status", extra={"kb_id": kb_id, "error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get("/{kb_id}/export")
async def export_knowledge_base(
    kb_id: str,
    no_embeddings: bool = Query(False, description="Omit embedding vectors from the archive"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a knowledge base as a zip archive download."""
    try:
        kb_service = KnowledgeBaseService(db)
        service = KBImportExportService(db, kb_service)
        temp_path, filename = await service.export_kb(kb_id, str(current_user.id), no_embeddings)

        def file_iterator():
            try:
                with open(temp_path, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        return StreamingResponse(
            file_iterator(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ShuException as e:
        return ShuResponse.error(message=e.message, code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to export KB", extra={"kb_id": kb_id, "error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("/import/validate")
async def validate_import_archive(
    file: UploadFile = File(..., description="Zip archive to validate"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate an import archive and return manifest data."""
    try:
        kb_service = KnowledgeBaseService(db)
        service = KBImportExportService(db, kb_service)
        result = await service.validate_import(file)
        return ShuResponse.success(result.model_dump())

    except ShuException as e:
        return ShuResponse.error(message=e.message, code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to validate import archive", extra={"error": str(e)})
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.post("/import")
async def import_knowledge_base(
    file: UploadFile = File(..., description="Zip archive to import"),
    skip_embeddings: bool = Form(False, description="Discard embeddings during import"),
    current_user: User = Depends(require_power_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a knowledge base from a zip archive."""
    try:
        kb_service = KnowledgeBaseService(db)
        service = KBImportExportService(db, kb_service)
        result = await service.start_import(file, skip_embeddings, str(current_user.id))
        return ShuResponse.created(result.model_dump())

    except ShuException as e:
        return ShuResponse.error(message=e.message, code=e.error_code, status_code=e.status_code)
    except Exception as e:
        logger.error("API: Failed to import KB", extra={"error": str(e)}, exc_info=True)
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
