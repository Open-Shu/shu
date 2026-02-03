class EgressDenied(Exception):
    pass


class CapabilityDenied(Exception):
    """Raised when a plugin tries to access a host capability it did not declare."""

    def __init__(self, capability: str):
        super().__init__(f"Host capability '{capability}' not declared in plugin manifest")


class HttpRequestFailed(Exception):
    """Raised by host.http when a non-success HTTP status is returned.

    This exception provides semantic properties so plugins don't need to parse
    status codes or extract error messages from response bodies. For most cases,
    plugins can simply let this exception bubble up - the executor will convert
    it to a structured PluginResult.err() automatically.

    Attributes:
        status_code: int HTTP status
        url: str request URL
        body: Any parsed body (dict/list/str)
        headers: dict of response headers

    Properties:
        error_category: Semantic category (auth_error, forbidden, rate_limited, etc.)
        is_retryable: True for 429 and 5xx errors
        retry_after_seconds: From Retry-After header if present
        provider_message: Best-effort extraction of error message from body
    """

    def __init__(self, status_code: int, url: str, body: object = None, headers: dict | None = None):
        self.status_code = int(status_code)
        self.url = str(url)
        self.body = body
        self.headers = dict(headers or {})
        msg = f"HTTP {self.status_code} calling {self.url}"
        super().__init__(msg)

    @property
    def error_category(self) -> str:
        """Semantic error category based on HTTP status code.

        Returns one of:
        - auth_error: 401 Unauthorized
        - forbidden: 403 Forbidden
        - not_found: 404 Not Found
        - gone: 410 Gone (e.g., expired delta tokens)
        - rate_limited: 429 Too Many Requests
        - server_error: 5xx errors
        - client_error: other 4xx errors
        """
        if self.status_code == 401:
            return "auth_error"
        elif self.status_code == 403:
            return "forbidden"
        elif self.status_code == 404:
            return "not_found"
        elif self.status_code == 410:
            return "gone"
        elif self.status_code == 429:
            return "rate_limited"
        elif self.status_code >= 500:
            return "server_error"
        else:
            return "client_error"

    @property
    def is_retryable(self) -> bool:
        """True for errors that may succeed on retry (429, 5xx)."""
        return self.status_code == 429 or self.status_code >= 500

    @property
    def retry_after_seconds(self) -> int | None:
        """Parse Retry-After header if present. Returns seconds or None.
        
        Performs case-insensitive header lookup per RFC 7230.
        """
        # Case-insensitive header lookup
        retry_after = None
        for key, value in self.headers.items():
            if key.lower() == "retry-after":
                retry_after = value
                break
        
        if not retry_after:
            return None
        try:
            return int(retry_after)
        except (ValueError, TypeError):
            # Could be HTTP-date format; return None for simplicity
            return None

    @property
    def provider_message(self) -> str:
        """Best-effort extraction of error message from response body.

        Attempts to extract from common API error formats:
        - {"error": {"message": "..."}} (Microsoft Graph, Google APIs)
        - {"error_description": "..."} (OAuth)
        - {"error": "...", "message": "..."} (various)
        - {"message": "..."} (simple)
        - Plain string body
        """
        if self.body is None:
            return ""

        if isinstance(self.body, str):
            return self.body[:500] if len(self.body) > 500 else self.body

        if isinstance(self.body, dict):
            # Try nested error object first (Microsoft Graph, Google APIs)
            error_obj = self.body.get("error")
            if isinstance(error_obj, dict):
                msg = error_obj.get("message")
                if msg:
                    return str(msg)

            # Try common top-level keys
            for key in ("error_description", "message", "error", "detail"):
                val = self.body.get(key)
                if val and isinstance(val, str):
                    return val

            # Fallback to string representation
            return str(self.body)[:500]

        return str(self.body)[:500]

    @property
    def provider_error_code(self) -> str | None:
        """Extract provider-specific error code if available.

        Attempts to extract from common API error formats:
        - {"error": {"code": "..."}} (Microsoft Graph)
        - {"error": {"status": "..."}} (Google APIs)
        - {"code": "..."} (simple)
        """
        if not isinstance(self.body, dict):
            return None

        error_obj = self.body.get("error")
        if isinstance(error_obj, dict):
            code = error_obj.get("code") or error_obj.get("status")
            if code:
                return str(code)

        code = self.body.get("code")
        if code:
            return str(code)

        return None

