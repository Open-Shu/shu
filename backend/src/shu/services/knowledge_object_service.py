"""
Knowledge Object (KO) write adapter over existing Knowledge Base (KB).

Implementation Status: Partial (write path implemented)
Limitations/Known Issues:
- Document.source_type is a free-form label; 'plugin:'-prefixed values are reserved for plugin-written documents. There is no central source_types registry; callers must choose consistent labels if they depend on aggregation.
- get_document_by_source_id matches only by (kb_id, source_id) and ignores the source_type label
Security Vulnerabilities:
- Ensure call sites handle redaction/PII before passing KO.content

See TASK-112 for the plan and acceptance criteria.
"""
from __future__ import annotations

from typing import Optional, Dict, Any
import hashlib
from datetime import datetime, date, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..knowledge.ko import KnowledgeObject, deterministic_ko_id
from ..services.document_service import DocumentService
from ..services.knowledge_base_service import KnowledgeBaseService


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Coerce various timestamp representations to timezone-aware datetime.
    Accepts:
    - datetime: returned as-is (add UTC tzinfo if naive)
    - date: converted to datetime at midnight UTC
    - str: ISO 8601; supports trailing 'Z'
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        try:
            # Support RFC3339/ISO8601 with 'Z'
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None



def _choose_source_type(ko: KnowledgeObject) -> str:
    """Choose the Document.source_type label for a KO.

    Rules:
    - If ko.source contains an explicit 'source_type', use that verbatim (e.g., 'filesystem').
    - Else if ko.source declares a 'plugin' name, return 'plugin:<plugin_name>'.
    - Else, fall back to 'plugin:generic' as a catch-all label.
    """
    src = ko.source if isinstance(ko.source, dict) else {}
    st = src.get("source_type")
    if st:
        return str(st)
    plugin_name = src.get("plugin")
    if plugin_name:
        return f"plugin:{plugin_name}"
    return "plugin:generic"


def _choose_file_type(ko: KnowledgeObject) -> str:
    # Map KO type to file_type; default to 'txt'
    t = (ko.type or "").lower()
    if t in {"email", "eml"}:
        return "email"
    if t in {"pdf", "docx", "md", "txt", "html"}:
        return t
    return "txt"


async def upsert_knowledge_object(db: AsyncSession, knowledge_base_id: str, ko: KnowledgeObject) -> str:
    """Upsert a KnowledgeObject into the KB-backed storage and trigger indexing.

    - Creates or updates a Document row using KO fields
    - Generates chunks+embeddings and replaces existing chunks
    - Marks document processed with stats
    Returns the KO ID (deterministic if not provided).
    """
    # Compute deterministic KO id if not provided
    if not ko.id:
        namespace = f"{ko.source.get('plugin')}:{ko.source.get('account')}" if isinstance(ko.source, dict) else "ko"
        ko.id = deterministic_ko_id(namespace, ko.external_id)

    # Prepare document attributes
    source_type = _choose_source_type(ko)
    file_type = _choose_file_type(ko)
    title = ko.title or ko.external_id
    content = ko.content or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    document_service = DocumentService(db)

    # Try to find existing document by (kb_id, source_id)
    existing = await document_service.get_document_by_source_id(knowledge_base_id, ko.external_id)

    # Create or update Document metadata/content
    if existing is None:
        from ..schemas.document import DocumentCreate
        doc_create = DocumentCreate(
            knowledge_base_id=knowledge_base_id,
            title=title,
            file_type=file_type,
            source_type=source_type,
            source_id=ko.external_id,
            source_url=(ko.attributes or {}).get("source_url"),
            file_size=len(content) if content else None,
            content=content,
            content_hash=content_hash,
            source_hash=(ko.attributes or {}).get("source_hash"),
            source_modified_at=_coerce_datetime((ko.attributes or {}).get("modified_at")),
            extraction_method="host_plugin",
            extraction_engine=(ko.source or {}).get("plugin") if isinstance(ko.source, dict) else None,
            extraction_confidence=None,
            extraction_duration=None,
            extraction_metadata=(ko.attributes or {}).get("extraction_metadata"),
        )
        # DocumentService.create_document treats source_type as a label; there is no SourceType registry enforcement.
        created = await document_service.create_document(doc_create)
        # Re-fetch the ORM object to get ID
        from sqlalchemy import select
        from ..models.document import Document
        res = await db.execute(select(Document).where(Document.id == created.id))
        document = res.scalar_one()
    else:
        # Update the ORM object in-place
        document = existing
        document.title = title
        document.file_type = file_type
        document.content = content
        document.content_hash = content_hash
        document.file_size = len(content) if content else None
        document.extraction_method = "host_plugin"
        document.extraction_engine = (ko.source or {}).get("plugin") if isinstance(ko.source, dict) else None
        # Optional attributes
        if ko.attributes:
            document.source_url = ko.attributes.get("source_url") or document.source_url
            document.source_hash = ko.attributes.get("source_hash") or document.source_hash
            coerced = _coerce_datetime(ko.attributes.get("modified_at"))
            document.source_modified_at = coerced if coerced is not None else document.source_modified_at
            document.extraction_metadata = ko.attributes.get("extraction_metadata") or document.extraction_metadata
        db.add(document)
        await db.commit()

    await document_service.process_and_update_chunks(
        knowledge_base_id=knowledge_base_id,
        document=document,
        title=title,
        content=content,
    )

    return ko.id



