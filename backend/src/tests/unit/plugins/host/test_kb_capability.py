"""Unit tests for KbCapability search methods and host_builder changes.

Covers:
- _knowledge_base_ids immutability via object.__setattr__
- search_chunks / search_documents / get_document delegation to KbSearchService
- Structured error when _knowledge_base_ids is empty
- Exception in KbSearchService caught and returned as structured error
- parse_host_context() extracts kb.knowledge_base_ids
- make_host() passes KB IDs to KbCapability when "kb" in capabilities

Import strategy: modules are loaded directly from file paths to avoid triggering
the full shu.plugins.host package __init__, which stubs out heavy capability
dependencies. KbSearchService is imported lazily inside _with_search_service
(not at module level), so no kb_search_service stub is needed here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Direct module loading helpers (avoids circular import via package __init__)
# ---------------------------------------------------------------------------

_HOST_DIR = Path(__file__).resolve().parents[4] / "shu" / "plugins" / "host"
_SERVICES_DIR = Path(__file__).resolve().parents[4] / "shu" / "services"


def _load_module(module_name: str, file_path: Path):
    """Load a module directly from its file path, registering it in sys.modules."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot load module '{module_name}' from '{file_path}': "
            "spec_from_file_location returned None spec or loader"
        )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load base (needed by kb_capability and host_builder)
_base_mod = _load_module("shu.plugins.host.base", _HOST_DIR / "base.py")
_exceptions_mod = _load_module("shu.plugins.host.exceptions", _HOST_DIR / "exceptions.py")

# kb_capability imports several services; pre-register stubs for the heavy ones
# so that we can load kb_capability in isolation.  The stubs don't need real logic
# because every test mocks the pieces it cares about.
for _stub_name in (
    "shu.services.ingestion_service",
    "shu.services.knowledge_object_service",
    "shu.knowledge",
    "shu.knowledge.ko",
):
    if _stub_name not in sys.modules:
        sys.modules[_stub_name] = MagicMock()

# Ensure get_db_session is importable in core.database stub
if "shu.core" not in sys.modules:
    sys.modules["shu.core"] = MagicMock()
if "shu.core.database" not in sys.modules:
    _db_stub = MagicMock()
    _db_stub.get_db_session = AsyncMock()
    sys.modules["shu.core.database"] = _db_stub

# Now load kb_capability directly — no kb_search_service stub needed because
# kb_capability no longer imports it at module level (deferred local import).
_kb_cap_mod = _load_module("shu.plugins.host.kb_capability", _HOST_DIR / "kb_capability.py")
KbCapability = _kb_cap_mod.KbCapability

# Stub out the heavier capability modules used by host_builder before loading it
for _cap_stub in (
    "shu.plugins.host.auth_capability",
    "shu.plugins.host.cache_capability",
    "shu.plugins.host.cursor_capability",
    "shu.plugins.host.http_capability",
    "shu.plugins.host.identity_capability",
    "shu.plugins.host.log_capability",
    "shu.plugins.host.ocr_capability",
    "shu.plugins.host.secrets_capability",
    "shu.plugins.host.storage_capability",
    "shu.plugins.host.utils_capability",
):
    if _cap_stub not in sys.modules:
        sys.modules[_cap_stub] = MagicMock()

# host_builder also imports KbCapability; ensure it picks up the version we loaded
sys.modules["shu.plugins.host.kb_capability"] = _kb_cap_mod

_host_builder_mod = _load_module("shu.plugins.host.host_builder", _HOST_DIR / "host_builder.py")
parse_host_context = _host_builder_mod.parse_host_context
make_host = _host_builder_mod.make_host

CapabilityDenied = _exceptions_mod.CapabilityDenied


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cap_with_kbs():
    """KbCapability with two bound knowledge base IDs."""
    return KbCapability(
        plugin_name="test-plugin",
        user_id="user-123",
        knowledge_base_ids=["kb-1", "kb-2"],
    )


