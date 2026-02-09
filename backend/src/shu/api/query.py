"""Query API endpoints for Shu RAG Backend.

This module provides REST endpoints for document querying operations
including vector similarity search, document listing, and retrieval.
"""

from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..auth.rbac import require_kb_query_access
from ..core.config import ConfigurationManager, get_config_manager_dependency, get_settings_instance
from ..core.exceptions import ShuException
from ..core.logging import get_logger
from ..core.response import ShuResponse
from ..schemas.query import QueryRequest
from ..services.query_service import QueryService
from ..services.rag_query_processing import execute_rag_queries
from .dependencies import get_db

logger = get_logger(__name__)
router = APIRouter(prefix="/query", tags=["query"])

settings = get_settings_instance()


@router.post(
    "/{knowledge_base_id}/search",
    summary="Query documents",
    description="Query documents using vector similarity, keyword, or hybrid search. Supports both QueryRequest and SimilaritySearchRequest for backward compatibility.",
)
# RBAC: require_kb_query_access expects path param 'knowledge_base_id'
async def query_documents(
    knowledge_base_id: str = Path(..., description="Knowledge base ID"),
    request: QueryRequest = ...,
    current_user: User = Depends(require_kb_query_access("knowledge_base_id")),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Query documents in a knowledge base.

    Supports multiple search types:
    - Vector similarity search (query_type="similarity")
    - Keyword search (query_type="keyword")
    - Hybrid search (query_type="hybrid")

    This endpoint consolidates all search functionality and supersedes the legacy
    /similarity route (now removed). For backward compatibility, it still accepts
    payloads shaped like SimilaritySearchRequest.

    Args:
        knowledge_base_id: ID of the knowledge base to search
        request: Query request with search parameters
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing search results

    Raises:
        HTTPException: If knowledge base not found or search fails

    """
    logger.info(
        "Processing document query",
        extra={
            "kb_id": knowledge_base_id,
            "query": request.query,
            "query_type": request.query_type,
            "limit": request.limit,
        },
    )

    original_query = request.query

    try:
        query_service = QueryService(db, config_manager)

        def build_request(_: str, __: dict[str, Any], query_text: str) -> QueryRequest:
            return request.model_copy(update={"query": query_text})

        rewritten_query, rewrite_diagnostics, query_results = await execute_rag_queries(
            db_session=db,
            config_manager=config_manager,
            query_service=query_service,
            current_user=current_user,
            query_text=original_query,
            knowledge_base_ids=[knowledge_base_id],
            request_builder=build_request,
            prior_messages=None,
            rag_rewrite_mode=request.rag_rewrite_mode,
        )

        if query_results:
            response = query_results[0].get("response") or {}
        else:
            response = {
                "results": [],
                "total_results": 0,
                "query": rewritten_query,
                "query_type": request.query_type,
                "execution_time": 0.0,
                "similarity_threshold": request.similarity_threshold or request.threshold or 0.0,
                "rag_config": None,
                "escalation": {"enabled": False},
            }

        if hasattr(response, "model_dump"):
            response = response.model_dump()

        if isinstance(response, dict):
            if rewrite_diagnostics:
                response.setdefault("rag_query", rewrite_diagnostics)
                response.setdefault("query", rewrite_diagnostics.get("rewritten") or original_query)
            else:
                response.setdefault("query", rewritten_query)

        return ShuResponse.success(response)

    except ShuException as e:
        logger.error(
            "Failed to perform document query",
            extra={"kb_id": knowledge_base_id, "query": original_query, "error": str(e)},
        )
        return ShuResponse.error(message=str(e), code="QUERY_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(
            "Unexpected error in document query",
            extra={"kb_id": knowledge_base_id, "query": original_query, "error": str(e)},
        )
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{knowledge_base_id}/documents",
    summary="List documents",
    description="List documents in a knowledge base with optional filtering.",
)
# RBAC: require_kb_query_access expects path param 'knowledge_base_id'
async def list_documents(
    knowledge_base_id: str = Path(..., description="Knowledge base ID"),
    limit: int = Query(50, ge=1, le=100, description="Number of documents to return"),
    offset: int = Query(0, ge=0, description="Number of documents to skip"),
    source_type: str | None = Query(None, description="Filter by source type"),
    file_type: str | None = Query(None, description="Filter by file type"),
    current_user: User = Depends(require_kb_query_access("knowledge_base_id")),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """List documents in a knowledge base with optional filtering.

    Supports pagination and filtering by source type and file type.
    Returns documents ordered by creation date (newest first).

    Args:
        knowledge_base_id: ID of the knowledge base
        limit: Maximum number of documents to return
        offset: Number of documents to skip for pagination
        source_type: Optional filter by source type
        file_type: Optional filter by file type
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing document list

    Raises:
        HTTPException: If knowledge base not found

    """
    logger.info(
        "Listing documents",
        extra={
            "kb_id": knowledge_base_id,
            "limit": limit,
            "offset": offset,
            "source_type": source_type,
            "file_type": file_type,
        },
    )

    try:
        query_service = QueryService(db, config_manager)
        result = await query_service.list_documents(
            knowledge_base_id=knowledge_base_id,
            limit=limit,
            offset=offset,
            source_type=source_type,
            file_type=file_type,
        )

        # Extract data from the new dictionary format
        documents = result["documents"]
        total_count = result["total_count"]

        # Convert SQLAlchemy Document objects to Pydantic models
        from ..schemas.document import DocumentResponse

        document_responses = [DocumentResponse.from_orm(doc) for doc in documents]

        # Format response
        response_data = {
            "items": document_responses,
            "total": total_count,
            "page": (offset // limit) + 1,
            "size": limit,
            "pages": (total_count + limit - 1) // limit,
        }

        return ShuResponse.success(response_data)

    except ShuException as e:
        logger.error("Failed to list documents", extra={"kb_id": knowledge_base_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="DOCUMENT_LIST_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(
            "Unexpected error listing documents",
            extra={"kb_id": knowledge_base_id, "error": str(e)},
        )
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{knowledge_base_id}/documents/{document_id}",
    summary="Get document details",
    description="Get detailed information about a specific document.",
)
# RBAC: require_kb_query_access expects path param 'knowledge_base_id'
async def get_document_details(
    knowledge_base_id: str = Path(..., description="Knowledge base ID"),
    document_id: str = Path(..., description="Document ID"),
    include_chunks: bool = Query(False, description="Include document chunks"),
    current_user: User = Depends(require_kb_query_access("knowledge_base_id")),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Get detailed information about a specific document.

    Returns document metadata and optionally includes document chunks.
    Useful for debugging and detailed document analysis.

    Args:
        knowledge_base_id: ID of the knowledge base
        document_id: ID of the document to retrieve
        include_chunks: Whether to include document chunks in response
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing document details

    Raises:
        HTTPException: If knowledge base or document not found

    """
    logger.info(
        "Getting document details",
        extra={
            "kb_id": knowledge_base_id,
            "document_id": document_id,
            "include_chunks": include_chunks,
        },
    )

    try:
        query_service = QueryService(db, config_manager)
        document = await query_service.get_document_details(
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            include_chunks=include_chunks,
        )

        if not document:
            return ShuResponse.error(
                message=f"Document '{document_id}' not found in knowledge base '{knowledge_base_id}'",
                code="DOCUMENT_NOT_FOUND",
                status_code=404,
            )

        # Convert SQLAlchemy Document object to Pydantic model
        from ..schemas.document import DocumentResponse

        document_response = DocumentResponse.from_orm(document)
        return ShuResponse.success(document_response)

    except ShuException as e:
        logger.error(
            "Failed to get document details",
            extra={"kb_id": knowledge_base_id, "document_id": document_id, "error": str(e)},
        )
        return ShuResponse.error(message=str(e), code="DOCUMENT_DETAILS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(
            "Unexpected error getting document details",
            extra={"kb_id": knowledge_base_id, "document_id": document_id, "error": str(e)},
        )
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)


