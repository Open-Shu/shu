"""
Router utilities for API-wide behaviors.

Currently provides trailing-slash tolerance by registering counterpart routes
(with or without trailing slash) for all standard HTTP routes in a router.

Implementation notes:
- We only duplicate FastAPI APIRoute instances (skip WebSocket routes)
- Counterpart routes are added with include_in_schema=False to avoid OpenAPI bloat
- We preserve common metadata when available; runtime behavior is identical
"""
from typing import List

from fastapi import APIRouter
from fastapi.routing import APIRoute


def add_trailing_slash_variants(router: APIRouter) -> None:
    """
    For every route in the router, ensure both trailing-slash and non-trailing-slash
    variants are registered, avoiding Starlette's 307 redirect behavior.

    This mutates the router in-place.
    """
    # Take a snapshot of current routes to avoid iterating over routes we append
    original_routes: List[APIRoute] = [r for r in router.routes if isinstance(r, APIRoute)]

    # Build a quick lookup of existing paths to avoid duplicates
    existing_paths = set(r.path for r in original_routes)

    for r in original_routes:
        path = r.path  # This is the route's subpath within the router (may be '' or start with '/')

        # Special-case the router root path (empty string) to also create '/'
        if path == "" or path == "/":
            # Add the '/'
            if "/" not in existing_paths:
                router.add_api_route(
                    "/",
                    r.endpoint,
                    methods=list(r.methods or []),
                    response_model=r.response_model,
                    status_code=r.status_code,
                    tags=r.tags,
                    dependencies=r.dependencies,
                    summary=r.summary,
                    description=r.description,
                    responses=r.responses,
                    deprecated=r.deprecated,
                    name=r.name,
                    response_class=r.response_class,
                    include_in_schema=False,
                )
                existing_paths.add("/")
            # And add the '' (non-slash) variant if it somehow didn't exist
            if "" not in existing_paths:
                router.add_api_route(
                    "",
                    r.endpoint,
                    methods=list(r.methods or []),
                    response_model=r.response_model,
                    status_code=r.status_code,
                    tags=r.tags,
                    dependencies=r.dependencies,
                    summary=r.summary,
                    description=r.description,
                    responses=r.responses,
                    deprecated=r.deprecated,
                    name=r.name,
                    response_class=r.response_class,
                    include_in_schema=False,
                )
                existing_paths.add("")
            continue

        # Normalize to compute both variants for non-root paths
        no_slash = path.rstrip("/")
        with_slash = no_slash + "/"

        # If current route is the non-slash variant, add the slash variant when missing
        if path == no_slash and with_slash not in existing_paths:
            router.add_api_route(
                with_slash,
                r.endpoint,
                methods=list(r.methods or []),
                response_model=r.response_model,
                status_code=r.status_code,
                tags=r.tags,
                dependencies=r.dependencies,
                summary=r.summary,
                description=r.description,
                responses=r.responses,
                deprecated=r.deprecated,
                name=r.name,
                response_class=r.response_class,
                include_in_schema=False,
            )
            existing_paths.add(with_slash)

        # If current route is the slash variant, add the non-slash variant when missing
        if path == with_slash and no_slash not in existing_paths:
            router.add_api_route(
                no_slash,
                r.endpoint,
                methods=list(r.methods or []),
                response_model=r.response_model,
                status_code=r.status_code,
                tags=r.tags,
                dependencies=r.dependencies,
                summary=r.summary,
                description=r.description,
                responses=r.responses,
                deprecated=r.deprecated,
                name=r.name,
                response_class=r.response_class,
                include_in_schema=False,
            )
            existing_paths.add(no_slash)

