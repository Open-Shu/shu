import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Optional

import sentence_transformers

from ..core.config import get_settings_instance
from ..core.logging import get_logger
from ..models.document import DocumentChunk
from ..models.knowledge_base import KnowledgeBase

logger = get_logger(__name__)


class RAGServiceManager:
    """Memory-aware manager for RAGProcessingService instances.
    Replaces the problematic singleton pattern with proper lifecycle management.
    """

    def __init__(self) -> None:
        self._instances: dict[str, dict] = {}  # key -> {instance, last_used, created_at}
        self._executor: ThreadPoolExecutor | None = None
        self._cache_ttl = 3600  # 1 hour TTL for unused instances
        self._max_instances = 5  # Limit concurrent model instances

    def get_service(self, embedding_model: str | None = None, device: str | None = None) -> "RAGProcessingService":
        """Get or create a RAGProcessingService instance with memory management."""
        settings = get_settings_instance()
        model_name = embedding_model or settings.default_embedding_model
        device_name = device or settings.embedding_device

        instance_key = f"{model_name}:{device_name}"
        current_time = time.time()

        # Clean up expired instances first
        self._cleanup_expired_instances(current_time)

        # Check if we have a cached instance
        if instance_key in self._instances:
            entry = self._instances[instance_key]
            entry["last_used"] = current_time
            logger.debug(f"Reusing RAGProcessingService instance for {instance_key}")
            return entry["instance"]

        # Check instance limit
        if len(self._instances) >= self._max_instances:
            self._evict_oldest_instance()

        # Create new instance
        logger.info(f"Creating new RAGProcessingService instance for {instance_key}")
        instance = RAGProcessingService(model_name, device_name, self._get_executor())

        self._instances[instance_key] = {
            "instance": instance,
            "last_used": current_time,
            "created_at": current_time,
        }

        return instance

    def _get_executor(self) -> ThreadPoolExecutor:
        """Get or create the shared thread pool executor."""
        if self._executor is None or self._executor._shutdown:
            settings = get_settings_instance()
            embedding_threads = max(1, int(getattr(settings, "embedding_threads", 4)))
            self._executor = ThreadPoolExecutor(
                max_workers=embedding_threads, thread_name_prefix="rag-embedding-worker"
            )
            logger.debug(f"Created new ThreadPoolExecutor for RAG processing (threads={embedding_threads})")
        return self._executor

    def _cleanup_expired_instances(self, current_time: float) -> None:
        """Remove instances that haven't been used recently."""
        expired_keys = []

        for key, entry in self._instances.items():
            if current_time - entry["last_used"] > self._cache_ttl:
                expired_keys.append(key)

        for key in expired_keys:
            entry = self._instances.pop(key)
            logger.info(f"Evicting expired RAGProcessingService instance: {key}")
            # Clean up the instance
            self._cleanup_instance(entry["instance"])

    def _evict_oldest_instance(self) -> None:
        """Evict the least recently used instance to make room."""
        if not self._instances:
            return

        oldest_key = min(self._instances.keys(), key=lambda k: self._instances[k]["last_used"])

        entry = self._instances.pop(oldest_key)
        logger.info(f"Evicting oldest RAGProcessingService instance: {oldest_key}")
        self._cleanup_instance(entry["instance"])

    def _cleanup_instance(self, instance: "RAGProcessingService") -> None:
        """Clean up resources associated with an instance."""
        try:
            # Clear model reference to help with garbage collection
            if hasattr(instance, "model"):
                del instance.model
            logger.debug("Cleaned up RAGProcessingService instance resources")
        except Exception as e:
            logger.warning(f"Error cleaning up RAGProcessingService instance: {e}")

    def clear_all(self) -> None:
        """Clear all cached instances and shutdown executor."""
        logger.info("Clearing all RAGProcessingService instances")

        # Clean up all instances
        for entry in self._instances.values():
            self._cleanup_instance(entry["instance"])

        self._instances.clear()

        # Shutdown executor
        if self._executor and not self._executor._shutdown:
            self._executor.shutdown(wait=False)
            self._executor = None
            logger.debug("Shutdown ThreadPoolExecutor")

    def get_stats(self) -> dict[str, any]:
        """Get statistics about cached instances."""
        current_time = time.time()
        return {
            "active_instances": len(self._instances),
            "max_instances": self._max_instances,
            "cache_ttl": self._cache_ttl,
            "instances": {
                key: {
                    "age_seconds": current_time - entry["created_at"],
                    "last_used_seconds_ago": current_time - entry["last_used"],
                }
                for key, entry in self._instances.items()
            },
        }


# Global service manager instance
_service_manager = RAGServiceManager()


