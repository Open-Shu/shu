from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from ..core.config import get_settings_instance
from ..core.logging import get_logger
from ..models.document import DocumentChunk
from ..models.knowledge_base import KnowledgeBase

if TYPE_CHECKING:
    from ..core.embedding_service import EmbeddingService

logger = get_logger(__name__)


class RAGProcessingService:
    """Handles text chunking and embedding generation for documents.

    Embedding generation is delegated to an EmbeddingService instance
    provided at construction time. This class owns chunking logic and
    the orchestration of chunk creation with embeddings.
    """

    def __init__(self, embedding_service: "EmbeddingService") -> None:
        self.embedding_service = embedding_service

    def chunk_text(self, text: str, chunk_size: int | None = None, chunk_overlap: int | None = None) -> list[str]:
        """Split text into overlapping chunks."""
        if not text:
            return []

        settings = get_settings_instance()
        default_chunk_size = chunk_size or settings.default_chunk_size
        default_chunk_overlap = chunk_overlap or settings.default_chunk_overlap

        chunks = []
        start = 0
        text_length = len(text)
        while start < text_length:
            end = min(start + default_chunk_size, text_length)
            chunk = text[start:end]
            chunks.append(chunk)
            if end == text_length:
                break
            start += default_chunk_size - default_chunk_overlap
        return chunks

    async def process_document(
        self,
        document_id: str,
        knowledge_base: KnowledgeBase,
        text: str,
        document_title: str | None = None,
        config_manager: Optional["ConfigurationManager"] = None,  # noqa: F821
        *,
        user_id: str | None = None,
    ) -> list[DocumentChunk]:
        """Chunk the document text and generate embeddings for each chunk.
        Returns a list of DocumentChunk objects (not yet added to DB).
        """
        from ..core.config import get_config_manager

        settings = get_settings_instance()
        chunk_size = int(knowledge_base.chunk_size or settings.default_chunk_size)
        chunk_overlap = int(knowledge_base.chunk_overlap or settings.default_chunk_overlap)

        # Get title configuration
        configuration_manager = config_manager if config_manager is not None else get_config_manager()

        kb_config = knowledge_base.get_rag_config()
        title_chunk_enabled = configuration_manager.get_title_chunk_enabled(kb_config=kb_config)
        title_weighting_enabled = configuration_manager.get_title_weighting_enabled(kb_config=kb_config)

        # 1. Chunk the text
        chunks = self.chunk_text(text, chunk_size, chunk_overlap)

        # 2. Handle document title based on configuration
        if document_title and chunks:
            if title_chunk_enabled:
                title_chunk = f"Document Title: {document_title}"
                chunks.insert(0, title_chunk)
            else:
                title_prefix = f"Document Title: {document_title}\n\n"
                chunks[0] = title_prefix + chunks[0]

        # 3. Generate embeddings via the EmbeddingService
        embeddings = await self.embedding_service.embed_texts(chunks, user_id=user_id)

        if len(embeddings) != len(chunks):
            raise ValueError(f"Embedding count mismatch: got {len(embeddings)} embeddings for {len(chunks)} chunks")

        # 4. Create DocumentChunk objects
        document_chunks = []
        start_char = 0
        title_chunk_offset = 1 if (document_title and title_chunk_enabled) else 0

        # When the title is inlined (title_chunk_enabled=False), the prefix
        # inflates chunks[0] but start_char/end_char must map to the original
        # source text. Track the prefix length so we can subtract it.
        title_prefix_len = 0
        if document_title and chunks and not title_chunk_enabled:
            title_prefix_len = len(f"Document Title: {document_title}\n\n")

        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=False)):
            is_title_chunk = idx == 0 and title_chunk_offset == 1

            chunk_metadata = {}
            if is_title_chunk:
                chunk_metadata = {
                    "chunk_type": "title",
                    "title_weighting_enabled": title_weighting_enabled,
                    "original_title": document_title,
                }
            else:
                chunk_metadata = {
                    "chunk_type": "content",
                    "title_weighting_enabled": title_weighting_enabled,
                }

            # For the title chunk (separate), positions are synthetic (not in source text).
            # For content chunks, positions map to the original source text.
            if is_title_chunk:
                chunk_start = 0
                chunk_end = len(chunk)
            else:
                chunk_start = start_char
                # For the first content chunk with inlined title, the source-text
                # length is the chunk length minus the prepended title prefix.
                source_len = len(chunk) - (title_prefix_len if idx == 0 else 0)
                chunk_end = start_char + source_len

            doc_chunk = DocumentChunk(
                document_id=document_id,
                knowledge_base_id=knowledge_base.id,
                chunk_index=idx,
                content=chunk,
                char_count=len(chunk),
                word_count=len(chunk.split()),
                start_char=chunk_start,
                end_char=chunk_end,
                embedding=embedding,
                embedding_model=self.embedding_service.model_name,
                embedding_created_at=datetime.now(UTC),
                chunk_metadata=chunk_metadata,
            )
            document_chunks.append(doc_chunk)

            if is_title_chunk:
                start_char = 0
            else:
                # Advance by the source-text portion of this chunk
                source_len = len(chunk) - (title_prefix_len if idx == 0 else 0)
                start_char += source_len - chunk_overlap

        return document_chunks
