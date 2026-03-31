"""PBAC tests for KnowledgeBaseService.

Uses a **real** PolicyCache (no mocking of check/is_admin) to verify that
get_knowledge_base, filter_accessible_kb_ids, and check_kb_read_access
enforce the correct action and resource slug.

Setup:
- Two knowledge bases: Research Papers (allowed) and Internal Docs (denied).
- Two users: admin-1 (admin bypass) and user-1 (policy grants kb.*
  on kb:research-papers only).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.core.exceptions import NotFoundError
from shu.services.knowledge_base_service import KnowledgeBaseService
from shu.services.policy_engine import CachedPolicy, CachedStatement, PolicyCache, _split_patterns

ADMIN_USER_ID = "admin-1"
REGULAR_USER_ID = "user-1"

ALLOWED_KB_ID = "kb-allowed"
ALLOWED_KB_NAME = "Research Papers"
ALLOWED_KB_SLUG = "research-papers"

DENIED_KB_ID = "kb-denied"
DENIED_KB_NAME = "Internal Docs"
DENIED_KB_SLUG = "internal-docs"

POLICY_ID = "policy-kb-access"


def _make_statement(actions: list[str], resources: list[str]) -> CachedStatement:
    exact_a, wc_a = _split_patterns(actions)
    exact_r, wc_r = _split_patterns(resources)
    return CachedStatement(
        exact_actions=exact_a,
        wildcard_actions=wc_a,
        exact_resources=exact_r,
        wildcard_resources=wc_r,
    )


def _make_pbac_cache() -> PolicyCache:
    """Build a PolicyCache granting user-1 access to research-papers only."""
    settings = MagicMock()
    settings.policy_cache_ttl = 9999
    cache = PolicyCache(settings=settings)
    cache._stale = False
    cache._last_refresh = 1e12

    cache._admin_user_ids = {ADMIN_USER_ID}
    cache._policies = {
        POLICY_ID: CachedPolicy(
            id=POLICY_ID,
            effect="allow",
            statements=[
                _make_statement(["kb.*"], [f"kb:{ALLOWED_KB_SLUG}"]),
            ],
        ),
    }
    cache._user_policies = {REGULAR_USER_ID: {POLICY_ID}}
    cache._group_policies = {}
    cache._user_groups = {}
    return cache


def _make_mock_kb(*, kb_id: str, name: str, slug: str) -> MagicMock:
    """Build a mock ORM KnowledgeBase object."""
    kb = MagicMock()
    kb.id = kb_id
    kb.name = name
    kb.slug = slug
    return kb


MOCK_KB_ALLOWED = _make_mock_kb(kb_id=ALLOWED_KB_ID, name=ALLOWED_KB_NAME, slug=ALLOWED_KB_SLUG)
MOCK_KB_DENIED = _make_mock_kb(kb_id=DENIED_KB_ID, name=DENIED_KB_NAME, slug=DENIED_KB_SLUG)


@pytest.fixture
def pbac_cache():
    return _make_pbac_cache()


@pytest.fixture
def db():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def service(db):
    return KnowledgeBaseService(db, config_manager=MagicMock())


VERIFIER_PATH = "shu.utils.knowledge_base_verifier.KnowledgeBaseVerifier"


class TestGetKnowledgeBase:
    """get_knowledge_base: single KB fetch + PBAC kb.read enforcement."""

    @pytest.mark.asyncio
    async def test_admin_accesses_allowed_kb(self, service, pbac_cache):
        """Admin bypasses PBAC and can access the allowed KB."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=MOCK_KB_ALLOWED):
            result = await service.get_knowledge_base(ALLOWED_KB_ID, ADMIN_USER_ID)
        assert result.id == ALLOWED_KB_ID
        assert result.slug == ALLOWED_KB_SLUG

    @pytest.mark.asyncio
    async def test_admin_accesses_denied_kb(self, service, pbac_cache):
        """Admin bypasses PBAC and can access KBs that regular users cannot."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=MOCK_KB_DENIED):
            result = await service.get_knowledge_base(DENIED_KB_ID, ADMIN_USER_ID)
        assert result.id == DENIED_KB_ID
        assert result.slug == DENIED_KB_SLUG

    @pytest.mark.asyncio
    async def test_user_accesses_allowed_kb(self, service, pbac_cache):
        """Regular user passes PBAC when the KB slug matches their policy."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=MOCK_KB_ALLOWED):
            result = await service.get_knowledge_base(ALLOWED_KB_ID, REGULAR_USER_ID)
        assert result.id == ALLOWED_KB_ID
        assert result.slug == ALLOWED_KB_SLUG

    @pytest.mark.asyncio
    async def test_user_denied_on_other_kb(self, service, pbac_cache):
        """Regular user is denied with NotFoundError when the KB slug is not in their policy."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=MOCK_KB_DENIED), \
             pytest.raises(NotFoundError):
            await service.get_knowledge_base(DENIED_KB_ID, REGULAR_USER_ID)

    @pytest.mark.asyncio
    async def test_nonexistent_kb_raises_not_found(self, service, pbac_cache):
        """NotFoundError is raised when the KB does not exist (same as PBAC deny)."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=None), \
             pytest.raises(NotFoundError):
            await service.get_knowledge_base("nonexistent-kb", REGULAR_USER_ID)


