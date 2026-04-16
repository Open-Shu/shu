from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .base import ImmutableCapabilityMixin

if TYPE_CHECKING:
    from .http_capability import HttpCapability
    from .kb_capability import KbCapability

logger = logging.getLogger(__name__)


class IngestCapability(ImmutableCapabilityMixin):
    """Fetch-and-ingest compound capability for plugins.

    Keeps multi-MB file bytes inside the parent process so they never
    cross the IPC boundary to the sandbox child.  The plugin calls
    ``host.ingest.from_http(...)`` over RPC; the parent does the HTTP
    fetch + KB ingest locally.

    Security: Immutable via ImmutableCapabilityMixin to prevent plugins
    from swapping the underlying http/kb delegates or identity fields.
    """

    __slots__ = ("_http", "_kb", "_plugin_name", "_user_id")

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        http: HttpCapability,
        kb: KbCapability,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_http", http)
        object.__setattr__(self, "_kb", kb)

    async def from_http(
        self,
        knowledge_base_id: str,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        filename: str,
        mime_type: str,
        source_id: str | None = None,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch bytes from a URL and ingest into a knowledge base in one step.

        This keeps multi-MB file bytes inside the parent process -- they
        never cross the IPC boundary to the sandbox child.  The plugin
        calls ``host.ingest.from_http(...)`` over RPC; the parent's
        IngestCapability does the HTTP fetch + KB ingest locally.
        """
        # NB: ``filename`` is a built-in LogRecord attribute; passing it
        # via ``extra`` raises KeyError at record construction. Use a
        # namespaced key instead.
        logger.info(
            "host.ingest.from_http",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "kb": knowledge_base_id,
                "url": url,
                "file_name": filename,
            },
        )

        result = await self._http.fetch_bytes(
            method, url, headers=headers or {}, params=params or {},
        )
        file_bytes: bytes = result["content"]

        ingestion_result = await self._kb.ingest_document(
            knowledge_base_id,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            source_id=source_id,
            source_url=source_url,
            attributes=attributes,
        )

        logger.info(
            "host.ingest.from_http.done",
            extra={
                "plugin": self._plugin_name,
                "user_id": self._user_id,
                "kb": knowledge_base_id,
                "file_name": filename,
                "bytes_fetched": len(file_bytes),
            },
        )

        return ingestion_result
