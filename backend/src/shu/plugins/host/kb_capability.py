from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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

# Dispatch table for KB search operations.  Populated lazily on first call to
# _with_search_service to avoid the circular import chain that arises when
# kb_capability is imported via shu.plugins.host.__init__ before
# kb_search_service is fully initialised.  Only methods explicitly listed here
# are callable — getattr is never used for dispatch.
_SEARCH_OPS: dict[str, Any] = {}


class KbCapability(ImmutableCapabilityMixin):
    """Knowledge base ingestion capability for plugins.

    Security: This class is immutable (via ImmutableCapabilityMixin) to prevent
    plugins from mutating _plugin_name or _user_id to access other plugins' knowledge bases.
    """

    __slots__ = ("_knowledge_base_ids", "_ocr_mode", "_plugin_name", "_schedule_id", "_user_id")

    _plugin_name: str
    _user_id: str
    _schedule_id: str | None
    _ocr_mode: str | None
    _knowledge_base_ids: list[str]

    def __init__(
        self,
        *,
        plugin_name: str,
        user_id: str,
        ocr_mode: str | None = None,
        schedule_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> None:
        object.__setattr__(self, "_plugin_name", plugin_name)
        object.__setattr__(self, "_user_id", user_id)
        object.__setattr__(self, "_schedule_id", str(schedule_id) if schedule_id else None)
        m = (ocr_mode or "").strip().lower() if isinstance(ocr_mode, str) else None
        object.__setattr__(self, "_ocr_mode", m if m in {"auto", "always", "never", "fallback"} else None)
        object.__setattr__(self, "_knowledge_base_ids", list(knowledge_base_ids or []))

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

    async def _check_kb_access(self, db: AsyncSession) -> dict[str, Any] | None:
        """Verify the executing user has RBAC access to every bound KB.

        This check runs for all entry points (direct execute, experience executor,
        LLM tool-calling), so no entry point can bypass it.

        Args:
            db: Active database session.

        Returns:
            ``None`` if the user has access to all bound KBs, or a structured
            error dict if the user is not found or is denied access to any KB.

        """
        from sqlalchemy import select

        from ...auth.models import User
        from ...auth.rbac import rbac as _rbac

        user_result = await db.execute(select(User).where(User.id == self._user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return {
                "status": "error",
                "error": {"code": "user_not_found", "message": "Executing user not found."},
            }
        for kb_id in self._knowledge_base_ids:
            if not await _rbac.can_access_knowledge_base(user, kb_id, db):
                return {
                    "status": "error",
                    "error": {
                        "code": "access_denied",
                        "message": f"Access denied to knowledge base '{kb_id}'.",
                    },
                }
        return None

    async def _with_search_service(self, op_name: str, **kwargs: Any) -> dict[str, Any]:
        """Shared session lifecycle and error handling for all KB search methods.

        Checks that knowledge bases are bound, acquires a DB session (background-task
        pattern via ``get_db_session()``), verifies the bound user has RBAC access to
        every KB via ``_check_kb_access``, invokes the named ``KbSearchService``
        method with ``knowledge_base_ids`` prepended to *kwargs*, and always closes
        the session.  ``op_name`` must be one of the keys in ``_SEARCH_OPS``.
        """
        if not self._knowledge_base_ids:
            return {
                "status": "error",
                "error": {
                    "code": "no_knowledge_bases",
                    "message": "No knowledge bases are bound to this execution context.",
                },
            }

        # Deferred import: avoids circular import when kb_capability is loaded
        # through shu.plugins.host.__init__ before kb_search_service is ready.
        from ...services.kb_search_service import KbSearchService

        if not _SEARCH_OPS:
            _SEARCH_OPS.update(
                {
                    "search_chunks": KbSearchService.search_chunks,
                    "search_documents": KbSearchService.search_documents,
                    "get_document": KbSearchService.get_document,
                }
            )

        if op_name not in _SEARCH_OPS:
            return {
                "status": "error",
                "error": {
                    "code": "invalid_op",
                    "message": f"Unknown search operation: '{op_name}'.",
                },
            }

        db = await get_db_session()
        try:
            access_error = await self._check_kb_access(db)
            if access_error:
                return access_error

            svc = KbSearchService(db)
            op = _SEARCH_OPS[op_name]
            return await op(svc, knowledge_base_ids=self._knowledge_base_ids, **kwargs)
        except Exception:
            logger.exception("KB search operation '%s' failed", op_name)
            return {"status": "error", "error": {"code": f"{op_name}_error", "message": "An internal error occurred"}}
        finally:
            try:
                await db.close()
            except Exception:
                pass

    async def search_chunks(
        self,
        field: str,
        operator: str,
        value: str | list[str],
        page: int = 1,
        sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Search document chunks by field, operator, and value across bound knowledge bases."""
        return await self._with_search_service(
            "search_chunks", field=field, operator=operator, value=value, page=page, sort_order=sort_order
        )

    async def search_documents(
        self,
        field: str,
        operator: str,
        value: str | list[str],
        page: int = 1,
        sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Search documents by field, operator, and value across bound knowledge bases."""
        return await self._with_search_service(
            "search_documents", field=field, operator=operator, value=value, page=page, sort_order=sort_order
        )

    async def get_document(self, document_id: str) -> dict[str, Any]:
        """Retrieve a single document by ID from the bound knowledge bases."""
        return await self._with_search_service("get_document", document_id=document_id)