class RAGProcessingService:
    """Handles text chunking and embedding generation for documents.
    No longer uses singleton pattern - managed by RAGServiceManager.
    """

    def __init__(self, embedding_model: str, device: str, executor: ThreadPoolExecutor) -> None:
        """Initialize RAGProcessingService with specified model and device."""
        settings = get_settings_instance()

        self.embedding_model_name = embedding_model
        self.device = device
        self.batch_size = settings.embedding_batch_size
        self.executor = executor

        logger.info(f"Loading SentenceTransformer model: {self.embedding_model_name}")

        # Set cache directory to ensure models are cached locally
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        os.makedirs(cache_dir, exist_ok=True)

        # Configure Hugging Face to use local cache and reduce API calls
        os.environ.setdefault("HF_HUB_CACHE", cache_dir)
        os.environ.setdefault("HF_HUB_OFFLINE", "0")  # Allow online but prefer cache
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")  # Disable telemetry

        # Load model with local caching
        self.model = sentence_transformers.SentenceTransformer(
            self.embedding_model_name, device=self.device, cache_folder=cache_dir
        )
        logger.info(f"Successfully loaded SentenceTransformer model: {self.embedding_model_name}")

        # Preload the model to avoid lazy loading during first use
        logger.debug("Preloading model with dummy text to ensure full initialization")
        dummy_embedding = self.model.encode(["test"], batch_size=1, show_progress_bar=False)
        logger.debug(f"Model preloaded successfully, embedding dimension: {len(dummy_embedding[0])}")

    @classmethod
    def get_instance(cls, embedding_model: str | None = None, device: str | None = None) -> "RAGProcessingService":
        """Get or create a RAGProcessingService instance via the service manager."""
        return _service_manager.get_service(embedding_model, device)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the model cache. Useful for testing or memory management."""
        _service_manager.clear_all()
        logger.info("RAGProcessingService cache cleared")

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
        config_manager: Optional["ConfigurationManager"] = None,  # noqa: F821 # indirect typing is fine here for now
    ) -> list[DocumentChunk]:
        """Chunk the document text and generate embeddings for each chunk.
        Returns a list of DocumentChunk objects (not yet added to DB).
        """
        import asyncio

        from ..core.config import get_config_manager

        settings = get_settings_instance()
        chunk_size = int(knowledge_base.chunk_size or settings.default_chunk_size)
        chunk_overlap = int(knowledge_base.chunk_overlap or settings.default_chunk_overlap)
        embedding_model = str(knowledge_base.embedding_model or settings.default_embedding_model)

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
                # Create a dedicated title chunk at the beginning
                title_chunk = f"Document Title: {document_title}"
                chunks.insert(0, title_chunk)
            else:
                # Legacy behavior: prepend title to the first chunk
                title_prefix = f"Document Title: {document_title}\n\n"
                chunks[0] = title_prefix + chunks[0]

        # 3. Generate embeddings asynchronously using shared ThreadPoolExecutor

        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            self.executor,
            lambda: self.model.encode(chunks, batch_size=self.batch_size, show_progress_bar=False),
        )

        # 4. Create DocumentChunk objects
        document_chunks = []
        start_char = 0
        title_chunk_offset = 1 if (document_title and title_chunk_enabled) else 0

        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=False)):
            end_char = start_char + len(chunk)

            # Determine if this is a title chunk
            is_title_chunk = idx == 0 and title_chunk_offset == 1

            # Create chunk metadata
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

            doc_chunk = DocumentChunk(
                document_id=document_id,
                knowledge_base_id=knowledge_base.id,
                chunk_index=idx,
                content=chunk,
                char_count=len(chunk),
                word_count=len(chunk.split()),
                start_char=start_char if not is_title_chunk else 0,  # Title chunks start at 0
                end_char=end_char if not is_title_chunk else len(chunk),
                embedding=embedding.tolist(),
                embedding_model=embedding_model,
                embedding_created_at=datetime.now(UTC),
                chunk_metadata=chunk_metadata,
            )
            document_chunks.append(doc_chunk)

            # Adjust start_char for next chunk
            if is_title_chunk:
                start_char = 0  # Reset for content chunks
            else:
                start_char += len(chunk) - chunk_overlap

        return document_chunks


# Utility functions for memory management and testing
def get_rag_service_stats() -> dict[str, any]:
    """Get statistics about RAG service instances for monitoring."""
    return _service_manager.get_stats()


def clear_rag_service_cache() -> None:
    """Clear all RAG service instances. Useful for testing and memory management."""
    _service_manager.clear_all()


def cleanup_rag_services() -> None:
    """Cleanup expired RAG service instances manually."""
    current_time = time.time()
    _service_manager._cleanup_expired_instances(current_time)