class TestFilterAccessibleKbIds:
    """filter_accessible_kb_ids: batch filter returning accessible KB IDs."""

    @pytest.mark.asyncio
    async def test_admin_sees_all_kbs(self, service, pbac_cache):
        """Admin bypasses PBAC so all KB IDs are returned."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.filter_accessible_kb_ids(
                ADMIN_USER_ID, [MOCK_KB_ALLOWED, MOCK_KB_DENIED],
            )
        assert set(result) == {ALLOWED_KB_ID, DENIED_KB_ID}

    @pytest.mark.asyncio
    async def test_user_sees_only_allowed_kbs(self, service, pbac_cache):
        """Regular user only gets IDs of KBs whose slug matches their policy."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.filter_accessible_kb_ids(
                REGULAR_USER_ID, [MOCK_KB_ALLOWED, MOCK_KB_DENIED],
            )
        assert result == [ALLOWED_KB_ID]

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self, service, pbac_cache):
        """Empty input list returns empty output without calling PBAC."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.filter_accessible_kb_ids(REGULAR_USER_ID, [])
        assert result == []

    @pytest.mark.asyncio
    async def test_all_denied_returns_empty(self, service, pbac_cache):
        """When all KBs are denied, an empty list is returned."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.filter_accessible_kb_ids(
                REGULAR_USER_ID, [MOCK_KB_DENIED],
            )
        assert result == []


