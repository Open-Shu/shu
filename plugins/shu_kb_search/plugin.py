"""KB Search plugin — delegates to host.kb search capabilities for agentic KB lookup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# Local minimal result shim — avoids importing host internals into the plugin sandbox.
class _Result:
    """Lightweight result wrapper matching the host capability return contract."""

    def __init__(
        self,
        status: str,
        data: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None) -> "_Result":
        """Return a successful result."""
        return cls("success", data or {})

    @classmethod
    def err(
        cls,
        message: str,
        code: str = "tool_error",
        details: dict[str, Any] | None = None,
    ) -> "_Result":
        """Return an error result."""
        return cls("error", data={}, error={"code": code, "message": message, "details": (details or {})})


@dataclass
class _SearchParams:
    """Validated, extracted parameters shared by search_chunks and search_documents."""

    field: str
    operator: str
    value: Any
    page: int
    sort_order: str


class KbSearchPlugin:
    """Plugin that exposes knowledge-base search operations as LLM-callable tools.

    All search operations delegate to ``host.kb.*`` methods which are scoped to the
    knowledge bases bound to the current execution context.  The plugin never holds a
    database session or constructs queries itself.

    Supported operations
    --------------------
    search_chunks
        Field-based search across ``document_chunks``.
        Valid fields: ``content``, ``summary`` (text); ``keywords``, ``topics`` (JSONB array).

    search_documents
        Field-based search across ``documents``.
        Valid fields: ``title``, ``content``, ``synopsis`` (text);
        ``capability_manifest`` (JSONB object).

    get_document
        Retrieve the full content and metadata for a single document by UUID.
    """

    name = "kb_search"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        """Return the JSON schema covering all four search operations.

        ``chat_plugins.py`` deep-copies this schema and pins the ``op`` field for each
        chat-callable op so the LLM receives a schema with only one valid operation
        per tool call.
        """
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": [
                        "search_chunks",
                        "search_documents",
                        "get_document",
                    ],
                    "description": (
                        "Operation to perform. "
                        "'search_chunks' searches document chunks by field/operator/value. "
                        "'search_documents' searches document records by field/operator/value. "
                        "'get_document' retrieves the full content and metadata of a single "
                        "document by its UUID."
                    ),
                    "x-ui": {
                        "help": "Choose a KB search operation.",
                        "enum_labels": {
                            "search_chunks": (
                                "Search knowledge base chunks by keyword, topic, or text. "
                                "Results are ordered ascending by the searched field."
                            ),
                            "search_documents": (
                                "Search knowledge base documents by title, synopsis, or capability manifest. "
                                "Results are ordered ascending by the searched field."
                            ),
                            "get_document": (
                                "Retrieve the full text and all metadata for a specific document by ID."
                            ),
                        },
                        "enum_help": {
                            "search_chunks": (
                                "Search document chunks by field value. "
                                "Text fields (eq/contains/icontains): content, summary. "
                                "JSONB array fields (contains/has_key/has_any): keywords, topics."
                            ),
                            "search_documents": (
                                "Search document records by field value. "
                                "Text fields (eq/contains/icontains): title, content, synopsis. "
                                "JSONB object field (contains/has_key/path_contains): capability_manifest."
                            ),
                            "get_document": (
                                "Retrieve full document content and metadata by document_id UUID. "
                                "Obtain document_id values from search_chunks or search_documents results."
                            ),
                        },
                    },
                },
                "field": {
                    "type": ["string", "null"],
                    "enum": [
                        "content",
                        "summary",
                        "keywords",
                        "topics",
                        "title",
                        "synopsis",
                        "capability_manifest",
                    ],
                    "description": (
                        "Field to search on. "
                        # --- search_chunks fields ---
                        "For search_chunks: "
                        "'content' — raw extracted text of the chunk; "
                        "'summary' — one-line AI-generated description of what the chunk contains; "
                        "'keywords' — specific extractable terms (names, dates, numbers, technical terms) as a JSON array; "
                        "'topics' — broader conceptual categories/themes as a JSON array. "
                        # --- search_documents fields ---
                        "For search_documents: "
                        "'title' — document display name (filename or extracted title); "
                        "'content' — full extracted text of the document; "
                        "'synopsis' — one-paragraph AI-generated summary of the document's essence and purpose; "
                        "'capability_manifest' — AI-generated JSON object with keys: "
                        "'answers_questions_about' (list of topics the document addresses), "
                        "'provides_information_type' (e.g. facts, opinions, decisions, instructions), "
                        "'authority_level' (primary, secondary, or commentary), "
                        "'completeness' (complete, partial, or reference), "
                        "'question_domains' (which of: who, what, when, where, why, how apply). "
                        "Use 'synopsis' or 'capability_manifest' to filter by subject matter; "
                        "use 'title' for filename/title searches."
                    ),
                },
                "operator": {
                    "type": ["string", "null"],
                    "enum": ["eq", "contains", "icontains", "has_key", "has_any", "path_contains"],
                    "description": (
                        "Operator to apply to the field. "
                        "Text fields (content, summary, title, synopsis): "
                        "'eq' (exact match), 'contains' (case-sensitive substring), "
                        "'icontains' (case-insensitive substring). "
                        "JSONB array fields (keywords, topics): "
                        "'contains' (array contains the value), "
                        "'has_key' (array element exists), "
                        "'has_any' (array contains any of a list of values). "
                        "JSONB object field (capability_manifest): "
                        "'contains' (object contains a dict subset), "
                        "'has_key' (top-level key exists), "
                        "'path_contains' (nested path containment — value must be a dict with "
                        "'path' and 'value' keys, e.g. "
                        "{\"path\": \"answers_questions_about\", \"value\": [\"newsletter\"]}). "
                    ),
                },
                "value": {
                    "type": ["string", "array", "object", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "The search value. "
                        "String for 'eq', 'contains', 'icontains', 'has_key'. "
                        "List of strings for 'has_any'. "
                        "Dict with 'path' and 'value' keys for 'path_contains'. "
                    ),
                },
                "page": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "default": 1,
                    "description": (
                        "1-indexed page number for paginated results. "
                        "Each page contains up to 20 results. "
                        "Default is 1. Used for search_chunks and search_documents."
                    ),
                },
                "sort_order": {
                    "type": ["string", "null"],
                    "enum": ["asc", "desc"],
                    "default": "asc",
                    "description": (
                        "Sort direction for results, applied to the searched field. "
                        "Use 'asc' for oldest/lowest/A-Z first (default). "
                        "Use 'desc' for newest/highest/Z-A first — required when fetching "
                        "the latest or most recent N items."
                    ),
                },
                "document_id": {
                    "type": ["string", "null"],
                    "description": (
                        "UUID of the document to retrieve. "
                        "Used only with get_document op. "
                        "Obtain this value from 'document_id' in search_chunks results "
                        "or 'id' in search_documents results."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    def get_schema_for_op(self, op: str) -> dict[str, Any] | None:
        """Return a lean, op-specific JSON schema used by chat_plugins.py for tool descriptors.

        Each op gets only the parameters it actually needs, with field and operator enums
        narrowed to the values that are valid for that op.  ``chat_plugins.py`` will still
        deep-copy this schema and pin the ``op`` const before sending it to the LLM.
        """
        _page = {
            "type": ["integer", "null"],
            "minimum": 1,
            "default": 1,
            "description": "1-indexed page number. Each page returns up to 20 results.",
        }
        _sort_order = {
            "type": ["string", "null"],
            "enum": ["asc", "desc"],
            "default": "asc",
            "description": (
                "Sort direction applied to the searched field. "
                "Use 'asc' for A-Z / oldest first (default). "
                "Use 'desc' for Z-A / newest first — use this when asked for the latest or most recent N items."
            ),
        }

        if op == "search_chunks":
            return {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": ["content", "summary", "keywords", "topics"],
                        "description": (
                            "'content' — raw extracted text of the chunk. "
                            "'summary' — one-line AI-generated description of the chunk. "
                            "'keywords' — specific extractable terms (names, dates, numbers, technical terms) as a JSON array. "
                            "'topics' — broader conceptual categories/themes as a JSON array."
                        ),
                    },
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "icontains", "has_key", "has_any"],
                        "description": (
                            "For text fields (content, summary): "
                            "'eq' exact match, 'icontains' case-insensitive substring. "
                            "For JSON array fields (keywords, topics): "
                            "'has_key' element exists in array, "
                            "'has_any' array contains any of a list of strings (value must be a list), "
                            "'contains' array contains the exact value."
                        ),
                    },
                    "value": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": (
                            "String for 'eq', 'icontains', 'has_key'. "
                            "List of strings for 'has_any'."
                        ),
                    },
                    "page": _page,
                    "sort_order": _sort_order,
                },
                "required": ["field", "operator", "value"],
                "additionalProperties": False,
            }

        if op == "search_documents":
            return {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": ["title", "content", "synopsis", "capability_manifest"],
                        "description": (
                            "'title' — document display name (filename or extracted title). "
                            "'content' — full extracted text of the document. "
                            "'synopsis' — one-paragraph AI-generated summary of the document's essence and purpose. "
                            "'capability_manifest' — AI-generated JSON object describing the document's subject matter. "
                            "Keys: 'answers_questions_about' (list of topics), "
                            "'provides_information_type' (facts/opinions/decisions/instructions), "
                            "'authority_level' (primary/secondary/commentary), "
                            "'completeness' (complete/partial/reference), "
                            "'question_domains' (who/what/when/where/why/how). "
                            "Use 'synopsis' or 'capability_manifest' to filter by subject; 'title' for name searches."
                        ),
                    },
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "icontains", "has_key", "path_contains"],
                        "description": (
                            "For text fields (title, content, synopsis): "
                            "'eq' exact match, 'icontains' case-insensitive substring. "
                            "For 'capability_manifest' (JSON object): "
                            "'has_key' top-level key exists, "
                            "'contains' object contains a dict subset, "
                            "'path_contains' nested path containment — value must be a dict with 'path' and 'value' keys "
                            "(e.g. {\"path\": \"answers_questions_about\", \"value\": [\"newsletter\"]})."
                        ),
                    },
                    "value": {
                        "type": "string",
                        "description": (
                            "The search value. "
                            "Always a plain string for text fields (title, content, synopsis) with any operator. "
                            "For 'capability_manifest' with 'contains': a JSON-encoded dict string representing the subset to match. "
                            "For 'capability_manifest' with 'path_contains': a JSON-encoded dict string with 'path' and 'value' keys, "
                            "e.g. '{\"path\": \"answers_questions_about\", \"value\": [\"newsletter\"]}'. "
                            "For 'capability_manifest' with 'has_key': the top-level key name as a plain string."
                        ),
                    },
                    "page": _page,
                    "sort_order": _sort_order,
                },
                "required": ["field", "operator", "value"],
                "additionalProperties": False,
            }

        if op == "get_document":
            return {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": (
                            "UUID of the document to retrieve. "
                            "Obtain from 'document_id' in search_chunks results "
                            "or 'id' in search_documents results."
                        ),
                    },
                },
                "required": ["document_id"],
                "additionalProperties": False,
            }

        return None

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> _Result:
        """Dispatch to the appropriate host.kb method based on the 'op' parameter.

        Args:
            params: Plugin invocation parameters.
            context: Plugin execution context (unused directly).
            host: Host capability proxy.

        Returns:
            A ``_Result`` with the search results or a structured error.

        """
        if not hasattr(host, "kb"):
            return _Result.err(
                "KB capability not available. The plugin must declare 'kb' in its capabilities.",
                code="kb_capability_unavailable",
            )

        op = (params.get("op") or "").strip()

        if op == "search_chunks":
            return await self._search_chunks(host, params)
        if op == "search_documents":
            return await self._search_documents(host, params)
        if op == "get_document":
            return await self._get_document(host, params)

        return _Result.err(f"Unsupported op: '{op}'", code="invalid_op")

    # ------------------------------------------------------------------
    # Private op handlers
    # ------------------------------------------------------------------

    def _parse_search_params(
        self, params: dict[str, Any], op_name: str, valid_fields: str
    ) -> "_SearchParams | _Result":
        """Extract and validate the common search parameters shared by search_chunks and search_documents.

        Args:
            params: Raw plugin invocation parameters.
            op_name: Operation name used in error messages.
            valid_fields: Human-readable list of valid field names for error messages.

        Returns:
            ``_SearchParams`` on success, or a ``_Result`` error if validation fails.

        """
        field = params.get("field")
        operator = params.get("operator")
        value = params.get("value")
        try:
            page = int(params.get("page") or 1)
        except (ValueError, TypeError):
            page = 1
        sort_order = params.get("sort_order") or "asc"
        if sort_order not in ("asc", "desc"):
            sort_order = "asc"

        if not field:
            return _Result.err(
                f"field is required for {op_name}. Valid fields: {valid_fields}.",
                code="missing_parameter",
            )
        if not operator:
            return _Result.err(f"operator is required for {op_name}.", code="missing_parameter")
        if value is None:
            return _Result.err(f"value is required for {op_name}.", code="missing_parameter")

        return _SearchParams(field=field, operator=operator, value=value, page=page, sort_order=sort_order)

    async def _search_chunks(self, host: Any, params: dict[str, Any]) -> _Result:
        """Handle the search_chunks operation.

        Args:
            host: Host capability proxy.
            params: Plugin invocation parameters.

        Returns:
            Search results or a structured error.

        """
        parsed = self._parse_search_params(params, "search_chunks", "content, summary, keywords, topics")
        if isinstance(parsed, _Result):
            return parsed

        result = await host.kb.search_chunks(
            field=parsed.field, operator=parsed.operator, value=parsed.value,
            page=parsed.page, sort_order=parsed.sort_order,
        )
        return self._wrap_host_result(result)

    async def _search_documents(self, host: Any, params: dict[str, Any]) -> _Result:
        """Handle the search_documents operation.

        Args:
            host: Host capability proxy.
            params: Plugin invocation parameters.

        Returns:
            Search results or a structured error.

        """
        parsed = self._parse_search_params(
            params, "search_documents", "title, content, synopsis, capability_manifest"
        )
        if isinstance(parsed, _Result):
            return parsed

        field, operator, value = parsed.field, parsed.operator, parsed.value

        # value must be a plain string for text fields; dicts are only valid for capability_manifest.
        if field != "capability_manifest" and isinstance(value, (dict, list)):
            return _Result.err(
                f"value must be a plain string when field is '{field}'. "
                "Dicts and lists are not valid for this field.",
                code="invalid_parameter",
            )

        # For capability_manifest operators that expect a dict, accept a JSON-encoded string.
        if field == "capability_manifest" and operator in ("contains", "path_contains") and isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                pass  # leave as string; backend will surface the error

        result = await host.kb.search_documents(
            field=field, operator=operator, value=value,
            page=parsed.page, sort_order=parsed.sort_order,
        )
        return self._wrap_host_result(result)

    async def _get_document(self, host: Any, params: dict[str, Any]) -> _Result:
        """Handle the get_document operation.

        Args:
            host: Host capability proxy.
            params: Plugin invocation parameters.

        Returns:
            Full document record or a structured error.

        """
        document_id = params.get("document_id")
        if not document_id:
            return _Result.err(
                "document_id is required for get_document.",
                code="missing_parameter",
            )

        result = await host.kb.get_document(document_id=document_id)
        res = self._wrap_host_result(result)
        return res

    @staticmethod
    def _wrap_host_result(result: dict[str, Any] | Any) -> _Result:
        """Convert a host.kb return value into a _Result.

        Host KB methods return either a plain result dict (success) or a dict
        with ``status == "error"`` (failure).  This helper normalises both cases.

        Args:
            result: The raw value returned by a host.kb method.

        Returns:
            ``_Result.ok(result)`` on success or ``_Result.err(...)`` on error.

        """
        if result is None:
            return _Result.err("Not found", code="not_found")
        if isinstance(result, dict) and result.get("status") == "error":
            err = result.get("error") or {}
            return _Result.err(
                err.get("message", "Unknown error"),
                code=err.get("code", "host_error"),
            )
        return _Result.ok(result if isinstance(result, dict) else {"result": result})
