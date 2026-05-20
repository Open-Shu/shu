"""Regression test for SHU-759: BaseProviderAdapter._call_plugin must
acquire its own short-lived DB session at the point of use.

Pre-SHU-759 the adapter stored ``context.db_session`` and passed it to
``execute_plugin`` mid-stream. After the chat endpoint refactor, the
request session is closed before any tool call fires — that stored
session is None and a real tool call would crash with ``AttributeError:
'NoneType' object has no attribute 'execute'``.

The fix is in ``BaseProviderAdapter._call_plugin``: open a fresh session
via ``get_async_session_local()`` regardless of what (if anything) was
passed at construction time. This test pins that behavior so a future
"helpful refactor" can't silently re-introduce the dependence on a
long-lived adapter session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.services.providers.adapter_base import BaseProviderAdapter, ProviderAdapterContext


class _FakeSessionFactory:
    """Async-context factory whose enter yields a sentinel session.

    Replaces ``get_async_session_local()()`` so the test can assert
    ``execute_plugin`` was called with the sentinel — proving the adapter
    used the freshly-acquired session, not whatever was passed at
    construction time.
    """

    def __init__(self, sentinel_session) -> None:
        self._session = sentinel_session
        self.enter_count = 0
        self.exit_count = 0

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self):
        self.enter_count += 1
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exit_count += 1


def _make_bare_adapter(context_db_session=None) -> BaseProviderAdapter:
    """Build a BaseProviderAdapter instance without running __init__.

    ``_call_plugin`` only reads ``self.conversation_owner_id`` and
    ``self.knowledge_base_ids`` off the adapter instance — both are
    populated explicitly here. Skipping ``__init__`` avoids the encryption-
    key / provider / settings wiring the constructor performs.
    """
    adapter = BaseProviderAdapter.__new__(BaseProviderAdapter)
    adapter.conversation_owner_id = "user-1"
    adapter.knowledge_base_ids = None
    # `self.db_session` is intentionally NOT set — the fix dropped that
    # attribute, and accessing it would mean the regression is back.
    return adapter


@pytest.mark.asyncio
async def test_call_plugin_uses_fresh_session_when_context_session_is_none():
    """The chat path constructs the adapter via UnifiedLLMClient with
    db_session=None. _call_plugin must still acquire a real session for
    execute_plugin instead of crashing on a NoneType."""
    sentinel_session = MagicMock(name="fresh_session_sentinel")
    fake_factory = _FakeSessionFactory(sentinel_session)

    adapter = _make_bare_adapter()

    with (
        patch(
            "shu.services.providers.adapter_base.get_async_session_local",
            return_value=fake_factory,
        ),
        patch(
            "shu.services.providers.adapter_base.execute_plugin",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_execute_plugin,
    ):
        result = await adapter._call_plugin("my_plugin", "my_op", {"x": 1})

    # The returned JSON should round-trip the execute_plugin response.
    assert result == '{"ok": true}'

    # The session factory must have been entered exactly once — proving the
    # adapter acquired a short-lived session and didn't reuse a stored one.
    assert fake_factory.enter_count == 1
    assert fake_factory.exit_count == 1

    # execute_plugin must have been called with the freshly-acquired
    # sentinel session, NOT with None.
    mock_execute_plugin.assert_awaited_once()
    call_args = mock_execute_plugin.await_args
    assert call_args.args[0] is sentinel_session, (
        "execute_plugin must receive the freshly-acquired session, not the "
        "(absent) adapter-stored one"
    )


@pytest.mark.asyncio
async def test_call_plugin_acquires_fresh_session_regardless_of_construction():
    """Even if a caller pre-SHU-759-style passes a live session in the
    ProviderAdapterContext, _call_plugin must still ignore it and open a
    fresh one. This guards against an accidental future code path that
    re-stores ``context.db_session`` on the adapter and silently reuses
    a now-closed session."""
    # Simulate a "stale" caller-provided session being baked into the
    # adapter as an attribute. If _call_plugin ever leans on this again,
    # the assertion below will fail because execute_plugin received the
    # stale session instead of the freshly-acquired sentinel.
    stale_session = MagicMock(name="stale_caller_session")
    sentinel_session = MagicMock(name="fresh_session_sentinel")
    fake_factory = _FakeSessionFactory(sentinel_session)

    adapter = _make_bare_adapter()
    adapter.db_session = stale_session  # type: ignore[attr-defined]

    with (
        patch(
            "shu.services.providers.adapter_base.get_async_session_local",
            return_value=fake_factory,
        ),
        patch(
            "shu.services.providers.adapter_base.execute_plugin",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_execute_plugin,
    ):
        await adapter._call_plugin("my_plugin", "my_op", {})

    call_args = mock_execute_plugin.await_args
    assert call_args.args[0] is sentinel_session, (
        "execute_plugin must use the freshly-acquired session even when a "
        "stale session is reachable from the adapter instance"
    )
    assert call_args.args[0] is not stale_session


def test_provider_adapter_context_accepts_none_db_session():
    """ProviderAdapterContext.db_session must be Optional. The chat path
    constructs UnifiedLLMClient with db_session=None; if this field were
    re-tightened to required-AsyncSession a dataclass-level TypeError or
    static-typing failure would surface before runtime.
    """
    ctx = ProviderAdapterContext(db_session=None, provider=None)
    assert ctx.db_session is None
