"""Document service for Shu RAG Backend.

This module contains business logic for managing documents,
including CRUD operations, processing, and multi-source support.
"""

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import DocumentNotFoundError
from ..core.logging import get_logger
from ..models.document import Document, DocumentChunk
from ..schemas.document import (
    DocumentChunkResponse,
    DocumentCreate,
    DocumentDetailResponse,
    DocumentList,
    DocumentResponse,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentStats,
    DocumentUpdate,
    ProcessingStatus,
)

logger = get_logger(__name__)


class DocumentService:
    """Service class for document operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(self, doc_data: DocumentCreate) -> DocumentResponse:
        """Create a new document."""
        logger.debug(
            "Creating document",
            extra={
                "title": doc_data.title,
                "kb_id": doc_data.knowledge_base_id,
                "source_type": doc_data.source_type,
            },
        )

        # Validate knowledge base exists
        from ..utils import KnowledgeBaseVerifier

        kb = await KnowledgeBaseVerifier.verify_exists(self.db, doc_data.knowledge_base_id)

        # Legacy SourceType validation removed; source_type is now a free-form string tied to plugin or system-defined source families.

        # Check if document already exists
        existing_result = await self.db.execute(
            select(Document).where(
                and_(
                    Document.knowledge_base_id == doc_data.knowledge_base_id,
                    Document.source_type == doc_data.source_type,
                    Document.source_id == doc_data.source_id,
                )
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Return existing document instead of failing - makes operation idempotent
            logger.warning(
                "Document already exists, returning existing",
                extra={
                    "doc_id": existing.id,
                    "source_id": doc_data.source_id,
                    "kb_id": doc_data.knowledge_base_id,
                    "source_type": doc_data.source_type,
                },
            )
            return DocumentResponse.from_orm(existing)

        # Create document
        document = Document(**doc_data.dict())
        self.db.add(document)
        await self.db.commit()
        await self.db.refresh(document)

        logger.debug(
            "Created document",
            extra={
                "doc_id": document.id,
                "title": document.title,
                "kb_id": document.knowledge_base_id,
                "source_type": document.source_type,
            },
        )

        return DocumentResponse.from_orm(document)

    async def get_document(self, doc_id: str) -> DocumentDetailResponse:
        """Get a specific document by ID."""
        logger.debug("Getting document", extra={"doc_id": doc_id})

        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        return DocumentDetailResponse.from_orm(document)

    async def update_document(self, doc_id: str, update_data: DocumentUpdate) -> DocumentResponse:
        """Update an existing document."""
        logger.debug("Updating document", extra={"doc_id": doc_id})

        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        # Update fields
        update_dict = update_data.dict(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(document, field, value)

        await self.db.commit()
        await self.db.refresh(document)

        logger.debug("Updated document", extra={"doc_id": doc_id})

        return DocumentResponse.from_orm(document)

    async def delete_document(self, doc_id: str) -> None:
        """Delete a document and all its chunks."""
        logger.debug("Deleting document", extra={"doc_id": doc_id})

        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        # Delete document (cascading will handle chunks)
        await self.db.delete(document)
        await self.db.commit()

        logger.debug("Deleted document", extra={"doc_id": doc_id})

    async def list_documents(
        self,
        knowledge_base_id: str | None = None,
        source_type: str | None = None,
        processing_status: ProcessingStatus | None = None,
        page: int = 1,
        size: int = 10,
    ) -> DocumentList:
        """List documents with filtering and pagination."""
        logger.debug(
            "Listing documents",
            extra={
                "kb_id": knowledge_base_id,
                "source_type": source_type,
                "processing_status": processing_status.value if processing_status else None,
                "page": page,
                "size": size,
            },
        )

        query = select(Document)

        # Apply filters
        if knowledge_base_id:
            query = query.where(Document.knowledge_base_id == knowledge_base_id)

        if source_type:
            query = query.where(Document.source_type == source_type)

        if processing_status:
            query = query.where(Document.processing_status == processing_status.value)

        # Get total count
        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()

        # Apply pagination
        offset = (page - 1) * size
        query = query.offset(offset).limit(size)
        result = await self.db.execute(query)
        documents = result.scalars().all()

        items = [DocumentResponse.from_orm(doc) for doc in documents]
        pages = (total + size - 1) // size

        result_list = DocumentList(items=items, total=total, page=page, size=size, pages=pages)

        logger.debug(
            "Listed documents",
            extra={
                "total": result_list.total,
                "page": result_list.page,
                "returned_items": len(result_list.items),
            },
        )

        return result_list

    async def get_documents_by_source_type(self, source_type: str) -> list[DocumentResponse]:
        """Get all documents of a specific source type."""
        logger.debug("Getting documents by source type", extra={"source_type": source_type})

        result = await self.db.execute(select(Document).where(Document.source_type == source_type))
        documents = result.scalars().all()

        result_list = [DocumentResponse.from_orm(doc) for doc in documents]

        logger.debug(
            "Retrieved documents by source type",
            extra={"source_type": source_type, "count": len(result_list)},
        )

        return result_list

    async def get_document_by_source_id(self, knowledge_base_id: str, source_id: str) -> Document | None:
        """Get a document by its source ID within a knowledge base."""
        logger.debug(
            "Getting document by source ID",
            extra={"kb_id": knowledge_base_id, "source_id": source_id},
        )

        result = await self.db.execute(
            select(Document).where(
                and_(Document.knowledge_base_id == knowledge_base_id, Document.source_id == source_id)
            )
        )
        document = result.scalar_one_or_none()

        if document:
            logger.debug(
                "Found document by source ID",
                extra={"doc_id": document.id, "title": document.title},
            )
        else:
            logger.debug(
                "No document found for source ID",
                extra={"kb_id": knowledge_base_id, "source_id": source_id},
            )

        return document

    async def get_document_stats(self, knowledge_base_id: str | None = None) -> DocumentStats:
        """Get document statistics."""
        logger.debug("Getting document statistics", extra={"kb_id": knowledge_base_id})

        query = select(Document)
        if knowledge_base_id:
            query = query.where(Document.knowledge_base_id == knowledge_base_id)

        # Basic counts
        total_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total_documents = total_result.scalar()

        processed_query = query.where(Document.processing_status == "processed")
        processed_result = await self.db.execute(select(func.count()).select_from(processed_query.subquery()))
        processed_documents = processed_result.scalar()

        pending_query = query.where(Document.processing_status == "pending")
        pending_result = await self.db.execute(select(func.count()).select_from(pending_query.subquery()))
        pending_documents = pending_result.scalar()

        error_query = query.where(Document.processing_status == "error")
        error_result = await self.db.execute(select(func.count()).select_from(error_query.subquery()))
        error_documents = error_result.scalar()

        # Aggregated stats
        total_chunks_result = await self.db.execute(
            select(func.sum(Document.chunk_count)).select_from(query.subquery()).where(Document.chunk_count.isnot(None))
        )
        total_chunks = total_chunks_result.scalar() or 0

        total_words_result = await self.db.execute(
            select(func.sum(Document.word_count)).select_from(query.subquery()).where(Document.word_count.isnot(None))
        )
        total_words = total_words_result.scalar() or 0

        total_characters_result = await self.db.execute(
            select(func.sum(Document.character_count))
            .select_from(query.subquery())
            .where(Document.character_count.isnot(None))
        )
        total_characters = total_characters_result.scalar() or 0

        # File type breakdown
        file_type_query = query.with_entities(Document.file_type, func.count(Document.id).label("count")).group_by(
            Document.file_type
        )
        file_type_result = await self.db.execute(file_type_query)
        file_type_results = file_type_result.all()

        file_type_breakdown = {result.file_type: result.count for result in file_type_results}

        # Source type breakdown
        source_type_query = query.with_entities(Document.source_type, func.count(Document.id).label("count")).group_by(
            Document.source_type
        )
        source_type_result = await self.db.execute(source_type_query)
        source_type_results = source_type_result.all()

        source_type_breakdown = {result.source_type: result.count for result in source_type_results}

        # Processing status breakdown
        status_query = query.with_entities(Document.processing_status, func.count(Document.id).label("count")).group_by(
            Document.processing_status
        )
        status_result = await self.db.execute(status_query)
        status_results = status_result.all()

        processing_status_breakdown = {result.processing_status: result.count for result in status_results}

        # Calculate averages
        avg_chunks_per_document = total_chunks / total_documents if total_documents > 0 else 0
        avg_words_per_document = total_words / total_documents if total_documents > 0 else 0

        result_stats = DocumentStats(
            total_documents=total_documents,
            processed_documents=processed_documents,
            pending_documents=pending_documents,
            error_documents=error_documents,
            total_chunks=total_chunks,
            total_words=total_words,
            total_characters=total_characters,
            file_type_breakdown=file_type_breakdown,
            source_type_breakdown=source_type_breakdown,
            processing_status_breakdown=processing_status_breakdown,
            average_chunks_per_document=avg_chunks_per_document,
            average_words_per_document=avg_words_per_document,
        )

        logger.debug(
            "Retrieved document statistics",
            extra={
                "total_documents": result_stats.total_documents,
                "processed_documents": result_stats.processed_documents,
                "pending_documents": result_stats.pending_documents,
                "error_documents": result_stats.error_documents,
            },
        )

        return result_stats

    async def process_and_update_chunks(
        self,
        knowledge_base_id: str,
        document: Document,
        title: str,
        content: str,
    ) -> tuple[int, int, int]:
        """Generate chunks for a document and update processing stats."""
        from .knowledge_base_service import KnowledgeBaseService
        from .rag_processing_service import RAGProcessingService

        kb_service = KnowledgeBaseService(self.db)
        kb = await kb_service.get_knowledge_base(knowledge_base_id)
        if not kb:
            raise ValueError(f"Knowledge base {knowledge_base_id} not found")

        rag = RAGProcessingService.get_instance()
        chunks = await rag.process_document(
            document_id=document.id,
            knowledge_base=kb,
            text=content,
            document_title=title,
        )

        await self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        for chunk in chunks:
            self.db.add(chunk)

        word_count = len(content.split()) if content else 0
        character_count = len(content)
        chunk_count = len(chunks)

        await self.mark_document_processed(
            document.id,
            word_count=word_count,
            character_count=character_count,
            chunk_count=chunk_count,
        )

        return word_count, character_count, chunk_count

    async def mark_document_processed(
        self, doc_id: str, word_count: int, character_count: int, chunk_count: int
    ) -> None:
        """Mark a document as processed with stats."""
        logger.debug(
            "Marking document as processed",
            extra={
                "doc_id": doc_id,
                "word_count": word_count,
                "character_count": character_count,
                "chunk_count": chunk_count,
            },
        )

        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        document.mark_processed()
        document.update_content_stats(word_count, character_count, chunk_count)

        await self.db.commit()

        logger.debug("Marked document as processed", extra={"doc_id": doc_id})

    async def mark_document_error(self, doc_id: str, error_message: str) -> None:
        """Mark a document as having an error."""
        logger.debug("Marking document as error", extra={"doc_id": doc_id, "error_message": error_message})

        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        document.mark_error(error_message)

        await self.db.commit()

        logger.debug("Marked document as error", extra={"doc_id": doc_id})

    async def get_document_chunks(self, doc_id: str) -> list[DocumentChunkResponse]:
        """Get all chunks for a document."""
        logger.debug("Getting document chunks", extra={"doc_id": doc_id})

        doc_result = await self.db.execute(select(Document).where(Document.id == doc_id))
        document = doc_result.scalar_one_or_none()
        if not document:
            raise DocumentNotFoundError(doc_id)

        chunks_result = await self.db.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == doc_id).order_by(DocumentChunk.chunk_index)
        )
        chunks = chunks_result.scalars().all()

        result = [DocumentChunkResponse.from_orm(chunk) for chunk in chunks]

        logger.debug("Retrieved document chunks", extra={"doc_id": doc_id, "chunk_count": len(result)})

        return result

    async def search_documents(self, search_request: DocumentSearchRequest) -> DocumentSearchResponse:
        """Search documents by query."""
        logger.debug(
            "Searching documents",
            extra={
                "query": search_request.query,
                "kb_id": search_request.knowledge_base_id,
                "file_types": search_request.file_types,
                "source_types": search_request.source_types,
            },
        )

        import time

        start_time = time.time()

        query = select(Document)

        # Apply filters
        if search_request.knowledge_base_id:
            query = query.where(Document.knowledge_base_id == search_request.knowledge_base_id)

        if search_request.file_types:
            query = query.where(Document.file_type.in_(search_request.file_types))

        if search_request.source_types:
            query = query.where(Document.source_type.in_(search_request.source_types))

        if search_request.processing_status:
            query = query.where(Document.processing_status == search_request.processing_status.value)

        if search_request.created_after:
            query = query.where(Document.created_at >= search_request.created_after)

        if search_request.created_before:
            query = query.where(Document.created_at <= search_request.created_before)

        # Text search with word boundary matching for titles
        if search_request.query:
            # Use regex word boundary for title search to avoid substring matches
            # Only apply word boundary to meaningful terms (3+ chars, not stop words)
            query_term = search_request.query.strip()
            if len(query_term) >= 3 and query_term.lower() not in {
                "the",
                "and",
                "for",
                "are",
                "but",
                "not",
                "you",
                "all",
                "can",
                "had",
                "her",
                "was",
                "one",
                "our",
                "out",
                "day",
                "get",
                "has",
                "him",
                "his",
                "how",
                "its",
                "may",
                "new",
                "now",
                "old",
                "see",
                "two",
                "who",
                "boy",
                "did",
                "she",
                "use",
                "way",
                "what",
                "when",
                "with",
                "have",
                "this",
                "will",
                "your",
                "from",
                "they",
                "know",
                "want",
                "been",
                "good",
                "much",
                "some",
                "time",
                "very",
                "come",
                "here",
                "just",
                "like",
                "long",
                "make",
                "many",
                "over",
                "such",
                "take",
                "than",
                "them",
                "well",
                "were",
            }:
                import re

                escaped_term = re.escape(query_term)
                query = query.where(
                    Document.title.op("~*")(f"\\m{escaped_term}\\M")
                    | Document.content.ilike(f"%{search_request.query}%")
                )
            else:
                # For short terms or stop words, use content search only
                query = query.where(Document.content.ilike(f"%{search_request.query}%"))

        # Get total count
        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar()

        # Apply limit
        query = query.limit(search_request.limit)
        result = await self.db.execute(query)
        documents = result.scalars().all()

        items = [DocumentResponse.from_orm(doc) for doc in documents]
        execution_time = time.time() - start_time

        result_search = DocumentSearchResponse(
            items=items, total=total, query=search_request.query, execution_time=execution_time
        )

        logger.debug(
            "Searched documents",
            extra={
                "query": search_request.query,
                "total": result_search.total,
                "returned_items": len(result_search.items),
                "execution_time": result_search.execution_time,
            },
        )

        return result_search

    async def get_documents_by_knowledge_base(self, knowledge_base_id: str) -> list[DocumentResponse]:
        """Get all documents for a knowledge base."""
        logger.debug("Getting documents by knowledge base", extra={"kb_id": knowledge_base_id})

        result = await self.db.execute(select(Document).where(Document.knowledge_base_id == knowledge_base_id))
        documents = result.scalars().all()

        result_list = [DocumentResponse.from_orm(doc) for doc in documents]

        logger.debug(
            "Retrieved documents by knowledge base",
            extra={"kb_id": knowledge_base_id, "count": len(result_list)},
        )

        return result_list

    async def get_pending_documents(self, knowledge_base_id: str | None = None) -> list[DocumentResponse]:
        """Get all pending documents."""
        logger.debug("Getting pending documents", extra={"kb_id": knowledge_base_id})

        query = select(Document).where(Document.processing_status == "pending")

        if knowledge_base_id:
            query = query.where(Document.knowledge_base_id == knowledge_base_id)

        result = await self.db.execute(query)
        documents = result.scalars().all()
        result_list = [DocumentResponse.from_orm(doc) for doc in documents]

        logger.debug(
            "Retrieved pending documents",
            extra={"kb_id": knowledge_base_id, "count": len(result_list)},
        )

        return result_list
