"""OpenAPI specification fetching, parsing, and operation extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from shu.core.logging import get_logger

logger = get_logger(__name__)

MAX_SPEC_SIZE = 50 * 1024 * 1024  # 50 MB
FETCH_TIMEOUT = 30.0
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class ParsedOperation:
    """A single extracted HTTP operation from an OpenAPI spec."""

    name: str
    description: str
    method: str
    path: str
    input_schema: dict[str, Any]
    path_params: list[str]
    query_params: list[str]
    has_body: bool
    content_type: str | None


@dataclass
class OpenApiParseResult:
    """Result of parsing an OpenAPI specification."""

    discovered_tools: list[dict[str, Any]]
    base_url: str | None
    errors: list[str] = field(default_factory=list)


def _validate_url(url: str) -> None:
    """Validate that the URL uses HTTPS, or HTTP only for localhost."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in _LOCALHOST_HOSTS:
        return
    raise ValueError(
        f"URL must use HTTPS (HTTP allowed only for localhost): {url}"
    )


async def fetch_spec(
    url: str, http_client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Fetch and parse an OpenAPI specification from a URL.

    Streams the response with a 50 MB size limit. Parses as JSON or YAML
    based on content-type, falling back to trying JSON first then YAML.

    Raises ``ValueError`` on URL validation failure, ``httpx.HTTPError`` on
    fetch failure, and ``ValueError`` on parse failure or size exceeded.
    """
    _validate_url(url)

    own_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(
            url, follow_redirects=True, timeout=FETCH_TIMEOUT
        )
        response.raise_for_status()

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_SPEC_SIZE:
            raise ValueError(
                f"Spec exceeds {MAX_SPEC_SIZE} byte size limit "
                f"(content-length: {content_length})"
            )

        body = response.content
        if len(body) > MAX_SPEC_SIZE:
            raise ValueError(
                f"Spec exceeds {MAX_SPEC_SIZE} byte size limit "
                f"(actual: {len(body)})"
            )

        text = body.decode("utf-8")
        content_type = response.headers.get("content-type", "")

        if "yaml" in content_type or "yml" in content_type:
            return _parse_yaml(text)
        return _parse_json_or_yaml(text)
    finally:
        if own_client:
            await client.aclose()


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        result = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML spec: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("Spec root must be a JSON object / YAML mapping")
    return result


def _parse_json_or_yaml(text: str) -> dict[str, Any]:
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("Spec root must be a JSON object")
        return result
    except (json.JSONDecodeError, ValueError):
        return _parse_yaml(text)


def resolve_refs(spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve internal ``$ref`` pointers within an OpenAPI spec.

    External refs (http:// or not starting with ``#``) are logged as warnings
    and skipped. Circular refs are detected and left as-is.
    Returns a new dict with refs inlined.
    """
    warnings: list[str] = []

    def _resolve(node: Any, visited: frozenset[str]) -> Any:
        if isinstance(node, list):
            return [_resolve(item, visited) for item in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#"):
                warnings.append(f"Skipping external $ref: {ref}")
                logger.warning("resolve_refs.external_ref_skipped ref=%s", ref)
                return node
            if ref in visited:
                logger.debug("resolve_refs.circular_ref ref=%s", ref)
                return {"$ref": ref}
            target = _follow_pointer(spec, ref)
            if target is None:
                warnings.append(f"Unresolvable $ref: {ref}")
                logger.warning("resolve_refs.unresolvable_ref ref=%s", ref)
                return node
            return _resolve(target, visited | {ref})

        return {k: _resolve(v, visited) for k, v in node.items()}

    resolved = _resolve(spec, frozenset())
    if warnings:
        resolved.setdefault("x-resolve-warnings", warnings)
    return resolved


def _follow_pointer(spec: dict[str, Any], ref: str) -> Any | None:
    """Follow a JSON Pointer like ``#/components/schemas/Foo``."""
    parts = ref.lstrip("#/").split("/")
    current: Any = spec
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def extract_base_url(spec: dict[str, Any]) -> str | None:
    """Extract the base URL from the first entry in ``servers``."""
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        url = servers[0].get("url") if isinstance(servers[0], dict) else None
        return url if isinstance(url, str) else None
    return None


def _sanitize_tool_name(name: str) -> str:
    """Sanitize an operationId to match the LLM tool name pattern ``[a-zA-Z0-9_-]{1,128}``."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_")
    return sanitized[:128] if sanitized else "unnamed"


def _slugify_path(method: str, path: str) -> str:
    """Create a slug from method and path for use as an operation name."""
    slug = re.sub(r"[/{}\-.]", "_", path)
    slug = re.sub(r"_+", "_", slug)
    slug = slug.strip("_")
    return f"{method}_{slug}"


def extract_operations(spec: dict[str, Any]) -> list[ParsedOperation]:
    """Extract all HTTP operations from an OpenAPI spec's ``paths``."""
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return []

    operations: list[ParsedOperation] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operations.append(
                _parse_single_operation(method, path, operation, path_item, spec)
            )
    return operations


def _parse_single_operation(
    method: str,
    path: str,
    operation: dict[str, Any],
    path_item: dict[str, Any],
    spec: dict[str, Any],
) -> ParsedOperation:
    """Parse a single operation into a ``ParsedOperation``."""
    op_id = operation.get("operationId")
    name = _sanitize_tool_name(op_id) if op_id else _slugify_path(method, path)

    description = (
        operation.get("summary")
        or operation.get("description")
        or name
    )

    path_params = re.findall(r"\{(\w+)\}", path)

    all_params = _merge_parameters(path_item, operation)
    query_params = [
        p["name"]
        for p in all_params
        if isinstance(p, dict) and p.get("in") == "query" and "name" in p
    ]

    request_body = operation.get("requestBody")
    has_body = (
        isinstance(request_body, dict)
        and isinstance(request_body.get("content"), dict)
        and len(request_body["content"]) > 0
    )

    content_type: str | None = None
    if has_body:
        content_type = next(iter(request_body["content"]))

    input_schema = _build_input_schema(operation, path_params, spec)

    return ParsedOperation(
        name=name,
        description=description,
        method=method.upper(),
        path=path,
        input_schema=input_schema,
        path_params=path_params,
        query_params=query_params,
        has_body=has_body,
        content_type=content_type,
    )


def _merge_parameters(
    path_item: dict[str, Any], operation: dict[str, Any]
) -> list[dict[str, Any]]:
    """Merge path-level and operation-level parameters (operation wins)."""
    path_params = path_item.get("parameters") or []
    op_params = operation.get("parameters") or []
    if not path_params:
        return op_params
    if not op_params:
        return path_params

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for p in path_params:
        if isinstance(p, dict) and "name" in p:
            by_key[(p["name"], p.get("in", ""))] = p
    for p in op_params:
        if isinstance(p, dict) and "name" in p:
            by_key[(p["name"], p.get("in", ""))] = p
    return list(by_key.values())


def _build_input_schema(
    operation: dict[str, Any],
    path_params: list[str],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Build a flat JSON Schema combining path, query, and body parameters.

    Handles name collisions by prefixing: path params get ``path_`` prefix
    when colliding with query/body, query params get ``query_`` prefix when
    colliding with body.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    body_prop_names = _collect_body_property_names(operation)
    query_prop_names = _collect_query_param_names(operation)

    for param_name in path_params:
        key = param_name
        if param_name in query_prop_names or param_name in body_prop_names:
            key = f"path_{param_name}"
        properties[key] = {"type": "string"}
        required.append(key)

    all_params = operation.get("parameters") or []
    for param in all_params:
        if not isinstance(param, dict) or param.get("in") != "query":
            continue
        param_name = param.get("name", "")
        if not param_name:
            continue
        schema = param.get("schema", {"type": "string"})
        key = param_name
        if param_name in body_prop_names:
            key = f"query_{param_name}"
        properties[key] = _simplify_schema(schema)

    _merge_body_properties(operation, properties, required)

    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _collect_body_property_names(operation: dict[str, Any]) -> set[str]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return set()
    content = request_body.get("content")
    if not isinstance(content, dict) or not content:
        return set()
    first_media = next(iter(content.values()))
    if not isinstance(first_media, dict):
        return set()
    schema = first_media.get("schema", {})
    if not isinstance(schema, dict):
        return set()
    return set(schema.get("properties", {}).keys())


def _collect_query_param_names(operation: dict[str, Any]) -> set[str]:
    params = operation.get("parameters") or []
    return {
        p["name"]
        for p in params
        if isinstance(p, dict) and p.get("in") == "query" and "name" in p
    }


def _merge_body_properties(
    operation: dict[str, Any],
    properties: dict[str, Any],
    required: list[str],
) -> None:
    """Merge request body schema properties into the flat input schema."""
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return
    content = request_body.get("content")
    if not isinstance(content, dict) or not content:
        return
    first_media = next(iter(content.values()))
    if not isinstance(first_media, dict):
        return
    schema = first_media.get("schema", {})
    if not isinstance(schema, dict):
        return

    for prop_name, prop_schema in schema.get("properties", {}).items():
        if prop_name not in properties:
            properties[prop_name] = _simplify_schema(prop_schema)

    for req_name in schema.get("required", []):
        if req_name in properties and req_name not in required:
            required.append(req_name)


def _simplify_schema(schema: Any) -> dict[str, Any]:
    """Return a simplified copy of a schema node suitable for tool input."""
    if not isinstance(schema, dict):
        return {"type": "string"}
    result: dict[str, Any] = {}
    for key in ("type", "description", "enum", "default", "format", "items"):
        if key in schema:
            result[key] = schema[key]
    if not result:
        return {"type": "string"}
    return result


def _operation_to_discovered_tool(op: ParsedOperation) -> dict[str, Any]:
    """Convert a ``ParsedOperation`` to a discovered tool dict."""
    return {
        "name": op.name,
        "description": op.description,
        "inputSchema": op.input_schema,
        "method": op.method,
        "path": op.path,
        "path_params": op.path_params,
        "query_params": op.query_params,
        "has_body": op.has_body,
        "content_type": op.content_type,
    }


async def fetch_and_parse(
    url: str, http_client: httpx.AsyncClient | None = None
) -> OpenApiParseResult:
    """Fetch an OpenAPI spec and extract all operations as discovered tools.

    Orchestrates fetching, ref resolution, operation extraction, and
    conversion to tool definitions. Non-fatal warnings are collected in
    the ``errors`` field of the result.
    """
    errors: list[str] = []

    raw_spec = await fetch_spec(url, http_client=http_client)
    resolved = resolve_refs(raw_spec)

    resolve_warnings = resolved.pop("x-resolve-warnings", [])
    if resolve_warnings:
        errors.extend(resolve_warnings)

    base_url = extract_base_url(resolved)
    operations = extract_operations(resolved)

    discovered_tools = [
        _operation_to_discovered_tool(op) for op in operations
    ]

    return OpenApiParseResult(
        discovered_tools=discovered_tools,
        base_url=base_url,
        errors=errors,
    )