class TestCheckKbReadAccess:
    """check_kb_read_access: batch check by UUID list, returns first denied ID or None."""

    @pytest.mark.asyncio
    async def test_admin_all_accessible(self, service, db, pbac_cache):
        """Admin bypasses PBAC so None is returned (all accessible)."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [MOCK_KB_ALLOWED, MOCK_KB_DENIED]
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.check_kb_read_access(
                ADMIN_USER_ID, [ALLOWED_KB_ID, DENIED_KB_ID],
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_user_all_accessible(self, service, db, pbac_cache):
        """Regular user gets None when all requested KBs are in their policy."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [MOCK_KB_ALLOWED]
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.check_kb_read_access(
                REGULAR_USER_ID, [ALLOWED_KB_ID],
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_user_gets_first_denied_id(self, service, db, pbac_cache):
        """Regular user gets the first denied KB ID when some are inaccessible."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [MOCK_KB_ALLOWED, MOCK_KB_DENIED]
        db.execute.return_value = mock_result

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.check_kb_read_access(
                REGULAR_USER_ID, [ALLOWED_KB_ID, DENIED_KB_ID],
            )
        assert result == DENIED_KB_ID

    @pytest.mark.asyncio
    async def test_empty_ids_returns_none(self, service, db, pbac_cache):
        """Empty KB ID list returns None (nothing denied)."""
        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            result = await service.check_kb_read_access(REGULAR_USER_ID, [])
        assert result is None


def _make_slug_result(slugs: list[str]) -> MagicMock:
    """Mock result for ``select(KnowledgeBase.slug)``."""
    mock = MagicMock()
    mock.fetchall.return_value = [(s,) for s in slugs]
    return mock


def _make_count_result(count: int) -> MagicMock:
    """Mock result for ``select(func.count(...))``."""
    mock = MagicMock()
    mock.scalar.return_value = count
    return mock


def _make_kb_result(kbs: list[MagicMock]) -> MagicMock:
    """Mock result for ``select(KnowledgeBase)``."""
    mock = MagicMock()
    mock.scalars.return_value.all.return_value = kbs
    return mock


def _make_document_result(doc: MagicMock | None) -> MagicMock:
    """Mock result for ``select(Document)``."""
    mock = MagicMock()
    mock.scalar_one_or_none.return_value = doc
    return mock


class TestListKnowledgeBases:
    """list_knowledge_bases: paginated list with SQL-level PBAC filtering."""

    @pytest.mark.asyncio
    async def test_admin_sees_all_kbs(self, service, db, pbac_cache):
        """Admin bypasses PBAC and sees every KB."""
        db.execute = AsyncMock(side_effect=[
            _make_slug_result([ALLOWED_KB_SLUG, DENIED_KB_SLUG]),
            _make_count_result(2),
            _make_kb_result([MOCK_KB_ALLOWED, MOCK_KB_DENIED]),
        ])

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            kbs, total = await service.list_knowledge_bases(ADMIN_USER_ID)

        assert total == 2
        assert {kb.id for kb in kbs} == {ALLOWED_KB_ID, DENIED_KB_ID}

    @pytest.mark.asyncio
    async def test_user_sees_only_allowed_kbs(self, service, db, pbac_cache):
        """Regular user only sees KBs their policy grants access to."""
        db.execute = AsyncMock(side_effect=[
            _make_slug_result([ALLOWED_KB_SLUG, DENIED_KB_SLUG]),
            _make_count_result(1),
            _make_kb_result([MOCK_KB_ALLOWED]),
        ])

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            kbs, total = await service.list_knowledge_bases(REGULAR_USER_ID)

        assert total == 1
        assert kbs[0].id == ALLOWED_KB_ID

    @pytest.mark.asyncio
    async def test_pagination_applied(self, service, db, pbac_cache):
        """Offset and limit are forwarded to the SQL query."""
        db.execute = AsyncMock(side_effect=[
            _make_slug_result([ALLOWED_KB_SLUG, DENIED_KB_SLUG]),
            _make_count_result(2),
            _make_kb_result([MOCK_KB_DENIED]),
        ])

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            kbs, total = await service.list_knowledge_bases(
                ADMIN_USER_ID, limit=1, offset=1,
            )

        assert total == 2
        assert len(kbs) == 1

    @pytest.mark.asyncio
    async def test_empty_database_returns_empty(self, service, db, pbac_cache):
        """No KBs in database returns empty list and zero count."""
        db.execute = AsyncMock(side_effect=[
            _make_slug_result([]),
            _make_count_result(0),
            _make_kb_result([]),
        ])

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache):
            kbs, total = await service.list_knowledge_bases(REGULAR_USER_ID)

        assert total == 0
        assert kbs == []



class TestGetDocument:
    """get_document: single document fetch raises NotFoundError on misses."""

    @pytest.mark.asyncio
    async def test_missing_document_raises_not_found(self, service, db, pbac_cache):
        """Document misses surface as NotFoundError instead of returning None."""
        db.execute.return_value = _make_document_result(None)

        with patch("shu.services.policy_engine.POLICY_CACHE", pbac_cache), \
             patch("shu.services.knowledge_base_service.POLICY_CACHE", pbac_cache), \
             patch(f"{VERIFIER_PATH}.get_optional", return_value=MOCK_KB_ALLOWED), \
             pytest.raises(NotFoundError, match="Document 'doc-404' not found"):
            await service.get_document(ALLOWED_KB_ID, "doc-404", user_id=REGULAR_USER_ID)
