"""Unit tests for openapi_parser module."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest

os.environ.setdefault("SHU_DATABASE_URL", "test_db_url")
os.environ.setdefault("JWT_SECRET_KEY", "test_secret")

from shu.plugins.openapi_parser import (
    OpenApiParseResult,
    ParsedOperation,
    extract_base_url,
    extract_operations,
    fetch_and_parse,
    fetch_spec,
    resolve_refs,
)


def _minimal_spec(
    paths: dict[str, Any] | None = None,
    servers: list[dict[str, Any]] | None = None,
    components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenAPI 3.0 spec dict."""
    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
    }
    if servers is not None:
        spec["servers"] = servers
    if paths is not None:
        spec["paths"] = paths
    if components is not None:
        spec["components"] = components
    return spec


class TestResolveRefs:
    def test_resolves_simple_ref(self) -> None:
        spec = _minimal_spec(
            components={"schemas": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}}}
        )
        spec["paths"] = {"/pets": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}}}}}
        result = resolve_refs(spec)
        schema = result["paths"]["/pets"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"type": "object", "properties": {"name": {"type": "string"}}}

    def test_resolves_nested_ref_chain(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "components": {
                "schemas": {
                    "A": {"$ref": "#/components/schemas/B"},
                    "B": {"type": "string"},
                }
            },
            "root": {"$ref": "#/components/schemas/A"},
        }
        result = resolve_refs(spec)
        assert result["root"] == {"type": "string"}

    def test_circular_ref_returns_ref_as_is(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "child": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                }
            },
            "root": {"$ref": "#/components/schemas/Node"},
        }
        result = resolve_refs(spec)
        child = result["root"]["properties"]["child"]
        assert child == {"$ref": "#/components/schemas/Node"}

    def test_external_ref_skipped_with_warning(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "ext": {"$ref": "http://example.com/schema.json"},
        }
        result = resolve_refs(spec)
        assert result["ext"] == {"$ref": "http://example.com/schema.json"}
        assert "Skipping external $ref: http://example.com/schema.json" in result["x-resolve-warnings"]

    def test_non_hash_ref_treated_as_external(self) -> None:
        spec = {"openapi": "3.0.3", "ext": {"$ref": "other.yaml#/Foo"}}
        result = resolve_refs(spec)
        assert result["ext"] == {"$ref": "other.yaml#/Foo"}
        assert len(result["x-resolve-warnings"]) == 1

    def test_unresolvable_ref_kept_with_warning(self) -> None:
        spec = {"openapi": "3.0.3", "bad": {"$ref": "#/does/not/exist"}}
        result = resolve_refs(spec)
        assert result["bad"] == {"$ref": "#/does/not/exist"}
        assert "Unresolvable $ref: #/does/not/exist" in result["x-resolve-warnings"]

    def test_no_warnings_when_all_resolved(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "components": {"schemas": {"X": {"type": "integer"}}},
            "root": {"$ref": "#/components/schemas/X"},
        }
        result = resolve_refs(spec)
        assert "x-resolve-warnings" not in result

    def test_resolves_refs_inside_lists(self) -> None:
        spec = {
            "openapi": "3.0.3",
            "components": {"schemas": {"S": {"type": "boolean"}}},
            "items": [{"$ref": "#/components/schemas/S"}, "plain"],
        }
        result = resolve_refs(spec)
        assert result["items"] == [{"type": "boolean"}, "plain"]


