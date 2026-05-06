"""Email backend factory and dependency injection helpers.

Selection mirrors `cache_backend.get_cache_backend`:
1. Read `SHU_EMAIL_BACKEND` from settings.
2. Validate required config for the chosen backend.
3. On missing config, log a single startup warning and fall back to
   `DisabledEmailBackend` so the app still boots.

Real backends (SMTP, Resend, ControlPlane) are imported lazily so the
foundation modules (console, disabled) carry no extra dependency weight.
"""

from typing import Optional

from ..logging import get_logger
from .backend import EmailBackend, EmailConfigurationError
from .console import ConsoleEmailBackend
from .disabled import DisabledEmailBackend

logger = get_logger(__name__)

_email_backend: Optional["EmailBackend"] = None


def _build_backend() -> EmailBackend:
    """Construct the configured backend, falling back to disabled on missing config."""
    from ..config import get_settings_instance

    settings = get_settings_instance()
    choice = (settings.email_backend or "disabled").strip().lower()

    if choice == "disabled":
        logger.info("Email backend: disabled (no outbound email)")
        return DisabledEmailBackend()

    if choice == "console":
        logger.info("Email backend: console (messages logged, not sent)")
        return ConsoleEmailBackend()

    if choice == "smtp":
        from .smtp import SMTPEmailBackend

        try:
            smtp_backend = SMTPEmailBackend.from_settings(settings)
        except EmailConfigurationError as e:
            logger.warning(
                "Email backend smtp selected but configuration is incomplete; "
                "falling back to disabled. Fix by setting %s.",
                ", ".join(e.details.get("missing", [])) or "the missing SMTP fields",
            )
            return DisabledEmailBackend()
        logger.info("Email backend: smtp (host=%s)", settings.smtp_host)
        return smtp_backend

    if choice == "resend":
        from .resend import ResendEmailBackend

        try:
            resend_backend = ResendEmailBackend.from_settings(settings)
        except EmailConfigurationError as e:
            logger.warning(
                "Email backend resend selected but configuration is incomplete; "
                "falling back to disabled. Fix by setting %s.",
                ", ".join(e.details.get("missing", [])) or "the missing Resend fields",
            )
            return DisabledEmailBackend()
        logger.info("Email backend: resend")
        return resend_backend

    if choice == "control_plane":
        try:
            # SHU-749 ships ControlPlaneEmailBackend. Until that lands, the
            # import fails and the factory degrades to disabled rather than
            # crashing the app — control_plane is a valid config value
            # whose implementation is pending.
            #
            # When SHU-749 lands, the backend will read cp_base_url and
            # router_shared_secret from billing/config.py BillingSettings
            # (introduced by SHU-743) — not from the core Settings passed
            # to .from_settings() here. The factory will continue to pass
            # `settings`; the backend's `from_settings` does the lookup.
            from .control_plane import ControlPlaneEmailBackend  # type: ignore[import-not-found]
        except ImportError:
            logger.warning(
                "Email backend control_plane is not implemented in this build; falling back to disabled. "
                "Track SHU-749 for the implementation."
            )
            return DisabledEmailBackend()
        try:
            cp_backend: EmailBackend = ControlPlaneEmailBackend.from_settings(settings)
        except EmailConfigurationError as e:
            logger.warning(
                "Email backend control_plane selected but configuration is incomplete; "
                "falling back to disabled. Fix by setting %s.",
                ", ".join(e.details.get("missing", [])) or "the missing control plane fields",
            )
            return DisabledEmailBackend()
        logger.info("Email backend: control_plane (relay via SHU control plane)")
        return cp_backend

    logger.warning(
        "Unknown SHU_EMAIL_BACKEND value '%s'; falling back to disabled. "
        "Valid values: disabled, console, smtp, resend, control_plane.",
        choice,
    )
    return DisabledEmailBackend()


async def get_email_backend() -> EmailBackend:
    """Return the configured email backend (singleton).

    Suitable for background tasks, schedulers, and worker code. FastAPI
    endpoints should prefer `get_email_backend_dependency`.
    """
    global _email_backend  # noqa: PLW0603

    if _email_backend is not None:
        return _email_backend

    _email_backend = _build_backend()
    return _email_backend


def get_email_backend_dependency() -> EmailBackend:
    """FastAPI dependency for the email backend.

    Returns the cached singleton if one has been constructed; otherwise
    constructs synchronously. The factory is sync because no current backend
    requires async initialisation.
    """
    global _email_backend  # noqa: PLW0603

    if _email_backend is not None:
        return _email_backend

    _email_backend = _build_backend()
    return _email_backend


async def initialize_email_backend() -> EmailBackend:
    """Initialise the email backend during application startup.

    Call from the FastAPI startup hook so missing-config warnings are emitted
    at boot rather than on first send.
    """
    return await get_email_backend()


def reset_email_backend() -> None:
    """Reset the singleton (for tests only)."""
    global _email_backend  # noqa: PLW0603
    _email_backend = None


def get_effective_email_backend_name() -> str:
    """Return the name of the actually-instantiated email backend.

    Differs from ``settings.email_backend`` when the configured backend
    could not be initialised (missing SMTP host, missing Resend API key,
    etc.) and was downgraded to ``DisabledEmailBackend`` by the factory.

    Auth flows that gate on "is email available" must check this rather
    than the raw setting — otherwise a self-hosted instance with
    ``SHU_EMAIL_BACKEND=smtp`` but no SMTP config configured will
    create users who need email verification but cannot receive mail,
    and reset requests will silently enqueue against the disabled
    backend rather than preserving the operator/manual fallback.
    """
    return get_email_backend_dependency().name