@pytest.fixture
def cap_no_kbs():
    """KbCapability with no knowledge base IDs bound."""
    return KbCapability(
        plugin_name="test-plugin",
        user_id="user-123",
        knowledge_base_ids=None,
    )


@pytest.fixture
def mock_db():
    """Async mock for a DB session."""
    db = AsyncMock()
    db.close = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Immutability tests
# ---------------------------------------------------------------------------


class TestKbCapabilityImmutability:
    """_knowledge_base_ids is set via object.__setattr__ and is immutable."""

    def test_knowledge_base_ids_set_on_construction(self, cap_with_kbs):
        """_knowledge_base_ids should be accessible after construction."""
        ids = object.__getattribute__(cap_with_kbs, "_knowledge_base_ids")
        assert ids == ["kb-1", "kb-2"]

    def test_knowledge_base_ids_none_becomes_empty_list(self, cap_no_kbs):
        """Passing None for knowledge_base_ids should result in an empty list."""
        ids = object.__getattribute__(cap_no_kbs, "_knowledge_base_ids")
        assert ids == []

    def test_knowledge_base_ids_is_immutable(self, cap_with_kbs):
        """Setting _knowledge_base_ids after construction must raise AttributeError."""
        with pytest.raises(AttributeError):
            cap_with_kbs._knowledge_base_ids = ["hacked"]

    def test_plugin_name_is_immutable(self, cap_with_kbs):
        """Setting _plugin_name after construction must raise AttributeError."""
        with pytest.raises(AttributeError):
            cap_with_kbs._plugin_name = "hacked"

    def test_user_id_is_immutable(self, cap_with_kbs):
        """Setting _user_id after construction must raise AttributeError."""
        with pytest.raises(AttributeError):
            cap_with_kbs._user_id = "hacked"

    def test_ocr_mode_is_immutable(self, cap_with_kbs):
        """Setting _ocr_mode after construction must raise AttributeError."""
        with pytest.raises(AttributeError):
            cap_with_kbs._ocr_mode = "always"

    def test_arbitrary_attribute_is_immutable(self, cap_with_kbs):
        """Setting any arbitrary attribute must raise AttributeError."""
        with pytest.raises(AttributeError):
            cap_with_kbs.new_attr = "value"


# ---------------------------------------------------------------------------
# Empty knowledge_base_ids — structured error returned immediately
# ---------------------------------------------------------------------------


class TestEmptyKnowledgeBaseIds:
    """Each search method returns a structured error when no KB IDs are bound."""

    @pytest.mark.asyncio
    async def test_search_chunks_returns_error_when_no_kbs(self, cap_no_kbs):
        """search_chunks returns no_knowledge_bases error when IDs list is empty."""
        result = await cap_no_kbs.search_chunks("content", "eq", "hello")
        assert result["status"] == "error"
        assert result["error"]["code"] == "no_knowledge_bases"
        assert isinstance(result["error"]["message"], str)

    @pytest.mark.asyncio
    async def test_search_documents_returns_error_when_no_kbs(self, cap_no_kbs):
        """search_documents returns no_knowledge_bases error when IDs list is empty."""
        result = await cap_no_kbs.search_documents("title", "eq", "hello")
        assert result["status"] == "error"
        assert result["error"]["code"] == "no_knowledge_bases"

    @pytest.mark.asyncio
    async def test_get_document_returns_error_when_no_kbs(self, cap_no_kbs):
        """get_document returns no_knowledge_bases error when IDs list is empty."""
        result = await cap_no_kbs.get_document("doc-123")
        assert result["status"] == "error"
        assert result["error"]["code"] == "no_knowledge_bases"


# ---------------------------------------------------------------------------
# Delegation tests — search_chunks
# ---------------------------------------------------------------------------


def _patch_access_granted(cap):
    """Context manager: patch _check_kb_access on KbCapability to return None (access granted)."""
    return patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=None))


