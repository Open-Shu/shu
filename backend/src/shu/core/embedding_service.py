"""EmbeddingService protocol and LocalEmbeddingService implementation.

Abstracts embedding generation behind a protocol interface following the
CacheBackend/QueueBackend pattern. LocalEmbeddingService wraps
sentence-transformers for local embedding generation.

DI wiring:
    - get_embedding_service()           — async singleton factory (workers, services)
    - get_embedding_service_dependency() — sync DI helper for FastAPI Depends()
    - initialize_embedding_service()    — app startup initializer
    - reset_embedding_service()         — test teardown
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, runtime_checkable

import sentence_transformers

from .config import get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingService(Protocol):
    """Protocol for embedding generation services.

    Implementations must provide async methods for generating embeddings
    from text. Supports both batch and single-query paths.
    """

    @property
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors produced by this service."""
        ...

    @property
    def model_name(self) -> str:
        """Name of the underlying embedding model."""
        ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed. Empty list returns [].

        Returns:
            List of embedding vectors, one per input text.

        """
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Generate an embedding for a single query text.

        Args:
            text: The query text to embed.

        Returns:
            A single embedding vector.

        """
        ...

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of query texts.

        Like embed_texts(), but applies the query prompt for asymmetric
        models (e.g., Snowflake arctic-embed). Use this when embedding
        synthesized queries or any text that will be matched against
        user search queries.

        Args:
            texts: List of query strings to embed. Empty list returns [].

        Returns:
            List of embedding vectors, one per input text.

        """
        ...


# ---------------------------------------------------------------------------
# LocalEmbeddingService
# ---------------------------------------------------------------------------


class LocalEmbeddingService:
    """Local embedding service using sentence-transformers.

    Loads a SentenceTransformer model and runs inference via a shared
    ThreadPoolExecutor to avoid blocking the async event loop.
    """

    def __init__(
        self,
        model_name: str,
        device: str,
        batch_size: int,
        executor: ThreadPoolExecutor,
        dtype: str = "float32",
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._executor = executor

        # Validate dtype
        valid_dtypes = ("float32", "float16")
        if dtype not in valid_dtypes:
            raise ValueError(f"Invalid embedding_dtype '{dtype}'. Must be one of: {valid_dtypes}")

        logger.info(f"Loading SentenceTransformer model: {model_name} (device={device}, dtype={dtype})")

        # Set cache directory to ensure models are cached locally
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        os.makedirs(cache_dir, exist_ok=True)

        # Configure Hugging Face to use local cache and reduce API calls
        os.environ.setdefault("HF_HUB_CACHE", cache_dir)
        os.environ.setdefault("HF_HUB_OFFLINE", "0")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        # Build model kwargs for dtype support
        model_kwargs: dict = {}
        if dtype == "float16":
            import torch

            model_kwargs["torch_dtype"] = torch.float16
            logger.info("Using float16 precision (reduced memory footprint)")

        self._model = sentence_transformers.SentenceTransformer(
            model_name,
            device=device,
            cache_folder=cache_dir,
            model_kwargs=model_kwargs if model_kwargs else None,
        )

        # Cache dimension from the loaded model
        self._dimension: int = self._model.get_sentence_embedding_dimension()

        # Cache prompt names for asymmetric models (e.g., Snowflake arctic-embed, E5, BGE).
        # Models define prompts in config_sentence_transformers.json. We detect supported
        # prompt names at load time; unsupported names stay None (no prefix applied).
        # sentence-transformers raises ValueError on unknown prompt_name, so we must check.
        self._query_prompt_name: str | None = "query" if "query" in self._model.prompts else None
        self._document_prompt_name: str | None = next(
            (name for name in ("document", "passage") if name in self._model.prompts), None
        )

        logger.info(
            f"Successfully loaded SentenceTransformer model: {model_name} (dim={self._dimension})"
            f"{f', query_prompt={self._query_prompt_name!r}' if self._query_prompt_name else ''}"
            f"{f', doc_prompt={self._document_prompt_name!r}' if self._document_prompt_name else ''}"
        )

        # Preload model to ensure full initialization
        logger.debug("Preloading model with dummy text")
        self._model.encode(["test"], batch_size=1, show_progress_bar=False)
        logger.debug("Model preloaded successfully")

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            self._executor,
            lambda: self._model.encode(
                texts,
                batch_size=self._batch_size,
                show_progress_bar=False,
                prompt_name=self._document_prompt_name,
            ),
        )
        return [e.tolist() for e in embeddings]

    async def embed_query(self, text: str) -> list[float]:
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(
            self._executor,
            lambda: self._model.encode(
                [text], batch_size=1, show_progress_bar=False, prompt_name=self._query_prompt_name
            )[0],
        )
        return embedding.tolist()

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            self._executor,
            lambda: self._model.encode(
                texts,
                batch_size=self._batch_size,
                show_progress_bar=False,
                prompt_name=self._query_prompt_name,
            ),
        )
        return [e.tolist() for e in embeddings]


# ---------------------------------------------------------------------------
# Instance management (extracted from RAGServiceManager)
# ---------------------------------------------------------------------------


class _EmbeddingServiceManager:
    """Memory-aware manager for LocalEmbeddingService instances.

    Handles instance caching with TTL, LRU eviction, and a shared
    ThreadPoolExecutor.
    """

    def __init__(self) -> None:
        self._instances: dict[str, dict] = {}  # key -> {instance, last_used, created_at}
        self._executor: ThreadPoolExecutor | None = None
        self._cache_ttl = 3600  # 1 hour TTL for unused instances
        self._max_instances = 5

    def get_service(
        self,
        model_name: str,
        device: str,
        batch_size: int,
        dtype: str = "float32",
    ) -> LocalEmbeddingService:
        """Get or create a LocalEmbeddingService instance."""
        instance_key = f"{model_name}:{device}:{dtype}"
        current_time = time.time()

        self._cleanup_expired_instances(current_time)

        if instance_key in self._instances:
            entry = self._instances[instance_key]
            entry["last_used"] = current_time
            logger.debug(f"Reusing LocalEmbeddingService instance for {instance_key}")
            return entry["instance"]

        if len(self._instances) >= self._max_instances:
            self._evict_oldest_instance()

        logger.info(f"Creating new LocalEmbeddingService instance for {instance_key}")
        instance = LocalEmbeddingService(
            model_name=model_name,
            device=device,
            batch_size=batch_size,
            executor=self._get_executor(),
            dtype=dtype,
        )

        self._instances[instance_key] = {
            "instance": instance,
            "last_used": current_time,
            "created_at": current_time,
        }

        return instance

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            settings = get_settings_instance()
            embedding_threads = max(1, int(getattr(settings, "embedding_threads", 4)))
            self._executor = ThreadPoolExecutor(max_workers=embedding_threads, thread_name_prefix="embedding-worker")
            logger.debug(f"Created ThreadPoolExecutor for embedding (threads={embedding_threads})")
        return self._executor

    def _cleanup_expired_instances(self, current_time: float) -> None:
        expired_keys = [
            key for key, entry in self._instances.items() if current_time - entry["last_used"] > self._cache_ttl
        ]
        for key in expired_keys:
            entry = self._instances.pop(key)
            logger.info(f"Evicting expired embedding service instance: {key}")
            self._cleanup_instance(entry["instance"])

    def _evict_oldest_instance(self) -> None:
        if not self._instances:
            return
        oldest_key = min(self._instances.keys(), key=lambda k: self._instances[k]["last_used"])
        entry = self._instances.pop(oldest_key)
        logger.info(f"Evicting oldest embedding service instance: {oldest_key}")
        self._cleanup_instance(entry["instance"])

    def _cleanup_instance(self, instance: LocalEmbeddingService) -> None:
        try:
            was_cuda = hasattr(instance, "_device") and instance._device.startswith("cuda")

            if hasattr(instance, "_model") and instance._model is not None:
                try:
                    if hasattr(instance._model, "tokenizer") and instance._model.tokenizer is not None:
                        instance._model.tokenizer = None
                except Exception:
                    pass
                instance._model = None  # type: ignore[assignment]

            # Release cached GPU memory after clearing model references
            if was_cuda:
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

            logger.debug("Cleaned up LocalEmbeddingService instance resources")
        except Exception as e:
            logger.warning(f"Error cleaning up embedding service instance: {e}")

    def clear_all(self) -> None:
        import gc

        logger.info("Clearing all embedding service instances")

        for entry in self._instances.values():
            self._cleanup_instance(entry["instance"])
        self._instances.clear()

        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._executor = None
            logger.debug("Shutdown embedding ThreadPoolExecutor")

        gc.collect()

    def get_stats(self) -> dict:
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

    def cleanup_expired(self) -> None:
        self._cleanup_expired_instances(time.time())


# Global manager
_service_manager = _EmbeddingServiceManager()


# ---------------------------------------------------------------------------
# DI wiring
# ---------------------------------------------------------------------------

# Global singleton
_embedding_service: EmbeddingService | None = None


async def get_embedding_service() -> EmbeddingService:
    """Get the configured embedding service (singleton).

    Creates a LocalEmbeddingService using settings for model, device, batch
    size, and dtype. Suitable for use in background tasks, workers, and
    services. For FastAPI endpoints, prefer get_embedding_service_dependency().

    Returns:
        The configured EmbeddingService instance.

    """
    global _embedding_service  # noqa: PLW0603

    if _embedding_service is not None:
        return _embedding_service

    settings = get_settings_instance()

    _embedding_service = _service_manager.get_service(
        model_name=settings.default_embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        dtype=settings.embedding_dtype,
    )

    return _embedding_service


def get_embedding_service_dependency() -> EmbeddingService:
    """Dependency injection function for EmbeddingService.

    Use in FastAPI endpoints with Depends(). Returns the cached singleton
    if available, otherwise creates one synchronously via the manager.

    Returns:
        An EmbeddingService instance.

    """
    global _embedding_service  # noqa: PLW0603

    if _embedding_service is not None:
        return _embedding_service

    # Fallback: create synchronously (startup should have initialized)
    logger.debug("get_embedding_service_dependency called before async initialization")
    settings = get_settings_instance()
    _embedding_service = _service_manager.get_service(
        model_name=settings.default_embedding_model,
        device=settings.embedding_device,
        batch_size=settings.embedding_batch_size,
        dtype=settings.embedding_dtype,
    )
    return _embedding_service


async def initialize_embedding_service() -> EmbeddingService:
    """Initialize the embedding service during application startup.

    Should be called during FastAPI app startup to ensure the embedding
    service is loaded before handling requests.

    Returns:
        The initialized EmbeddingService instance.

    """
    return await get_embedding_service()


def reset_embedding_service() -> None:
    """Reset the embedding service singleton (for testing only)."""
    global _embedding_service  # noqa: PLW0603
    _embedding_service = None
    _service_manager.clear_all()


# ---------------------------------------------------------------------------
# Utility functions for resource management API and monitoring
# ---------------------------------------------------------------------------


def get_embedding_service_stats() -> dict:
    """Get statistics about embedding service instances for monitoring."""
    return _service_manager.get_stats()


def clear_embedding_service_cache() -> None:
    """Clear all embedding service instances. Useful for testing and memory management."""
    global _embedding_service  # noqa: PLW0603
    _embedding_service = None
    _service_manager.clear_all()


def cleanup_embedding_services() -> None:
    """Cleanup expired embedding service instances manually."""
    _service_manager.cleanup_expired()
