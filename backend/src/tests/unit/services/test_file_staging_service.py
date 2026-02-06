"""
Unit tests for FileStagingService.

Tests cover:
- Staging and retrieval round-trip with cleanup verification
- Missing staged file error handling

These tests add value beyond integration tests by:
- Verifying the cleanup invariant (key deletion after retrieval)
- Testing error handling paths for missing files
"""

import pytest

from shu.core.cache_backend import InMemoryCacheBackend
from shu.services.file_staging_service import FileStagingError, FileStagingService


@pytest.fixture
def cache_backend() -> InMemoryCacheBackend:
    """Provide a fresh InMemoryCacheBackend for each test."""
    return InMemoryCacheBackend(cleanup_interval_seconds=0)


@pytest.fixture
def staging_service(cache_backend: InMemoryCacheBackend) -> FileStagingService:
    """Provide a FileStagingService instance with injected cache backend."""
    return FileStagingService(cache_backend)


class TestFileStagingService:
    """Unit tests for FileStagingService."""

    @pytest.mark.asyncio
    async def test_stage_and_retrieve_round_trip_with_cleanup(
        self,
        staging_service: FileStagingService,
        cache_backend: InMemoryCacheBackend,
    ):
        """Stage file, retrieve it, verify bytes match and key is deleted.

        This test validates the cleanup invariant: after retrieval, the staging
        key must be deleted from cache to prevent accumulation of stale data.
        """
        document_id = "test_doc_123"
        file_bytes = b"Hello, this is test file content with binary data \x00\x01\x02"

        # Stage the file
        staging_key = await staging_service.stage_file(document_id, file_bytes)

        # Verify staging key format
        assert staging_key == f"file_staging:{document_id}"

        # Verify file is in cache before retrieval
        cached_bytes = await cache_backend.get_bytes(staging_key)
        assert cached_bytes == file_bytes

        # Retrieve the file
        retrieved_bytes = await staging_service.retrieve_file(staging_key)

        # Verify bytes match
        assert retrieved_bytes == file_bytes

        # Verify key is deleted after retrieval (cleanup invariant)
        cached_after = await cache_backend.get_bytes(staging_key)
        assert cached_after is None

    @pytest.mark.asyncio
    async def test_retrieve_without_delete_preserves_staging_key(
        self,
        staging_service: FileStagingService,
        cache_backend: InMemoryCacheBackend,
    ):
        """Retrieve with delete_after_retrieve=False keeps key in cache."""
        document_id = "test_doc_nodelete"
        file_bytes = b"retry-safe content"

        staging_key = await staging_service.stage_file(document_id, file_bytes)

        # Retrieve without deleting
        retrieved_bytes = await staging_service.retrieve_file(staging_key, delete_after_retrieve=False)
        assert retrieved_bytes == file_bytes

        # Key should still exist in cache
        cached_after = await cache_backend.get_bytes(staging_key)
        assert cached_after == file_bytes

        # Explicit cleanup should remove it
        await staging_service.delete_staged_file(staging_key)
        cached_final = await cache_backend.get_bytes(staging_key)
        assert cached_final is None

    @pytest.mark.asyncio
    async def test_retrieve_missing_file_raises_error(
        self,
        staging_service: FileStagingService,
    ):
        """Test retrieve_file() with non-existent key raises FileStagingError.

        This tests the error handling path that integration tests may not exercise.
        """
        non_existent_key = "file_staging:non_existent_doc"

        with pytest.raises(FileStagingError) as exc_info:
            await staging_service.retrieve_file(non_existent_key)

        assert "Staged file not found" in str(exc_info.value.message)
        assert exc_info.value.details["staging_key"] == non_existent_key
