"""Request size limit middleware, scoped by path prefix.
Intended for /api/v1/plugins/* endpoints initially.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int, path_prefix: str | None = None) -> None:
        super().__init__(app)
        self.max_bytes = int(max_bytes)
        self.path_prefix = path_prefix.rstrip("/") if path_prefix else None

    async def dispatch(self, request: Request, call_next):
        if self.path_prefix and not request.url.path.startswith(self.path_prefix):
            return await call_next(request)

        # Prefer Content-Length when present; fall back to reading limited body size.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "REQUEST_TOO_LARGE",
                            "detail": f"Content-Length exceeds limit of {self.max_bytes} bytes",
                            "max_bytes": self.max_bytes,
                        },
                    )
            except ValueError:
                pass

        # Read body carefully with limit to avoid buffering huge payloads
        body = await request.body()
        if len(body) > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "REQUEST_TOO_LARGE",
                    "detail": f"Payload exceeds limit of {self.max_bytes} bytes",
                    "max_bytes": self.max_bytes,
                },
            )

        # Replace the stream so downstream can read the same body
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]
        return await call_next(request)