class TestSearchChunksDelegation:
    """search_chunks delegates correctly to KbSearchService.search_chunks."""

    @pytest.mark.asyncio
    async def test_delegates_to_search_chunks_op(self, cap_with_kbs, mock_db):
        """search_chunks calls _SEARCH_OPS['search_chunks'] with correct args."""
        expected_result = {"status": "ok", "results": []}
        mock_op = AsyncMock(return_value=expected_result)

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService") as MockSvc, \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc

            result = await cap_with_kbs.search_chunks("content", "eq", "hello", page=1)

        assert result is expected_result
        MockSvc.assert_called_once_with(mock_db)
        mock_op.assert_called_once_with(
            mock_svc,
            knowledge_base_ids=["kb-1", "kb-2"],
            field="content",
            operator="eq",
            value="hello",
            page=1,
            sort_order="asc",
        )

    @pytest.mark.asyncio
    async def test_search_chunks_closes_db_on_success(self, cap_with_kbs, mock_db):
        """search_chunks always closes the DB session on success."""
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            await cap_with_kbs.search_chunks("content", "eq", "hello")

        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_chunks_default_page_is_1(self, cap_with_kbs, mock_db):
        """search_chunks uses page=1 as default when not specified."""
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService") as MockSvc, \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            MockSvc.return_value = MagicMock()
            await cap_with_kbs.search_chunks("content", "eq", "hello")

        _, kwargs = mock_op.call_args
        assert kwargs["page"] == 1


# ---------------------------------------------------------------------------
# Delegation tests — search_documents
# ---------------------------------------------------------------------------


class TestSearchDocumentsDelegation:
    """search_documents delegates correctly to KbSearchService.search_documents."""

    @pytest.mark.asyncio
    async def test_delegates_to_search_documents_op(self, cap_with_kbs, mock_db):
        """search_documents calls _SEARCH_OPS['search_documents'] with correct args."""
        expected_result = {"status": "ok", "results": [], "total_results": 0, "page": 2, "page_size": 20}
        mock_op = AsyncMock(return_value=expected_result)

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService") as MockSvc, \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_documents": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc

            result = await cap_with_kbs.search_documents("title", "icontains", "report", page=2)

        assert result is expected_result
        MockSvc.assert_called_once_with(mock_db)
        mock_op.assert_called_once_with(
            mock_svc,
            knowledge_base_ids=["kb-1", "kb-2"],
            field="title",
            operator="icontains",
            value="report",
            page=2,
            sort_order="asc",
        )

    @pytest.mark.asyncio
    async def test_search_documents_closes_db_on_success(self, cap_with_kbs, mock_db):
        """search_documents always closes the DB session on success."""
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_documents": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            await cap_with_kbs.search_documents("title", "eq", "hello")

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Delegation tests — get_document
# ---------------------------------------------------------------------------


