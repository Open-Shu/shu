"""HTTP client management for Shu RAG Backend.

This module provides a centralized HTTP client with connection pooling
for external API calls (Google Drive, LLM providers, etc.).
"""

from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings_instance
from .logging import get_logger

logger = get_logger(__name__)


class HTTPClientManager:
    """Manages HTTP client connections with pooling for external APIs."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._settings = get_settings_instance()

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling."""
        if self._client is None:
            logger.debug("Creating new HTTP client with connection pooling")
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=30.0),
                timeout=httpx.Timeout(
                    connect=self._settings.llm_global_timeout,
                    read=self._settings.llm_streaming_read_timeout,
                    write=self._settings.llm_global_timeout,
                    pool=self._settings.llm_global_timeout,
                ),
                follow_redirects=True,
                headers={"User-Agent": f"Shu-RAG-Backend/{self._settings.version}"},
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            logger.debug("Closing HTTP client")
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> httpx.AsyncClient:
        """Async context manager entry."""
        return await self.get_client()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> Any:
        """Async context manager exit."""
        await self.close()


# Global HTTP client manager instance
http_client_manager = HTTPClientManager()


@lru_cache(maxsize=1)
def get_http_client_manager() -> HTTPClientManager:
    """Get the global HTTP client manager instance."""
    return http_client_manager


async def get_http_client() -> httpx.AsyncClient:
    """Get HTTP client for external API calls."""
    manager = get_http_client_manager()
    return await manager.get_client()


async def close_http_client() -> None:
    """Close HTTP client (call during shutdown)."""
    manager = get_http_client_manager()
    await manager.close()
