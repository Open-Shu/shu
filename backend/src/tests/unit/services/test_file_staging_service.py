"""
Unit tests for FileStagingService (disk-based implementation).

Tests cover:
- Staging and retrieval round-trip with cleanup verification
- delete_after_retrieve=False preserves the file
- Missing staged file error handling
- Explicit delete_staged_file removes the file
"""

from pathlib import Path

import pytest

from shu.services.file_staging_service import FileStagingError, FileStagingService


@pytest.fixture
def staging_dir(tmp_path):
    """Provide a fresh temporary directory for each test."""
    return str(tmp_path / "staging")


@pytest.fixture
def staging_service(staging_dir: str) -> FileStagingService:
    """Provide a FileStagingService instance using a temp staging directory."""
    return FileStagingService(staging_dir=staging_dir)


class TestFileStagingService:
    """Unit tests for FileStagingService."""

    @pytest.mark.asyncio
    async def test_stage_and_retrieve_round_trip_with_cleanup(
        self,
        staging_service: FileStagingService,
        staging_dir: str,
    ):
        """Stage file, retrieve it, verify bytes match and file is deleted."""
        document_id = "test_doc_123"
        file_bytes = b"Hello, this is test file content with binary data \x00\x01\x02"

        staging_key = await staging_service.stage_file(document_id, file_bytes)

        # staging_key is a file path
        assert Path(staging_key).is_file()
        assert staging_key.startswith(staging_dir)
        assert document_id in Path(staging_key).name

        # Retrieve the file
        retrieved_bytes = await staging_service.retrieve_file(staging_key)

        assert retrieved_bytes == file_bytes
        # File must be deleted after retrieval (cleanup invariant)
        assert not Path(staging_key).exists()

    @pytest.mark.asyncio
    async def test_retrieve_without_delete_preserves_file(
        self,
        staging_service: FileStagingService,
    ):
        """Retrieve with delete_after_retrieve=False keeps the file on disk."""
        document_id = "test_doc_nodelete"
        file_bytes = b"retry-safe content"

        staging_key = await staging_service.stage_file(document_id, file_bytes)

        retrieved_bytes = await staging_service.retrieve_file(staging_key, delete_after_retrieve=False)
        assert retrieved_bytes == file_bytes

        # File should still exist
        assert Path(staging_key).is_file()

        # Explicit cleanup should remove it
        await staging_service.delete_staged_file(staging_key)
        assert not Path(staging_key).exists()

    @pytest.mark.asyncio
    async def test_retrieve_missing_file_raises_error(
        self,
        staging_service: FileStagingService,
        staging_dir: str,
    ):
        """retrieve_file() with a non-existent path raises FileStagingError."""
        non_existent_key = str(Path(staging_dir) / "non_existent_doc_abc123.bin")

        with pytest.raises(FileStagingError) as exc_info:
            await staging_service.retrieve_file(non_existent_key)

        assert "Staged file not found" in str(exc_info.value.message)
        assert exc_info.value.details["staging_key"] == non_existent_key

    @pytest.mark.asyncio
    async def test_staging_dir_created_on_init(self, tmp_path):
        """FileStagingService creates the staging directory if it doesn't exist."""
        new_dir = str(tmp_path / "does" / "not" / "exist")
        assert not Path(new_dir).exists()

        FileStagingService(staging_dir=new_dir)

        assert Path(new_dir).is_dir()

    @pytest.mark.asyncio
    async def test_unique_keys_for_same_document_id(
        self,
        staging_service: FileStagingService,
    ):
        """Two stage_file calls for the same document_id produce different paths."""
        document_id = "same_doc"
        file_bytes = b"content"

        key1 = await staging_service.stage_file(document_id, file_bytes)
        key2 = await staging_service.stage_file(document_id, file_bytes)

        assert key1 != key2

        # Clean up
        await staging_service.delete_staged_file(key1)
        await staging_service.delete_staged_file(key2)
