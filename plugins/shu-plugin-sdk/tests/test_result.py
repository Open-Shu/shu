"""Tests for shu_plugin_sdk.result (PluginResult, SkipReason, Skip, IngestionResult)."""

from __future__ import annotations

import pytest

from shu_plugin_sdk.result import IngestionResult, PluginResult, Skip, SkipReason


# ---------------------------------------------------------------------------
# PluginResult.ok()
# ---------------------------------------------------------------------------


def test_ok_defaults() -> None:
    r = PluginResult.ok()
    assert r.status == "success"
    assert r.data == {}
    assert r.error is None
    assert r.cost is None
    assert r.diagnostics is None
    assert r.skips is None
    assert r.citations is None


def test_ok_with_data() -> None:
    r = PluginResult.ok(data={"items": [1, 2, 3]})
    assert r.status == "success"
    assert r.data == {"items": [1, 2, 3]}


def test_ok_with_all_optional_fields() -> None:
    r = PluginResult.ok(
        data={"x": 1},
        cost={"api_calls": 2, "tokens": 500},
        diagnostics=["fetched 2 pages"],
        citations=[{"type": "web", "ref": "https://example.com", "label": "Example"}],
    )
    assert r.cost == {"api_calls": 2, "tokens": 500}
    assert r.diagnostics == ["fetched 2 pages"]
    assert r.citations == [{"type": "web", "ref": "https://example.com", "label": "Example"}]


def test_ok_none_data_normalized_to_empty_dict() -> None:
    """Passing data=None should produce data={}, not data=None."""
    r = PluginResult.ok(data=None)
    assert r.data == {}


# ---------------------------------------------------------------------------
# PluginResult.err()
# ---------------------------------------------------------------------------


def test_err_defaults() -> None:
    r = PluginResult.err("something went wrong")
    assert r.status == "error"
    assert r.data == {}
    assert r.error == {"code": "tool_error", "message": "something went wrong", "details": {}}


def test_err_custom_code() -> None:
    r = PluginResult.err("rate limited", code="rate_limit")
    assert r.error["code"] == "rate_limit"
    assert r.error["message"] == "rate limited"


def test_err_with_details() -> None:
    r = PluginResult.err("too large", code="size_limit", details={"size": 10_000, "max": 5_000})
    assert r.error["details"] == {"size": 10_000, "max": 5_000}


def test_err_none_details_normalized_to_empty_dict() -> None:
    r = PluginResult.err("oops", details=None)
    assert r.error["details"] == {}


# ---------------------------------------------------------------------------
# PluginResult.with_skips()
# ---------------------------------------------------------------------------


def test_with_skips_from_skip_dataclass() -> None:
    skip = Skip(id="doc-1", reason=SkipReason.too_large, details={"size": 999})
    r = PluginResult.ok(data={"indexed": 0}).with_skips([skip])
    assert r.skips == [{"id": "doc-1", "reason": "too_large", "details": {"size": 999}}]


def test_with_skips_from_raw_dict() -> None:
    r = PluginResult.ok().with_skips([{"id": "x", "reason": "other"}])
    assert r.skips == [{"id": "x", "reason": "other"}]


def test_with_skips_returns_self() -> None:
    """with_skips() is a fluent builder — it returns the same instance."""
    r = PluginResult.ok()
    returned = r.with_skips([])
    assert returned is r


def test_with_skips_empty_list() -> None:
    r = PluginResult.ok().with_skips([])
    assert r.skips == []


# ---------------------------------------------------------------------------
# Skip dataclass
# ---------------------------------------------------------------------------


def test_skip_to_dict_minimal() -> None:
    s = Skip(id="abc", reason=SkipReason.ext_filtered)
    assert s.to_dict() == {"id": "abc", "reason": "ext_filtered"}


def test_skip_to_dict_full() -> None:
    s = Skip(id="abc", reason=SkipReason.unsupported_format, name="report.pages", details={"mime": "application/x-iwork"})
    d = s.to_dict()
    assert d["name"] == "report.pages"
    assert d["details"] == {"mime": "application/x-iwork"}


def test_skip_to_dict_omits_none_fields() -> None:
    """name and details should not appear in the dict when not set."""
    s = Skip(id="x", reason=SkipReason.auth)
    d = s.to_dict()
    assert "name" not in d
    assert "details" not in d


def test_skip_accepts_raw_string_reason() -> None:
    """reason can be a plain string for forward-compat with future contract values."""
    s = Skip(id="x", reason="future_reason")
    assert s.to_dict()["reason"] == "future_reason"


# ---------------------------------------------------------------------------
# SkipReason enum
# ---------------------------------------------------------------------------


def test_skip_reason_values() -> None:
    expected = {"too_large", "ext_filtered", "unsupported_format", "empty_extraction", "auth", "network", "other"}
    actual = {r.value for r in SkipReason}
    assert actual == expected


def test_skip_reason_is_str() -> None:
    """SkipReason(str, Enum) members compare equal to their string values."""
    assert SkipReason.too_large == "too_large"
    assert SkipReason.network == "network"


# ---------------------------------------------------------------------------
# IngestionResult
# ---------------------------------------------------------------------------


def test_ingestion_result_ok_no_skips() -> None:
    r = IngestionResult.ok(data={"indexed": 5})
    assert r.status == "success"
    assert r.data == {"indexed": 5}
    assert r.skips is None


def test_ingestion_result_ok_with_skips() -> None:
    skips = [Skip(id="f1", reason=SkipReason.too_large)]
    r = IngestionResult.ok(data={"indexed": 3}, skips=skips)
    assert r.skips == [{"id": "f1", "reason": "too_large"}]


def test_ingestion_result_ok_empty_skips_list() -> None:
    """Empty skips list should leave skips as None (no skips occurred)."""
    r = IngestionResult.ok(data={}, skips=[])
    assert r.skips is None


def test_ingestion_result_is_plugin_result_subtype() -> None:
    assert isinstance(IngestionResult.ok(), PluginResult)


def test_ingestion_result_err_inherited() -> None:
    """IngestionResult inherits err() from PluginResult unchanged."""
    r = IngestionResult.err("auth failed", code="auth_error")
    assert r.status == "error"
    assert r.error["code"] == "auth_error"
