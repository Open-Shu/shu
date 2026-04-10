"""LocalEmbeddingService implementation and DI wiring.

Wraps sentence-transformers for local embedding generation. The
EmbeddingService protocol lives in embedding_protocol.py to avoid
loading sentence-transformers (~2GB) when only the protocol is needed.

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

from ..models.llm_provider import ModelType
from .config import get_settings_instance
from .embedding_protocol import EmbeddingService
from .exceptions import LLMConfigurationError
from .external_model_resolver import resolve_external_model
from .logging import get_logger

logger = get_logger(__name__)

# Re-export so existing callers don't break
__all__ = ["EmbeddingService", "LocalEmbeddingService"]


# ---------------------------------------------------------------------------
# Device resolution
# ---------------------------------------------------------------------------

_VALID_DEVICES = ("auto", "cpu", "mps", "cuda")


def resolve_embedding_device(requested: str) -> str:
    """Resolve the embedding device string to a concrete PyTorch device.

    - ``"auto"`` probes cuda → mps → cpu and picks the best available.
    - Explicit values (``"cuda"``, ``"mps"``, ``"cpu"``) are validated;
      unavailable devices raise ``RuntimeError`` so misconfigurations
      fail fast rather than silently falling back to CPU.
    """
    if requested not in _VALID_DEVICES:
        raise ValueError(f"Invalid SHU_EMBEDDING_DEVICE '{requested}'. Must be one of: {_VALID_DEVICES}")

    import torch

    if requested == "auto":
        if torch.cuda.is_available():
            logger.info("Auto-detected CUDA device for embeddings")
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            logger.info("Auto-detected MPS (Apple Silicon) device for embeddings")
            return "mps"
        logger.info("No GPU detected, using CPU for embeddings")
        return "cpu"

    # Explicit device — fail fast if unavailable
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "SHU_EMBEDDING_DEVICE='cuda' but CUDA is not available. "
            "Install a CUDA-enabled PyTorch build, or set SHU_EMBEDDING_DEVICE=auto."
        )
    if requested == "mps" and not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
        raise RuntimeError(
            "SHU_EMBEDDING_DEVICE='mps' but MPS is not available. "
            "Requires Apple Silicon with macOS 12.3+, or set SHU_EMBEDDING_DEVICE=auto."
        )
    return requested


_VALID_DTYPES = ("auto", "float32", "float16")


def resolve_embedding_dtype(requested: str, device: str) -> str:
    """Resolve embedding dtype, optionally based on the resolved device.

    - ``"auto"``: float16 on GPU (cuda/mps), float32 on CPU.
    - Explicit values pass through with validation.
    """
    if requested not in _VALID_DTYPES:
        raise ValueError(f"Invalid SHU_EMBEDDING_DTYPE '{requested}'. Must be one of: {_VALID_DTYPES}")

    if requested == "auto":
        if device in ("cuda", "mps"):
            logger.info(f"Auto-selected float16 dtype for {device} device")
            return "float16"
        logger.info("Auto-selected float32 dtype for CPU (float16 is ~9x slower on CPU)")
        return "float32"

    if requested == "float16" and device == "cpu":
        logger.warning("float16 on CPU is ~9x slower than float32 due to lack of native fp16 compute")

    return requested


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
        # Deferred import: sentence-transformers loads ~2GB of models on import.
        # Kept inside __init__ (not at module level) so that importing this module
        # doesn't trigger the load when SHU_LOCAL_EMBEDDING_ENABLED=false.
        import sentence_transformers

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
            lambda: self._model.encode_document(
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
            lambda: self._model.encode_query(
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
            lambda: self._model.encode_query(
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

    Resolution order:
    1. Return cached singleton if already initialized.
    2. If SHU_LOCAL_EMBEDDING_ENABLED=true, create a LocalEmbeddingService
       (default, backward-compatible — local is preferred when enabled).
    3. If SHU_LOCAL_EMBEDDING_ENABLED=false and an active model with
       model_type="embedding" exists in the DB, create an
       ExternalEmbeddingService using the provider's credentials.
    4. If local is disabled and no external model is configured, raise
       LLMConfigurationError.

    Suitable for use in background tasks, workers, and services. For
    FastAPI endpoints, prefer get_embedding_service_dependency().
    """
    global _embedding_service  # noqa: PLW0603

    if _embedding_service is not None:
        return _embedding_service

    settings = get_settings_instance()

    if settings.local_embedding_enabled:
        logger.info("Using local embedding service")
        device = resolve_embedding_device(settings.embedding_device)
        dtype = resolve_embedding_dtype(settings.embedding_dtype, device)
        _embedding_service = _service_manager.get_service(
            model_name=settings.default_embedding_model,
            device=device,
            batch_size=settings.embedding_batch_size,
            dtype=dtype,
        )
        return _embedding_service

    resolved = await resolve_external_model(ModelType.EMBEDDING)
    if resolved is None:
        raise LLMConfigurationError(
            "No embedding service available: SHU_LOCAL_EMBEDDING_ENABLED=false "
            "and no external embedding model (model_type='embedding') is configured "
            "in llm_models. Either enable local embedding or register an external model."
        )

    dimension = resolved.config.get("dimension")
    if not dimension:
        raise LLMConfigurationError(
            f"External embedding model '{resolved.model_name}' is missing 'dimension' "
            f"in its config. Set config.dimension on the llm_models record."
        )

    from ..services.external_embedding_service import ExternalEmbeddingService

    logger.info(
        "Using external embedding service",
        extra={"model": resolved.model_name, "provider": resolved.provider_name, "dimension": dimension},
    )
    _embedding_service = ExternalEmbeddingService(
        api_base_url=resolved.api_base_url,
        api_key=resolved.api_key,
        model_name=resolved.model_name,
        dimension=int(dimension),
        provider_id=resolved.provider_id,
        model_id=resolved.model_id,
        query_prefix=resolved.config.get("query_prefix", ""),
        document_prefix=resolved.config.get("document_prefix", ""),
    )
    return _embedding_service


def get_embedding_service_dependency() -> EmbeddingService:
    """Dependency injection function for EmbeddingService.

    Use in FastAPI endpoints with Depends(). Returns the cached singleton
    if available, otherwise raises. Startup should have initialized the
    service via initialize_embedding_service().

    Returns:
        An EmbeddingService instance.

    """
    global _embedding_service  # noqa: PLW0603

    if _embedding_service is not None:
        return _embedding_service

    # Fallback: create synchronously via local path (startup should have initialized)
    logger.debug("get_embedding_service_dependency called before async initialization")
    settings = get_settings_instance()

    if not settings.local_embedding_enabled:
        raise LLMConfigurationError(
            "Embedding service not initialized and local embedding is disabled. "
            "Ensure initialize_embedding_service() is called during startup."
        )

    device = resolve_embedding_device(settings.embedding_device)
    dtype = resolve_embedding_dtype(settings.embedding_dtype, device)
    _embedding_service = _service_manager.get_service(
        model_name=settings.default_embedding_model,
        device=device,
        batch_size=settings.embedding_batch_size,
        dtype=dtype,
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
