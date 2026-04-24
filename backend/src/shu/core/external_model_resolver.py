"""Shared resolution logic for external model backends (embedding, OCR).

Queries llm_models for an active model of a given type, extracts the
provider's API base URL and decrypted API key. Used by both the embedding
and OCR service resolution to avoid duplicate DB/credential logic.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.llm_provider import LLMModel, LLMProvider
from .config import get_settings_instance
from .database import get_async_session_local
from .exceptions import InactiveProviderError
from .logging import get_logger

logger = get_logger(__name__)

# Positive-result TTL cache for provider/model is_active checks.
# Every embedding batch and OCR call hits this guard; uncached, that's a
# per-call round-trip to Postgres on a hot ingestion path. 60s bounds the
# staleness window after an admin flips is_active — short enough that a
# deactivation feels responsive, long enough to absorb ingest bursts.
# Only ACTIVE results are cached; inactive raises and re-checks next time
# so reactivation takes effect immediately.
_ACTIVE_CHECK_TTL_SECONDS = 60.0
_active_check_cache: dict[tuple[str, str], float] = {}


def _clear_active_check_cache() -> None:
    """Test hook — drop the positive-result cache between tests."""
    _active_check_cache.clear()


@dataclass(frozen=True)
class ResolvedExternalModel:
    """Credentials and metadata for an external model ready to use."""

    model_id: str
    model_name: str
    provider_id: str
    provider_name: str
    api_base_url: str
    api_key: str = field(repr=False)
    config: dict[str, Any] = field(default_factory=dict)


async def resolve_external_model(model_type: str) -> ResolvedExternalModel | None:
    """Look up an active external model of the given type and extract its credentials.

    Args:
        model_type: The llm_models.model_type to search for (e.g. "embedding", "ocr").

    Returns:
        ResolvedExternalModel with credentials, or None if no usable model is found.

    """
    from cryptography.fernet import Fernet
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    # TODO: Nondeterministic when multiple active models of the same type exist.
    # limit(1) with no order_by means the DB picks an arbitrary row. We need
    # either a selection policy (e.g., a "preferred" flag or most-recently-created)
    # or a uniqueness constraint ensuring only one active model per type. This is
    # risky for embedding dimension checks and re-embedding behavior if two active
    # embedding models with different dimensions coexist.
    session_factory = get_async_session_local()
    async with session_factory() as session:
        result = await session.execute(
            select(LLMModel)
            .join(LLMModel.provider)
            .where(
                LLMModel.model_type == model_type,
                LLMModel.is_active,
                LLMProvider.is_active,
            )
            .options(selectinload(LLMModel.provider))
            .order_by(LLMModel.created_at.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()

    if model is None or model.provider is None:
        return None

    provider = model.provider
    provider_config = provider.config if isinstance(provider.config, dict) else {}

    api_base_url = provider_config.get("get_api_base_url")
    if not api_base_url:
        logger.warning(
            "Provider %s has no api_base_url configured, skipping external %s",
            provider.name,
            model_type,
        )
        return None

    api_key = None
    if provider.api_key_encrypted:
        settings = get_settings_instance()
        encryption_key = settings.llm_encryption_key
        if encryption_key:
            try:
                fernet = Fernet(encryption_key.encode())
                api_key = fernet.decrypt(provider.api_key_encrypted.encode()).decode()
            except Exception:
                logger.error(
                    "Failed to decrypt API key for provider %s, skipping external %s",
                    provider.name,
                    model_type,
                )
                return None

    if not api_key:
        logger.warning(
            "Provider %s has no API key, skipping external %s",
            provider.name,
            model_type,
        )
        return None

    return ResolvedExternalModel(
        model_id=model.id,
        model_name=model.model_name,
        provider_id=provider.id,
        provider_name=provider.name,
        api_base_url=api_base_url,
        api_key=api_key,
        config=model.config or {},
    )


async def ensure_provider_and_model_active(
    provider_id: str,
    model_id: str,
    *,
    call_type: str,
    session: AsyncSession | None = None,
) -> None:
    """Raise :class:`InactiveProviderError` if the provider or model is deactivated.

    Positive results are cached for ``_ACTIVE_CHECK_TTL_SECONDS`` (60s) — admin
    deactivations take up to that long to stop routing. When ``session`` is provided the check
    reuses it (callers that already hold a session, like the OCR pre-call path,
    avoid opening a second connection); otherwise a fresh session is opened so
    long-lived embedding/OCR singletons still observe real-time state.
    """
    cache_key = (provider_id, model_id)
    now = time.monotonic()
    expires_at = _active_check_cache.get(cache_key)
    if expires_at is not None and expires_at > now:
        return

    if session is None:
        session_factory = get_async_session_local()
        async with session_factory() as owned_session:
            provider = await owned_session.get(LLMProvider, provider_id)
            model = await owned_session.get(LLMModel, model_id)
    else:
        provider = await session.get(LLMProvider, provider_id)
        model = await session.get(LLMModel, model_id)

    if provider is None or not provider.is_active:
        logger.warning(
            f"Blocking {call_type} call — provider inactive",
            extra={"provider_id": provider_id, "model_id": model_id},
        )
        raise InactiveProviderError(f"Provider '{provider_id}' is inactive; {call_type} call blocked.")

    if model is None or not model.is_active:
        logger.warning(
            f"Blocking {call_type} call — model inactive",
            extra={"provider_id": provider_id, "model_id": model_id},
        )
        raise InactiveProviderError(f"Model '{model_id}' is inactive; {call_type} call blocked.")

    _active_check_cache[cache_key] = now + _ACTIVE_CHECK_TTL_SECONDS