class TestExtractOperations:
    def test_extracts_single_get(self) -> None:
        spec = _minimal_spec(paths={
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "summary": "List all pets",
                    "parameters": [],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        })
        ops = extract_operations(spec)
        assert len(ops) == 1
        assert ops[0].name == "listPets"
        assert ops[0].method == "GET"
        assert ops[0].path == "/pets"
        assert ops[0].description == "List all pets"

    def test_extracts_multiple_methods(self) -> None:
        spec = _minimal_spec(paths={
            "/items": {
                "get": {"operationId": "getItems", "responses": {}},
                "post": {"operationId": "createItem", "responses": {}},
                "delete": {"operationId": "deleteItem", "responses": {}},
            }
        })
        ops = extract_operations(spec)
        names = {op.name for op in ops}
        assert names == {"getItems", "createItem", "deleteItem"}

    def test_fallback_name_when_no_operation_id(self) -> None:
        spec = _minimal_spec(paths={
            "/users/{id}/posts": {
                "get": {"responses": {}}
            }
        })
        ops = extract_operations(spec)
        assert len(ops) == 1
        assert ops[0].name == "get_users_id_posts"

    def test_path_params_extracted(self) -> None:
        spec = _minimal_spec(paths={
            "/orgs/{org}/repos/{repo}": {
                "get": {"operationId": "getRepo", "responses": {}}
            }
        })
        ops = extract_operations(spec)
        assert ops[0].path_params == ["org", "repo"]

    def test_query_params_extracted(self) -> None:
        spec = _minimal_spec(paths={
            "/search": {
                "get": {
                    "operationId": "search",
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "page", "in": "query", "schema": {"type": "integer"}},
                    ],
                    "responses": {},
                }
            }
        })
        ops = extract_operations(spec)
        assert ops[0].query_params == ["q", "page"]

    def test_request_body_detected(self) -> None:
        spec = _minimal_spec(paths={
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        }
                    },
                    "responses": {},
                }
            }
        })
        ops = extract_operations(spec)
        assert ops[0].has_body is True
        assert ops[0].content_type == "application/json"

    def test_no_body_when_empty_content(self) -> None:
        spec = _minimal_spec(paths={
            "/ping": {
                "post": {
                    "operationId": "ping",
                    "requestBody": {"content": {}},
                    "responses": {},
                }
            }
        })
        ops = extract_operations(spec)
        assert ops[0].has_body is False
        assert ops[0].content_type is None

    def test_empty_paths_returns_empty(self) -> None:
        spec = _minimal_spec(paths={})
        assert extract_operations(spec) == []

    def test_missing_paths_returns_empty(self) -> None:
        spec = _minimal_spec()
        assert extract_operations(spec) == []

    def test_description_falls_back_to_name(self) -> None:
        spec = _minimal_spec(paths={
            "/x": {"get": {"operationId": "doX", "responses": {}}}
        })
        ops = extract_operations(spec)
        assert ops[0].description == "doX"

    def test_description_prefers_summary_over_description(self) -> None:
        spec = _minimal_spec(paths={
            "/x": {
                "get": {
                    "operationId": "doX",
                    "summary": "Short summary",
                    "description": "Long description",
                    "responses": {},
                }
            }
        })
        ops = extract_operations(spec)
        assert ops[0].description == "Short summary"

    def test_path_level_params_merged(self) -> None:
        spec = _minimal_spec(paths={
            "/items": {
                "parameters": [
                    {"name": "shared", "in": "query", "schema": {"type": "string"}},
                ],
                "get": {
                    "operationId": "listItems",
                    "responses": {},
                },
            }
        })
        ops = extract_operations(spec)
        assert ops[0].query_params == ["shared"]


class TestBuildInputSchema:
    def _get_schema(self, spec: dict[str, Any]) -> dict[str, Any]:
        ops = extract_operations(spec)
        assert len(ops) == 1
        return ops[0].input_schema

    def test_path_params_required(self) -> None:
        spec = _minimal_spec(paths={
            "/pets/{petId}": {"get": {"operationId": "getPet", "responses": {}}}
        })
        schema = self._get_schema(spec)
        assert "petId" in schema["properties"]
        assert "petId" in schema["required"]

    def test_query_params_optional(self) -> None:
        spec = _minimal_spec(paths={
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {},
                }
            }
        })
        schema = self._get_schema(spec)
        assert "limit" in schema["properties"]
        assert "limit" not in schema.get("required", [])

    def test_body_properties_merged(self) -> None:
        spec = _minimal_spec(paths={
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                    "responses": {},
                }
            }
        })
        schema = self._get_schema(spec)
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert "name" in schema["required"]

    def test_path_param_collision_with_body_gets_prefix(self) -> None:
        spec = _minimal_spec(paths={
            "/items/{name}": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {},
                }
            }
        })
        schema = self._get_schema(spec)
        assert "path_name" in schema["properties"]
        assert "path_name" in schema["required"]
        assert "name" in schema["properties"]

    def test_path_param_collision_with_query_gets_prefix(self) -> None:
        spec = _minimal_spec(paths={
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "query", "schema": {"type": "string"}}],
                    "responses": {},
                }
            }
        })
        schema = self._get_schema(spec)
        assert "path_id" in schema["properties"]
        assert "path_id" in schema["required"]
        assert "id" in schema["properties"]

    def test_query_param_collision_with_body_gets_prefix(self) -> None:
        spec = _minimal_spec(paths={
            "/search": {
                "post": {
                    "operationId": "search",
                    "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"q": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {},
                }
            }
        })
        schema = self._get_schema(spec)
        assert "query_q" in schema["properties"]
        assert "q" in schema["properties"]

    def test_no_params_produces_empty_properties(self) -> None:
        spec = _minimal_spec(paths={
            "/health": {"get": {"operationId": "health", "responses": {}}}
        })
        schema = self._get_schema(spec)
        assert schema["properties"] == {}
        assert "required" not in schema


