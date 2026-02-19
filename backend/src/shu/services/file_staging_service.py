"""File Staging Service for Document Ingestion Pipeline.

Stages file bytes to disk between pipeline stages (plugin execution → OCR worker).
Each staged file is written to ``$SHU_INGESTION_STAGING_DIR/{document_id}_{uuid}.bin``
and the file path is returned as the staging key.  Workers read from disk, process,
and delete the file on completion.

The public interface (stage_file, retrieve_file, delete_staged_file) is unchanged
from the previous CacheBackend-based implementation — callers require no changes.
"""

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import get_settings_instance
from ..core.exceptions import ShuException
from ..core.logging import get_logger

logger = get_logger(__name__)


class FileStagingError(ShuException):
    """Raised when file staging operations fail."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            error_code="FILE_STAGING_ERROR",
            status_code=500,
            details=details,
        )


class FileStagingService:
    """Service for staging file bytes between pipeline stages.

    Writes bytes to disk under ``SHU_INGESTION_STAGING_DIR`` and returns the
    file path as the staging key.  Reads and deletes on retrieval.

    The staging directory is created on first instantiation if it does not exist.
    In multi-replica deployments the directory must reside on a shared volume
    (ReadWriteMany) so that the writing process and the reading worker can both
    access it regardless of which pod they run on.

    Args:
        staging_dir: Override the staging directory (defaults to settings value).
            Primarily used in tests.

    """

    def __init__(self, staging_dir: str | None = None) -> None:
        settings = get_settings_instance()
        self._staging_dir = staging_dir or settings.ingestion_staging_dir
        os.makedirs(self._staging_dir, exist_ok=True)

    async def stage_file(
        self,
        document_id: str,
        file_bytes: bytes,
    ) -> str:
        """Stage file bytes to disk.

        Args:
            document_id: The document ID — used as a filename prefix for debuggability.
            file_bytes: The raw file bytes to stage.

        Returns:
            staging_key: Absolute path to the staged file on disk.

        Raises:
            FileStagingError: If the write fails.

        """
        filename = f"{document_id}_{uuid4().hex}.bin"
        staging_path = str(Path(self._staging_dir) / filename)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_file, staging_path, file_bytes)

            logger.debug(
                "Staged file for document",
                extra={
                    "document_id": document_id,
                    "staging_key": staging_path,
                    "file_size": len(file_bytes),
                },
            )
            return staging_path

        except Exception as e:
            raise FileStagingError(
                f"Failed to stage file for document {document_id}: {e}",
                details={"document_id": document_id, "error": str(e)},
            ) from e

    async def retrieve_file(
        self,
        staging_key: str,
        delete_after_retrieve: bool = True,
    ) -> bytes:
        """Retrieve file bytes from disk staging.

        Args:
            staging_key: The file path returned by stage_file.
            delete_after_retrieve: If True (default), deletes the file after
                reading.  Set to False for retry-safe callers that will call
                delete_staged_file explicitly on success.

        Returns:
            The raw file bytes.

        Raises:
            FileStagingError: If the file is not found or the read fails.

        """
        if not os.path.exists(staging_key):
            raise FileStagingError(
                f"Staged file not found: {staging_key}",
                details={"staging_key": staging_key},
            )

        try:
            loop = asyncio.get_running_loop()
            file_bytes = await loop.run_in_executor(None, _read_file, staging_key)

            if delete_after_retrieve:
                try:
                    os.unlink(staging_key)
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to delete staging file after retrieval",
                        extra={"staging_key": staging_key, "error": str(cleanup_error)},
                    )

            logger.debug(
                "Retrieved staged file",
                extra={
                    "staging_key": staging_key,
                    "file_size": len(file_bytes),
                    "deleted": delete_after_retrieve,
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

    async def delete_staged_file(self, staging_key: str) -> None:
        """Delete a staged file from disk.

        Args:
            staging_key: The file path to delete.

        """
        try:
            if os.path.exists(staging_key):
                os.unlink(staging_key)
        except Exception as e:
            logger.warning(
                "Failed to delete staging file",
                extra={"staging_key": staging_key, "error": str(e)},
            )


# ---------------------------------------------------------------------------
# Sync helpers (run in executor to avoid blocking the event loop)
# ---------------------------------------------------------------------------


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
