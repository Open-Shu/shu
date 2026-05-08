"""Shared API-test fixtures.

Routes that exercise framework-level behavior (Depends() resolution, exception
handler wiring, response Content-Type) need a real FastAPI app to observe the
behavior under test. Spinning up the production app via main.py drags in DB
init and settings validation; this helper keeps the surface minimal.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from shu.core.exceptions import ShuException


def make_app_with_router(router: APIRouter, *, prefix: str = "/api/v1") -> FastAPI:
    """Build a minimal FastAPI app mounting `router` and the production ShuException handler.

    The handler shape mirrors main.py's setup_exception_handlers — replicated
    here rather than imported because the production wiring pulls in
    get_settings_instance and the full app config.
    """
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    @app.exception_handler(ShuException)
    async def _shu_exception_handler(request, exc: ShuException):  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    return app
