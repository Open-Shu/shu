"""Knowledge base search service for agentic plugin lookups.

Provides field-based search across document chunks and documents within
knowledge bases bound to a plugin execution context. Supports text,
JSONB array, and JSONB object operators with pagination.
"""

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.document import Document, DocumentChunk
from ..models.knowledge_base import KnowledgeBase

logger = get_logger(__name__)

# Type alias for the common serializer signature used by _field_search.
# Callers always invoke serializer(row, kb_name) with no extra kwargs, so this
# alias is accurate even though _serialize_document_row also accepts an optional
# include_content kwarg (only used by get_document, not via _field_search).
RowSerializer = Callable[[Any, str], dict[str, Any]]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 20

# ---------------------------------------------------------------------------
# Operator maps
# ---------------------------------------------------------------------------

TEXT_OPERATORS: dict[str, Any] = {
    "eq": lambda col, val: col == val,
    "contains": lambda col, val: col.contains(val),
    "icontains": lambda col, val: col.icontains(val),
}


def _jsonb_array_contains(col: Any, val: Any) -> Any:
    if not isinstance(val, list):
        raise TypeError(f"'contains' requires a list value, got {type(val).__name__}")
    return col.op("@>")(cast(json.dumps(val), JSONB))


def _jsonb_object_contains(col: Any, val: Any) -> Any:
    if not isinstance(val, dict):
        raise TypeError(f"'contains' requires a dict value, got {type(val).__name__}")
    return col.op("@>")(cast(json.dumps(val), JSONB))


JSONB_ARRAY_OPERATORS: dict[str, Any] = {
    "contains": _jsonb_array_contains,
    "has_key": lambda col, val: col.op("?")(val),
    "has_any": lambda col, val: col.op("?|")(cast(val, ARRAY(Text))),
}

# NOTE: The path_contains lambda uses late binding — KbSearchService is
# resolved at call time, not at dict-creation time.
JSONB_OBJECT_OPERATORS: dict[str, Any] = {
    "contains": _jsonb_object_contains,
    "has_key": lambda col, val: col.op("?")(val),
    "path_contains": lambda col, val: KbSearchService._build_path_contains(col, val),
}

# ---------------------------------------------------------------------------
# Field validation maps
# ---------------------------------------------------------------------------

CHUNK_SEARCHABLE_FIELDS: dict[str, tuple[str, Any]] = {
    "content": ("text", DocumentChunk.content),
    "summary": ("text", DocumentChunk.summary),
    "keywords": ("jsonb_array", DocumentChunk.keywords),
    "topics": ("jsonb_array", DocumentChunk.topics),
}