class TestGetDocumentDelegation:
    """get_document delegates correctly to KbSearchService.get_document."""

    @pytest.mark.asyncio
    async def test_delegates_to_get_document_op(self, cap_with_kbs, mock_db):
        """get_document calls _SEARCH_OPS['get_document'] with correct args."""
        expected_result = {"id": "doc-99", "title": "My Doc", "content": "text"}
        mock_op = AsyncMock(return_value=expected_result)

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService") as MockSvc, \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"get_document": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            mock_svc = MagicMock()
            MockSvc.return_value = mock_svc

            result = await cap_with_kbs.get_document("doc-99")

        assert result is expected_result
        MockSvc.assert_called_once_with(mock_db)
        mock_op.assert_called_once_with(
            mock_svc,
            knowledge_base_ids=["kb-1", "kb-2"],
            document_id="doc-99",
        )

    @pytest.mark.asyncio
    async def test_get_document_closes_db_on_success(self, cap_with_kbs, mock_db):
        """get_document always closes the DB session on success."""
        mock_op = AsyncMock(return_value={"id": "doc-1"})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"get_document": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            await cap_with_kbs.get_document("doc-1")

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Exception handling — structured error on KbSearchService exception
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """Exceptions raised by KbSearchService are caught and returned as structured errors."""

    @pytest.mark.asyncio
    async def test_search_chunks_exception_returns_structured_error(self, cap_with_kbs, mock_db):
        """Exception in search_chunks op is caught and returned as structured error."""
        mock_op = AsyncMock(side_effect=RuntimeError("DB exploded"))

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            result = await cap_with_kbs.search_chunks("content", "eq", "hello")

        assert result["status"] == "error"
        assert result["error"]["code"] == "search_chunks_error"
        assert "DB exploded" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_search_documents_exception_returns_structured_error(self, cap_with_kbs, mock_db):
        """Exception in search_documents op is caught and returned as structured error."""
        mock_op = AsyncMock(side_effect=ValueError("bad value"))

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_documents": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            result = await cap_with_kbs.search_documents("title", "eq", "hello")

        assert result["status"] == "error"
        assert result["error"]["code"] == "search_documents_error"
        assert "bad value" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_get_document_exception_returns_structured_error(self, cap_with_kbs, mock_db):
        """Exception in get_document op is caught and returned as structured error."""
        mock_op = AsyncMock(side_effect=ConnectionError("timeout"))

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"get_document": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            result = await cap_with_kbs.get_document("doc-1")

        assert result["status"] == "error"
        assert result["error"]["code"] == "get_document_error"
        assert "timeout" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_exception_still_closes_db(self, cap_with_kbs, mock_db):
        """DB session is closed even when the op raises an exception."""
        mock_op = AsyncMock(side_effect=Exception("boom"))

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             _patch_access_granted(cap_with_kbs):
            await cap_with_kbs.search_chunks("content", "eq", "hello")

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# RBAC enforcement — _check_kb_access gates all search methods
# ---------------------------------------------------------------------------


class TestRbacEnforcement:
    """_check_kb_access error propagates through every search method."""

    @pytest.mark.asyncio
    async def test_search_chunks_blocked_when_access_denied(self, cap_with_kbs, mock_db):
        """search_chunks returns access_denied error when _check_kb_access denies access."""
        access_error = {
            "status": "error",
            "error": {"code": "access_denied", "message": "Access denied to knowledge base 'kb-1'."},
        }
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=access_error)):
            result = await cap_with_kbs.search_chunks("content", "eq", "hello")

        assert result["status"] == "error"
        assert result["error"]["code"] == "access_denied"
        mock_op.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_documents_blocked_when_access_denied(self, cap_with_kbs, mock_db):
        """search_documents returns access_denied error when _check_kb_access denies access."""
        access_error = {
            "status": "error",
            "error": {"code": "access_denied", "message": "Access denied to knowledge base 'kb-2'."},
        }
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_documents": mock_op}), \
             patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=access_error)):
            result = await cap_with_kbs.search_documents("title", "eq", "hello")

        assert result["status"] == "error"
        assert result["error"]["code"] == "access_denied"
        mock_op.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_document_blocked_when_access_denied(self, cap_with_kbs, mock_db):
        """get_document returns access_denied error when _check_kb_access denies access."""
        access_error = {
            "status": "error",
            "error": {"code": "access_denied", "message": "Access denied to knowledge base 'kb-1'."},
        }
        mock_op = AsyncMock(return_value={"id": "doc-1"})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"get_document": mock_op}), \
             patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=access_error)):
            result = await cap_with_kbs.get_document("doc-1")

        assert result["status"] == "error"
        assert result["error"]["code"] == "access_denied"
        mock_op.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_when_user_not_found(self, cap_with_kbs, mock_db):
        """search_chunks returns user_not_found error when _check_kb_access cannot find user."""
        access_error = {
            "status": "error",
            "error": {"code": "user_not_found", "message": "Executing user not found."},
        }
        mock_op = AsyncMock(return_value={"status": "ok", "results": []})

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": mock_op}), \
             patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=access_error)):
            result = await cap_with_kbs.search_chunks("content", "eq", "hello")

        assert result["status"] == "error"
        assert result["error"]["code"] == "user_not_found"
        mock_op.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_closed_even_when_access_denied(self, cap_with_kbs, mock_db):
        """DB session is closed even when _check_kb_access denies access."""
        access_error = {
            "status": "error",
            "error": {"code": "access_denied", "message": "denied"},
        }

        with patch("shu.plugins.host.kb_capability.get_db_session", new=AsyncMock(return_value=mock_db)), \
             patch("shu.services.kb_search_service.KbSearchService", return_value=MagicMock()), \
             patch.dict("shu.plugins.host.kb_capability._SEARCH_OPS", {"search_chunks": AsyncMock()}), \
             patch.object(KbCapability, "_check_kb_access", new=AsyncMock(return_value=access_error)):
            await cap_with_kbs.search_chunks("content", "eq", "hello")

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# parse_host_context — kb.knowledge_base_ids extraction
# ---------------------------------------------------------------------------