@router.get(
    "/{knowledge_base_id}/stats",
    summary="Get query statistics",
    description="Get query statistics for a knowledge base.",
)
# RBAC: require_kb_query_access expects path param 'knowledge_base_id'
async def get_query_stats(
    knowledge_base_id: str = Path(..., description="Knowledge base ID"),
    current_user: User = Depends(require_kb_query_access("knowledge_base_id")),
    db: AsyncSession = Depends(get_db),
    config_manager: ConfigurationManager = Depends(get_config_manager_dependency),
):
    """Get query statistics for a knowledge base.

    Returns comprehensive statistics about the knowledge base including
    document counts, chunk counts, and processing metrics.

    Args:
        knowledge_base_id: ID of the knowledge base
        db: Database session

    Returns:
        JSONResponse with single-envelope structure containing query statistics

    Raises:
        HTTPException: If knowledge base not found

    """
    logger.info("Getting query statistics", extra={"kb_id": knowledge_base_id})

    try:
        query_service = QueryService(db, config_manager)
        stats = await query_service.get_query_stats(knowledge_base_id)

        return ShuResponse.success(stats)

    except ShuException as e:
        logger.error("Failed to get query statistics", extra={"kb_id": knowledge_base_id, "error": str(e)})
        return ShuResponse.error(message=str(e), code="QUERY_STATS_ERROR", status_code=e.status_code)
    except Exception as e:
        logger.error(
            "Unexpected error getting query statistics",
            extra={"kb_id": knowledge_base_id, "error": str(e)},
        )
        return ShuResponse.error(message="Internal server error", code="INTERNAL_SERVER_ERROR", status_code=500)