DOCUMENT_SEARCHABLE_FIELDS: dict[str, tuple[str, Any]] = {
    "title": ("text", Document.title),
    "content": ("text", Document.content),
    "synopsis": ("text", Document.synopsis),
    "capability_manifest": ("jsonb_object", Document.capability_manifest),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class KbSearchService:
    """Service for field-based knowledge base search.

    All public methods accept a list of *knowledge_base_ids* that the caller
    has resolved from the plugin execution context.  The service never makes
    its own authorization decisions -- it trusts the caller to supply the
    correct set of bound KBs.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialise with an async database session.

        Args:
            db: SQLAlchemy async session.

        """
        self.db = db

    @staticmethod
    def _build_path_contains(col: Any, val: dict[str, Any]) -> Any:
        """Build a JSONB path containment expression.

        Args:
            col: SQLAlchemy column (e.g., ``Document.capability_manifest``).
            val: Dict with ``"path"`` (str) and ``"value"`` (JSON-serializable)
                keys.  Example::

                    {"path": "answers_questions_about", "value": ["newsletter"]}

                Generates: ``col->'answers_questions_about' @> '["newsletter"]'::jsonb``

        """
        path = val["path"]
        value = val["value"]
        return col[path].op("@>")(cast(json.dumps(value), JSONB))

    @staticmethod
    def _get_operator_fn(field_type: str, operator: str) -> Any | None:
        """Return the operator lambda for *field_type* and *operator*, or ``None``."""
        if field_type == "text":
            return TEXT_OPERATORS.get(operator)
        if field_type == "jsonb_array":
            return JSONB_ARRAY_OPERATORS.get(operator)
        if field_type == "jsonb_object":
            return JSONB_OBJECT_OPERATORS.get(operator)
        return None

    @staticmethod
    def _valid_operators_for_type(field_type: str) -> list[str]:
        """Return list of valid operator names for a given field type."""
        if field_type == "text":
            return list(TEXT_OPERATORS.keys())
        if field_type == "jsonb_array":
            return list(JSONB_ARRAY_OPERATORS.keys())
        if field_type == "jsonb_object":
            return list(JSONB_OBJECT_OPERATORS.keys())
        return []

    @staticmethod
    def _serialize_datetime(val: Any) -> str | None:
        """Safely convert a datetime-like value to an ISO-8601 string."""
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(val)

    @staticmethod
    def _serialize_chunk_row(chunk: Any, kb_name: str) -> dict[str, Any]:
        """Serialize a DocumentChunk row, excluding content and embedding."""
        return {
            "document_id": chunk.document_id,
            "knowledge_base_name": kb_name,
            "chunk_index": chunk.chunk_index,
            "summary": chunk.summary,
            "keywords": chunk.keywords,
            "topics": chunk.topics,
            "word_count": chunk.word_count,
            "created_at": KbSearchService._serialize_datetime(chunk.created_at),
        }

    @staticmethod
    def _serialize_document_row(
        doc: Any,
        kb_name: str,
        *,
        include_content: bool = False,
    ) -> dict[str, Any]:
        """Serialize a Document row, excluding content (if applicable) and embedding.

        Args:
            doc: Document model instance.
            kb_name: Resolved knowledge base name.
            include_content: If ``True`` the ``content`` field is included
                (used by ``get_document``).  Search results omit it.

        """
        result: dict[str, Any] = {
            "id": doc.id,
            "knowledge_base_name": kb_name,
            "title": doc.title,
            "source_url": doc.source_url,
            "source_modified_at": KbSearchService._serialize_datetime(doc.source_modified_at),
            "synopsis": doc.synopsis,
            "document_type": doc.document_type,
            "capability_manifest": doc.capability_manifest,
            "relational_context": doc.relational_context,
            "word_count": doc.word_count,
            "created_at": KbSearchService._serialize_datetime(doc.created_at),
        }
        if include_content:
            result["content"] = doc.content
        return result

    @staticmethod
    def _error_dict(code: str, message: str) -> dict[str, Any]:
        """Return a structured error dictionary.

        Args:
            code: Machine-readable error code (e.g. ``"invalid_field"``).
            message: Human-readable error description.

        Returns:
            Dict with ``status`` set to ``"error"`` and nested ``error``
            containing ``code`` and ``message``.

        """
        return {"status": "error", "error": {"code": code, "message": message}}

    async def _field_search(
        self,
        *,
        model: type,
        field_map: dict[str, tuple[str, Any]],
        knowledge_base_ids: list[str],
        field: str,
        operator: str,
        value: str | list[str],
        page: int,
        sort_order: str = "asc",
        serializer: RowSerializer,
        label: str,
    ) -> dict[str, Any]:
        """Run a validated, paginated field search against a model table.

        This is the shared implementation behind ``search_chunks`` and
        ``search_documents``.  It validates the field/operator pair, builds
        the SQLAlchemy query with a KB join, counts total results, paginates,
        serializes rows, and returns the standard result envelope.

        Args:
            model: SQLAlchemy model class (``DocumentChunk`` or ``Document``).
            field_map: Mapping of field name to ``(field_type, column)``.
            knowledge_base_ids: KB IDs resolved from execution context.
            field: Search field name.
            operator: Operator name appropriate for the field type.
            value: The search value (string or list depending on operator).
            page: 1-indexed page number.
            sort_order: ``"asc"`` (default) or ``"desc"``.
            serializer: Callable ``(row, kb_name) -> dict`` for result shaping.
            label: Human-readable label for log messages (e.g. ``"Chunk"``).

        Returns:
            Dict with ``results``, ``total_results``, ``page``, ``page_size``.

        """
        # Validate field
        if field not in field_map:
            return self._error_dict(
                "invalid_field",
                f"Invalid field '{field}'. " f"Valid fields: {', '.join(sorted(field_map.keys()))}",
            )

        field_type, column = field_map[field]

        # Validate operator
        op_fn = self._get_operator_fn(field_type, operator)
        if op_fn is None:
            return self._error_dict(
                "invalid_operator",
                f"Invalid operator '{operator}' for field '{field}' "
                f"(type={field_type}). Valid operators: "
                f"{', '.join(self._valid_operators_for_type(field_type))}",
            )

        # Build filter
        try:
            condition = op_fn(column, value)
        except Exception as exc:
            logger.warning(
                f"Failed to build {label.lower()} search condition",
                extra={"field": field, "operator": operator, "error": str(exc)},
            )
            return self._error_dict(
                "invalid_value",
                f"Failed to build search condition: {exc}",
            )

        # Base query -- join KnowledgeBase for the KB name
        kb_id_col = model.knowledge_base_id
        base_query = (
            select(model, KnowledgeBase.name.label("knowledge_base_name"))
            .join(KnowledgeBase, kb_id_col == KnowledgeBase.id)
            .where(kb_id_col.in_(knowledge_base_ids))
            .where(condition)
        )

        # Total count
        count_query = select(func.count()).select_from(base_query.subquery())
        count_result = await self.db.execute(count_query)
        total_results: int = count_result.scalar() or 0

        # Paginate — order by the searched field in the requested direction
        sanitized_page = max(page, 1)
        order_clause = column.desc() if (sort_order or "").lower() == "desc" else column.asc()
        offset = (sanitized_page - 1) * PAGE_SIZE
        rows_query = base_query.order_by(order_clause).offset(offset).limit(PAGE_SIZE)
        rows_result = await self.db.execute(rows_query)
        rows = rows_result.all()

        results = [serializer(row, kb_name) for row, kb_name in rows]

        logger.info(
            f"{label} search completed",
            extra={
                "field": field,
                "operator": operator,
                "page": sanitized_page,
                "total_results": total_results,
                "returned": len(results),
            },
        )

        return {
            "results": results,
            "total_results": total_results,
            "page": sanitized_page,
            "page_size": PAGE_SIZE,
        }

    async def search_chunks(
        self,
        knowledge_base_ids: list[str],
        field: str,
        operator: str,
        value: str | list[str],
        page: int = 1,
        sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Search document chunks by field, operator, and value.

        Args:
            knowledge_base_ids: KB IDs resolved from execution context.
            field: One of ``content``, ``summary``, ``keywords``, ``topics``.
            operator: Operator name appropriate for the field type.
            value: The search value (string or list depending on operator).
            page: 1-indexed page number (default 1).
            sort_order: ``"asc"`` (default) or ``"desc"``.

        Returns:
            Dict with ``results``, ``total_results``, ``page``, ``page_size``.

        """
        return await self._field_search(
            model=DocumentChunk,
            field_map=CHUNK_SEARCHABLE_FIELDS,
            knowledge_base_ids=knowledge_base_ids,
            field=field,
            operator=operator,
            value=value,
            page=page,
            sort_order=sort_order,
            serializer=self._serialize_chunk_row,
            label="Chunk",
        )

    async def search_documents(
        self,
        knowledge_base_ids: list[str],
        field: str,
        operator: str,
        value: str | list[str],
        page: int = 1,
        sort_order: str = "asc",
    ) -> dict[str, Any]:
        """Search documents by field, operator, and value.

        Args:
            knowledge_base_ids: KB IDs resolved from execution context.
            field: One of ``title``, ``content``, ``synopsis``,
                ``capability_manifest``.
            operator: Operator name appropriate for the field type.
            value: The search value (string or list depending on operator).
            page: 1-indexed page number (default 1).
            sort_order: ``"asc"`` (default) or ``"desc"``.

        Returns:
            Dict with ``results``, ``total_results``, ``page``, ``page_size``.

        """
        return await self._field_search(
            model=Document,
            field_map=DOCUMENT_SEARCHABLE_FIELDS,
            knowledge_base_ids=knowledge_base_ids,
            field=field,
            operator=operator,
            value=value,
            page=page,
            sort_order=sort_order,
            serializer=self._serialize_document_row,
            label="Document",
        )

    async def get_document(
        self,
        knowledge_base_ids: list[str],
        document_id: str,
    ) -> dict[str, Any]:
        """Retrieve a single document by ID.

        The document's ``knowledge_base_id`` must be in the provided list
        of bound KB IDs, otherwise a structured error is returned.

        Args:
            knowledge_base_ids: KB IDs resolved from execution context.
            document_id: The document to retrieve.

        Returns:
            Full document record (including ``content``) or an error dict.

        """
        query = (
            select(Document, KnowledgeBase.name.label("knowledge_base_name"))
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .where(Document.id == document_id)
            .where(Document.knowledge_base_id.in_(knowledge_base_ids))
        )
        result = await self.db.execute(query)
        row = result.one_or_none()

        if row is None:
            return self._error_dict(
                "not_found",
                f"Document '{document_id}' not found.",
            )

        doc, kb_name = row

        logger.info(
            "Document retrieved",
            extra={"document_id": document_id, "knowledge_base_id": doc.knowledge_base_id},
        )

        return self._serialize_document_row(doc, kb_name, include_content=True)