class TestParseHostContext:
    """parse_host_context extracts kb.knowledge_base_ids correctly."""

    def test_extracts_valid_kb_ids(self):
        """Should extract a list of valid KB ID strings."""
        ctx = {"kb": {"knowledge_base_ids": ["kb-a", "kb-b"]}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids == ["kb-a", "kb-b"]

    def test_returns_none_when_kb_key_missing(self):
        """Should return None for knowledge_base_ids when 'kb' key is absent."""
        ctx = {"auth": {}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_returns_none_when_knowledge_base_ids_key_missing(self):
        """Should return None when 'kb' dict lacks 'knowledge_base_ids' key."""
        ctx = {"kb": {"other_key": "value"}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_returns_none_when_list_is_empty(self):
        """Should return None when knowledge_base_ids list is empty."""
        ctx = {"kb": {"knowledge_base_ids": []}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_filters_out_non_string_ids(self):
        """Should filter out non-string entries from knowledge_base_ids."""
        ctx = {"kb": {"knowledge_base_ids": ["kb-1", 42, None, "kb-2"]}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids == ["kb-1", "kb-2"]

    def test_returns_none_when_all_ids_are_invalid(self):
        """Should return None when all IDs are non-string."""
        ctx = {"kb": {"knowledge_base_ids": [123, None, 456]}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_returns_none_when_empty_strings_only(self):
        """Should return None when all string IDs are empty strings."""
        ctx = {"kb": {"knowledge_base_ids": ["", ""]}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_handles_none_host_context(self):
        """Should return None knowledge_base_ids for None host_context input."""
        result = parse_host_context(None)
        assert result.knowledge_base_ids is None

    def test_handles_non_dict_kb_value(self):
        """Should return None when 'kb' is not a dict."""
        ctx = {"kb": "not-a-dict"}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids is None

    def test_single_kb_id(self):
        """Should work correctly with a single KB ID."""
        ctx = {"kb": {"knowledge_base_ids": ["kb-only"]}}
        result = parse_host_context(ctx)
        assert result.knowledge_base_ids == ["kb-only"]

    def test_also_extracts_schedule_id_and_ocr_mode(self):
        """parse_host_context also correctly extracts other context fields."""
        ctx = {
            "exec": {"schedule_id": "sched-123"},
            "ocr": {"mode": "always"},
            "kb": {"knowledge_base_ids": ["kb-1"]},
        }
        result = parse_host_context(ctx)
        assert result.schedule_id == "sched-123"
        assert result.ocr_mode == "always"
        assert result.knowledge_base_ids == ["kb-1"]


# ---------------------------------------------------------------------------
# make_host — KB IDs passed to KbCapability when "kb" in capabilities
# ---------------------------------------------------------------------------


class TestMakeHost:
    """make_host passes KB IDs to KbCapability when 'kb' is in capabilities."""

    def test_make_host_with_kb_capability_and_ids(self):
        """make_host creates KbCapability with correct knowledge_base_ids from host_context."""
        host_ctx = {"kb": {"knowledge_base_ids": ["kb-alpha", "kb-beta"]}}

        h = make_host(
            plugin_name="my-plugin",
            user_id="user-abc",
            user_email="user@example.com",
            capabilities=["kb"],
            host_context=host_ctx,
        )

        kb = object.__getattribute__(h, "kb")
        assert kb is not None
        ids = object.__getattribute__(kb, "_knowledge_base_ids")
        assert ids == ["kb-alpha", "kb-beta"]

    def test_make_host_with_kb_capability_no_ids_in_context(self):
        """make_host creates KbCapability with empty list when no KB IDs in context."""
        h = make_host(
            plugin_name="my-plugin",
            user_id="user-abc",
            user_email=None,
            capabilities=["kb"],
            host_context={},
        )

        kb = object.__getattribute__(h, "kb")
        assert kb is not None
        ids = object.__getattribute__(kb, "_knowledge_base_ids")
        assert ids == []

    def test_make_host_without_kb_capability_does_not_create_kb(self):
        """make_host does not set kb capability when 'kb' not in capabilities."""
        h = make_host(
            plugin_name="my-plugin",
            user_id="user-abc",
            user_email=None,
            capabilities=[],
            host_context={"kb": {"knowledge_base_ids": ["kb-1"]}},
        )

        # Accessing undeclared 'kb' capability should raise CapabilityDenied
        with pytest.raises(CapabilityDenied):
            _ = h.kb

    def test_make_host_kb_plugin_name_matches(self):
        """KbCapability._plugin_name matches the plugin_name passed to make_host."""
        h = make_host(
            plugin_name="acme-plugin",
            user_id="user-xyz",
            user_email=None,
            capabilities=["kb"],
            host_context={},
        )

        kb = object.__getattribute__(h, "kb")
        plugin_name = object.__getattribute__(kb, "_plugin_name")
        assert plugin_name == "acme-plugin"

    def test_make_host_kb_user_id_matches(self):
        """KbCapability._user_id matches the user_id passed to make_host."""
        h = make_host(
            plugin_name="some-plugin",
            user_id="user-999",
            user_email=None,
            capabilities=["kb"],
            host_context={},
        )

        kb = object.__getattribute__(h, "kb")
        user_id = object.__getattribute__(kb, "_user_id")
        assert user_id == "user-999"

    def test_make_host_kb_ocr_mode_from_host_context(self):
        """KbCapability._ocr_mode is taken from the ocr section of host_context."""
        host_ctx = {
            "ocr": {"mode": "never"},
            "kb": {"knowledge_base_ids": ["kb-1"]},
        }

        h = make_host(
            plugin_name="ocr-plugin",
            user_id="user-ocr",
            user_email=None,
            capabilities=["kb"],
            host_context=host_ctx,
        )

        kb = object.__getattribute__(h, "kb")
        ocr_mode = object.__getattribute__(kb, "_ocr_mode")
        assert ocr_mode == "never"

    def test_make_host_kb_schedule_id_from_host_context(self):
        """KbCapability._schedule_id is taken from the exec section of host_context."""
        host_ctx = {
            "exec": {"schedule_id": "sched-abc"},
            "kb": {"knowledge_base_ids": ["kb-1"]},
        }

        h = make_host(
            plugin_name="feed-plugin",
            user_id="user-feed",
            user_email=None,
            capabilities=["kb"],
            host_context=host_ctx,
        )

        kb = object.__getattribute__(h, "kb")
        schedule_id = object.__getattribute__(kb, "_schedule_id")
        assert schedule_id == "sched-abc"
