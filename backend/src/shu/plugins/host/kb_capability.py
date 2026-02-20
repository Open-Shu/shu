from __future__ import annotations

import logging
from typing import Any

from ...core.database import get_db_session
from ...knowledge.ko import KnowledgeObject
from ...services.ingestion_service import ingest_document as _host_ingest_document
from ...services.ingestion_service import ingest_email as _host_ingest_email
from ...services.ingestion_service import ingest_text as _host_ingest_text
from ...services.ingestion_service import ingest_thread as _host_ingest_thread
from ...services.knowledge_object_service import delete_ko_by_external_id as _host_delete_ko_by_external_id
from ...services.knowledge_object_service import delete_kos_by_external_ids as _host_delete_kos_by_external_ids
from ...services.knowledge_object_service import upsert_knowledge_object as _host_upsert_knowledge_object
from .base import ImmutableCapabilityMixin

logger = logging.getLogger(__name__)


class KbCapability(ImmutableCapabilityMixin):
    """Knowledge base ingestion capability for plugins.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' knowledge bases.
    """

    __slots__ = ("_ocr_mode", "_plugin_name", "_schedule_id", "_user_id")

    _plugin_name: str
    _user_id: str
    _schedule_id: str | None
    _ocr_mode: str | None

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        ocr_mode: str | None = None,
        schedule_id: str | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_schedule_id", str(schedule_id) if schedule_id else None)
        m = (ocr_mode or "").strip().lower() if isinstance(ocr_mode, str) else None
        object.__setattr__(self, "_ocr_mode", m if m in {"auto", "always", "never", "fallback"} else None)

    async def upsert_knowledge_object(self, knowledge_base_id: str, ko: dict[str, Any] | KnowledgeObject) -> str:
        # Normalize KO
        ko_obj = ko if isinstance(ko, KnowledgeObject) else KnowledgeObject(**ko)
        db = await get_db_session()
        try:
            ko_id = await _host_upsert_knowledge_object(db, knowledge_base_id, ko_obj)
            logger.info(
                "host.kb.upsert",
                extra={
                    "plugin": self._plugin_name,
                    "user_id": self._user_id,
                    "kb": knowledge_base_id,
                    "ko_id": ko_id,
                },
            )
            return ko_id
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def ingest_document(
        self,
        knowledge_base_id: str,
        *,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        source_id: str,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = await get_db_session()
        try:
            return await _host_ingest_document(
                db,
                knowledge_base_id,
                plugin_name=self._plugin_name,
                user_id=self._user_id,
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
                source_id=source_id,
                source_url=source_url,
                attributes=attributes,
                ocr_mode=self._ocr_mode,
            )
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def ingest_email(
        self,
        knowledge_base_id: str,
        *,
        subject: str,
        sender: str | None,
        recipients: dict[str, Any],
        date: str | None,
        message_id: str,
        thread_id: str | None,
        body_text: str | None,
        body_html: str | None = None,
        labels: list[str] | None = None,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = await get_db_session()
        try:
            return await _host_ingest_email(
                db,
                knowledge_base_id,
                plugin_name=self._plugin_name,
                user_id=self._user_id,
                subject=subject,
                sender=sender,
                recipients=recipients,
                date=date,
                message_id=message_id,
                thread_id=thread_id,
                body_text=body_text,
                body_html=body_html,
                labels=labels,
                source_url=source_url,
                attributes=attributes,
            )
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def ingest_text(
        self,
        knowledge_base_id: str,
        *,
        title: str,
        content: str,
        source_id: str,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = await get_db_session()
        try:
            return await _host_ingest_text(
                db,
                knowledge_base_id,
                plugin_name=self._plugin_name,
                user_id=self._user_id,
                title=title,
                content=content,
                source_id=source_id,
                source_url=source_url,
                attributes=attributes,
            )
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def ingest_thread(
        self,
        knowledge_base_id: str,
        *,
        title: str,
        content: str,
        thread_id: str,
        source_url: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        db = await get_db_session()
        try:
            return await _host_ingest_thread(
                db,
                knowledge_base_id,
                plugin_name=self._plugin_name,
                user_id=self._user_id,
                title=title,
                content=content,
                thread_id=thread_id,
                source_url=source_url,
                attributes=attributes,
            )
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def delete_ko(self, *, external_id: str) -> dict[str, Any]:
        """Delete a single KO owned by this plugin within the feed's KB.
        Security: Only allowed when running under a feed (schedule_id bound in host context).
        The kb_id is resolved from the feed; plugins cannot override it.
        """
        if not self._schedule_id:
            raise PermissionError("kb_delete_not_allowed_outside_feed")
        db = await get_db_session()
        try:
            from sqlalchemy import select

            from ...models.plugin_feed import PluginFeed

            # Resolve kb_id from the feed params
            res = await db.execute(select(PluginFeed).where(PluginFeed.id == self._schedule_id))
            sched = res.scalars().first()
            if not sched:
                raise PermissionError("feed_not_found_for_delete")
            params = sched.params or {}
            kb_id = params.get("kb_id") if isinstance(params, dict) else None
            if not kb_id:
                raise PermissionError("kb_id_missing_in_feed_params")

            out = await _host_delete_ko_by_external_id(
                db, kb_id=kb_id, external_id=external_id, plugin_name=self._plugin_name
            )
            try:
                logger.info(
                    "host.kb.delete_ko",
                    extra={
                        "plugin": self._plugin_name,
                        "user_id": self._user_id,
                        "kb": kb_id,
                        "schedule_id": self._schedule_id,
                        "external_id": external_id,
                        "deleted": bool(out.get("deleted")),
                        "ko_id": out.get("ko_id"),
                    },
                )
            except Exception:
                pass
            return out
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def delete_kos_batch(self, *, external_ids: list[str]) -> dict[str, Any]:
        """Delete multiple KOs owned by this plugin within the feed's KB.
        Returns {deleted_count, failed}.
        """
        if not self._schedule_id:
            raise PermissionError("kb_delete_not_allowed_outside_feed")
        ids = list(external_ids or [])
        if not ids:
            return {"deleted_count": 0, "failed": []}
        db = await get_db_session()
        try:
            from sqlalchemy import select

            from ...models.plugin_feed import PluginFeed

            res = await db.execute(select(PluginFeed).where(PluginFeed.id == self._schedule_id))
            sched = res.scalars().first()
            if not sched:
                raise PermissionError("feed_not_found_for_delete")
            params = sched.params or {}
            kb_id = params.get("kb_id") if isinstance(params, dict) else None
            if not kb_id:
                raise PermissionError("kb_id_missing_in_feed_params")

            out = await _host_delete_kos_by_external_ids(
                db, kb_id=kb_id, external_ids=ids, plugin_name=self._plugin_name
            )
            try:
                logger.info(
                    "host.kb.delete_kos_batch",
                    extra={
                        "plugin": self._plugin_name,
                        "user_id": self._user_id,
                        "kb": kb_id,
                        "schedule_id": self._schedule_id,
                        "deleted_count": out.get("deleted_count"),
                        "failed": out.get("failed"),
                    },
                )
            except Exception:
                pass
            return out
        finally:
            try:
                await db.close()
            except Exception:
                pass