async def delete_ko_by_external_id(db: AsyncSession, *, kb_id: str, external_id: str, plugin_name: str) -> Dict[str, Any]:
    """Delete a single KO (Document) by (kb_id, source_type=plugin:<name>, source_id).
    Returns {deleted: bool, ko_id?: str}.
    """
    from sqlalchemy import select, and_, delete as sqla_delete
    from ..models.document import Document
    # Find matching document
    res = await db.execute(
        select(Document).where(
            and_(
                Document.knowledge_base_id == kb_id,
                Document.source_id == external_id,
                Document.source_type == f"plugin:{plugin_name}",
            )
        )
    )
    doc = res.scalars().first()
    if not doc:
        return {"deleted": False}
    doc_id = str(doc.id)
    await db.delete(doc)
    await db.commit()
    return {"deleted": True, "ko_id": doc_id}


async def delete_kos_by_external_ids(db: AsyncSession, *, kb_id: str, external_ids: list[str], plugin_name: str, chunk_size: int = 500) -> Dict[str, Any]:
    """Delete multiple KOs by external_ids under a plugin source_type in a KB.
    Returns {deleted_count, failed}.
    """
    from sqlalchemy import select, and_, delete as sqla_delete
    from ..models.document import Document

    ids = list(external_ids or [])
    if not ids:
        return {"deleted_count": 0, "failed": []}

    deleted_total = 0
    failed: list[str] = []
    # Chunk to avoid parameter bloat
    for i in range(0, len(ids), max(1, int(chunk_size))):
        chunk = ids[i:i + max(1, int(chunk_size))]
        # Select ids first for logging/robustness
        to_del_rows = await db.execute(
            select(Document.id).where(
                and_(
                    Document.knowledge_base_id == kb_id,
                    Document.source_id.in_(chunk),
                    Document.source_type == f"plugin:{plugin_name}",
                )
            )
        )
        doc_ids = [str(r[0]) for r in to_del_rows.all()]
        if not doc_ids:
            continue
        # Bulk delete by ids
        await db.execute(sqla_delete(Document).where(Document.id.in_(doc_ids)))
        await db.commit()
        deleted_total += len(doc_ids)
        # Any ids not present considered failed (not found)
        remaining = set(chunk) - set([])  # we don't map back source_id here cheaply
        # We skip populating failed for performance; could enhance if required
    return {"deleted_count": deleted_total, "failed": failed}
