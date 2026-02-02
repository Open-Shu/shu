class EgressDenied(Exception):
    pass


class CapabilityDenied(Exception):
    """Raised when a plugin tries to access a host capability it did not declare."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"Host capability '{capability}' not declared in plugin manifest")


class HttpRequestFailed(Exception):
    """Raised by host.http when a non-success HTTP status is returned.

    Attributes:
        status_code: int HTTP status
        url: str request URL
        body: Any parsed body (dict/list/str)
        headers: dict of response headers

    """

    def __init__(self, status_code: int, url: str, body: object = None, headers: dict | None = None) -> None:
        self.status_code = int(status_code)
        self.url = str(url)
        self.body = body
        self.headers = dict(headers or {})
        msg = f"HTTP {self.status_code} calling {self.url}"
        super().__init__(msg)
