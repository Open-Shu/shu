"""File Staging Service for Document Ingestion Pipeline.

This service handles staging file bytes between pipeline stages using the
CacheBackend interface. It provides a consistent code path for both Redis
(production) and in-memory (development) backends.

All files are staged using native binary storage via set_bytes() and get_bytes()
methods, keeping job payloads small and avoiding base64 encoding overhead.
"""

from typing import Any

from ..core.cache_backend import CacheBackend
from ..core.exceptions import ShuException
from ..core.logging import get_logger

logger = get_logger(__name__)

# Default TTL for staged files (1 hour)
DEFAULT_STAGING_TTL = 3600


class FileStagingError(ShuException):
    """Raised when file staging operations fail."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_code="FILE_STAGING_ERROR",
            status_code=500,
            details=details,
        )


class FileStagingService:
    """Service for staging file bytes between pipeline stages.

    Uses CacheBackend.set_bytes/get_bytes for native binary storage.
    Works with both RedisCacheBackend and InMemoryCacheBackend.

    Args:
        cache: The CacheBackend instance to use for storage and retrieval.
        staging_ttl: TTL in seconds for staged files. Defaults to 3600 (1 hour).
    """

    def __init__(self, cache: CacheBackend, staging_ttl: int = DEFAULT_STAGING_TTL):
        """Initialize the file staging service.

        Args:
            cache: The CacheBackend instance to use for storage and retrieval.
            staging_ttl: TTL in seconds for staged files. Defaults to 3600 (1 hour).
        """
        self._cache = cache
        self._staging_ttl = staging_ttl

    async def stage_file(
        self,
        document_id: str,
        file_bytes: bytes,
    ) -> str:
        """Stage file bytes for OCR worker.

        Args:
            document_id: The document ID to associate with the staged file.
            file_bytes: The raw file bytes to stage.

        Returns:
            staging_key: Cache key reference for retrieval.

        Raises:
            FileStagingError: If staging fails.
        """
        staging_key = f"file_staging:{document_id}"

        try:
            success = await self._cache.set_bytes(
                staging_key,
                file_bytes,
                ttl_seconds=self._staging_ttl,
            )
            if not success:
                raise FileStagingError(
                    f"Failed to stage file for document {document_id}",
                    details={"document_id": document_id, "staging_key": staging_key},
                )

            logger.debug(
                "Staged file for document",
                extra={
                    "document_id": document_id,
                    "staging_key": staging_key,
                    "file_size": len(file_bytes),
                },
            )
            return staging_key

        except FileStagingError:
            raise
        except Exception as e:
            raise FileStagingError(
                f"Failed to stage file for document {document_id}: {e}",
                details={"document_id": document_id, "error": str(e)},
            ) from e

    async def retrieve_file(
        self,
        staging_key: str,
    ) -> bytes:
        """Retrieve file bytes from staging.

        Retrieves the staged file and cleans up the staging key after retrieval.

        Args:
            staging_key: The cache key for the staged file.

        Returns:
            The raw file bytes.

        Raises:
            FileStagingError: If staged file not found or retrieval fails.
        """
        try:
            file_bytes = await self._cache.get_bytes(staging_key)
            if file_bytes is None:
                raise FileStagingError(
                    f"Staged file not found: {staging_key}",
                    details={"staging_key": staging_key},
                )

            # Clean up after retrieval
            try:
                await self._cache.delete(staging_key)
            except Exception as cleanup_error:
                # Log but don't fail - file was retrieved successfully
                logger.warning(
                    "Failed to clean up staging key after retrieval",
                    extra={
                        "staging_key": staging_key,
                        "error": str(cleanup_error),
                    },
                )

            logger.debug(
                "Retrieved and cleaned up staged file",
                extra={
                    "staging_key": staging_key,
                    "file_size": len(file_bytes),
                },
            )
            return file_bytes

        except FileStagingError:
            raise
        except Exception as e:
            raise FileStagingError(
                f"Failed to retrieve staged file: {staging_key}: {e}",
                details={"staging_key": staging_key, "error": str(e)},
            ) from e
