"""Email backend abstraction for Shu.

Selection logic mirrors `cache_backend` and `queue_backend`:
- `SHU_EMAIL_BACKEND=smtp` → `SMTPEmailBackend`
- `SHU_EMAIL_BACKEND=resend` → `ResendEmailBackend`
- `SHU_EMAIL_BACKEND=console` → `ConsoleEmailBackend`
- `SHU_EMAIL_BACKEND=disabled` (default) or missing required config → `DisabledEmailBackend`

A fifth implementation, `ControlPlaneEmailBackend`, lives in a sibling ticket
(SHU-749) and is loaded lazily by the factory when selected.

Use `get_email_backend()` from background tasks; use
`get_email_backend_dependency` in FastAPI endpoints.
"""

from .backend import (
    EmailBackend,
    EmailBackendError,
    EmailConfigurationError,
    EmailMessage,
    EmailTransportError,
    SendResult,
    SendStatus,
)
from .console import ConsoleEmailBackend
from .disabled import DisabledEmailBackend
from .factory import (
    get_email_backend,
    get_email_backend_dependency,
    initialize_email_backend,
    reset_email_backend,
)

__all__ = [
    "ConsoleEmailBackend",
    "DisabledEmailBackend",
    "EmailBackend",
    "EmailBackendError",
    "EmailConfigurationError",
    "EmailMessage",
    "EmailTransportError",
    "SendResult",
    "SendStatus",
    "get_email_backend",
    "get_email_backend_dependency",
    "initialize_email_backend",
    "reset_email_backend",
]