class TestExtractBaseUrl:
    def test_returns_first_server_url(self) -> None:
        spec = _minimal_spec(servers=[
            {"url": "https://api.example.com/v1"},
            {"url": "https://staging.example.com"},
        ])
        assert extract_base_url(spec) == "https://api.example.com/v1"

    def test_returns_none_for_empty_servers(self) -> None:
        spec = _minimal_spec(servers=[])
        assert extract_base_url(spec) is None

    def test_returns_none_for_missing_servers(self) -> None:
        spec = _minimal_spec()
        assert extract_base_url(spec) is None

    def test_returns_none_for_non_list_servers(self) -> None:
        spec: dict[str, Any] = {"servers": "not a list"}
        assert extract_base_url(spec) is None

    def test_returns_none_for_non_dict_entry(self) -> None:
        spec: dict[str, Any] = {"servers": ["not a dict"]}
        assert extract_base_url(spec) is None


class TestFetchAndParse:
    @pytest.mark.asyncio
    async def test_end_to_end_with_mock_transport(self) -> None:
        spec = _minimal_spec(
            servers=[{"url": "https://api.example.com"}],
            paths={
                "/pets": {
                    "get": {
                        "operationId": "listPets",
                        "summary": "List pets",
                        "responses": {"200": {"description": "ok"}},
                    },
                    "post": {
                        "operationId": "createPet",
                        "summary": "Create a pet",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            }
                        },
                        "responses": {"201": {"description": "created"}},
                    },
                }
            },
        )
        spec_json = json.dumps(spec)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=spec)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_and_parse("https://api.example.com/openapi.json", http_client=client)

        assert isinstance(result, OpenApiParseResult)
        assert result.base_url == "https://api.example.com"
        assert len(result.discovered_tools) == 2
        assert result.errors == []

        names = {t["name"] for t in result.discovered_tools}
        assert names == {"listPets", "createPet"}

    @pytest.mark.asyncio
    async def test_collects_ref_warnings(self) -> None:
        spec = _minimal_spec(paths={
            "/x": {
                "get": {
                    "operationId": "getX",
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "http://ext/schema"}}}}},
                }
            }
        })

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=spec)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_and_parse("https://example.com/spec.json", http_client=client)

        assert len(result.errors) > 0
        assert any("external" in e.lower() or "Skipping" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_yaml_content_type_parsed(self) -> None:
        spec = _minimal_spec(paths={
            "/ping": {"get": {"operationId": "ping", "responses": {}}}
        })
        import yaml as _yaml
        spec_yaml = _yaml.dump(spec)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=spec_yaml.encode(),
                headers={"content-type": "application/x-yaml"},
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_and_parse("https://example.com/spec.yaml", http_client=client)

        assert len(result.discovered_tools) == 1
        assert result.discovered_tools[0]["name"] == "ping"

    @pytest.mark.asyncio
    async def test_rejects_http_non_localhost(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            await fetch_and_parse("http://remote-server.com/spec.json")

    @pytest.mark.asyncio
    async def test_allows_http_localhost(self) -> None:
        spec = _minimal_spec(paths={
            "/health": {"get": {"operationId": "health", "responses": {}}}
        })

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=spec)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_and_parse("http://localhost:8080/spec.json", http_client=client)

        assert len(result.discovered_tools) == 1

    @pytest.mark.asyncio
    async def test_tool_dict_shape(self) -> None:
        spec = _minimal_spec(paths={
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "summary": "Get a pet",
                    "parameters": [
                        {"name": "include", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {},
                }
            }
        })

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=spec)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_and_parse("https://example.com/spec.json", http_client=client)

        tool = result.discovered_tools[0]
        assert tool["name"] == "getPet"
        assert tool["description"] == "Get a pet"
        assert tool["method"] == "GET"
        assert tool["path"] == "/pets/{petId}"
        assert tool["path_params"] == ["petId"]
        assert tool["query_params"] == ["include"]
        assert tool["has_body"] is False
        assert tool["content_type"] is None
        assert "petId" in tool["inputSchema"]["properties"]
