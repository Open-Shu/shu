"""Unit tests for the GitHub Shu plugin.

Uses ``FakeHostBuilder`` from ``shu_plugin_sdk`` for all host interactions so
no real GitHub API calls are made during the test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shu_plugin_sdk import FakeHostBuilder
from shu_plugin_sdk.contracts import assert_plugin_contract

from github.manifest import PLUGIN_MANIFEST
from github.plugin import GithubPlugin


# ---------------------------------------------------------------------------
# Contract gate — do not modify.
# Runs the full SDK validation pipeline against GithubPlugin.
# Fails if the manifest, schemas, or op cross-references are invalid.
# ---------------------------------------------------------------------------


def test_contract() -> None:
    """Assert that GithubPlugin satisfies the full Shu plugin contract."""
    assert_plugin_contract(GithubPlugin, manifest=PLUGIN_MANIFEST)


# ---------------------------------------------------------------------------
# Shared test fixtures / shims
# ---------------------------------------------------------------------------

# Minimal execution-context shim used across all test cases.
_CTX = type("Ctx", (), {"user_id": "test_user", "agent_key": None})()

# ---------------------------------------------------------------------------
# URL and response helpers
#
# FakeHostBuilder matches routes by exact (method, url) string.  These helpers
# centralise URL construction so any future API-path change only needs one fix.
# ---------------------------------------------------------------------------

_GH_BASE = "https://api.github.com"
_REPO = "owner/myrepo"
_EMAIL = "dev@example.com"
_DATE = "2026-02-26"
_DATE_END = "2026-02-26"
_PAT = "ghp_test_token"
_USERNAME = "testuser"
_SHA = "abc123def456"


def _make_commit_item(sha: str = _SHA, username: str | None = _USERNAME) -> dict:
    """Return a raw GitHub commit search result item."""
    return {
        "sha": sha,
        "author": {"login": username} if username is not None else None,
        "commit": {
            "message": "Add feature X",
            "committer": {"date": f"{_DATE}T10:00:00Z"},
        },
    }


def _search_response(items: list) -> dict:
    """Wrap items in a GitHub search API response envelope."""
    return {"status_code": 200, "headers": {}, "body": {"items": items}}


def _commit_stats_response(additions: int = 10, deletions: int = 5, files: int = 2) -> dict:
    """Return a commit detail API response with diff stats."""
    return {
        "status_code": 200,
        "headers": {},
        "body": {
            "stats": {"additions": additions, "deletions": deletions},
            "files": [{"filename": f"file{i}.py"} for i in range(files)],
        },
    }


def _reviews_response(reviews: list) -> dict:
    """Return a PR reviews list API response."""
    return {"status_code": 200, "headers": {}, "body": reviews}


def _branches_url(
    owner: str = "owner",
    repo_name: str = "myrepo",
    page: int = 1,
) -> str:
    """Return the exact branches list URL the plugin builds for a given page."""
    return f"{_GH_BASE}/repos/{owner}/{repo_name}/branches?per_page=100&page={page}"


def _branches_response(names: list[str] | None = None) -> dict:
    """Return a branches list API response."""
    if names is None:
        names = ["main"]
    return {"status_code": 200, "headers": {}, "body": [{"name": n} for n in names]}


def _commit_search_url(
    email: str = _EMAIL,
    repo: str = _REPO,
    date: str = _DATE,
    date_end: str = _DATE_END,
    page: int = 1,
    branch: str = "main",
) -> str:
    """Return the exact commit-search URL the plugin builds for a given branch/page."""
    return (
        f"{_GH_BASE}/search/commits"
        f"?q=author-email:{email}+repo:{repo}+committer-date:{date}..{date_end}+branch:{branch}"
        f"&per_page=30&page={page}"
    )


def _commit_stats_url(
    sha: str = _SHA,
    owner: str = "owner",
    repo_name: str = "myrepo",
) -> str:
    """Return the exact per-commit stats URL the plugin builds."""
    return f"{_GH_BASE}/repos/{owner}/{repo_name}/commits/{sha}"


def _prs_authored_url(
    repo: str = _REPO,
    user: str = _USERNAME,
    date: str = _DATE,
    date_end: str = _DATE_END,
    page: int = 1,
) -> str:
    """Return the exact PR-authored search URL the plugin builds."""
    return (
        f"{_GH_BASE}/search/issues"
        f"?q=type:pr+repo:{repo}+author:{user}+updated:{date}..{date_end}"
        f"&per_page=30&page={page}"
    )


def _prs_reviewed_url(
    repo: str = _REPO,
    user: str = _USERNAME,
    date: str = _DATE,
    date_end: str = _DATE_END,
    page: int = 1,
) -> str:
    """Return the exact PR-reviewed search URL the plugin builds."""
    return (
        f"{_GH_BASE}/search/issues"
        f"?q=type:pr+repo:{repo}+reviewed-by:{user}+updated:{date}..{date_end}"
        f"&per_page=30&page={page}"
    )


def _reviews_url(
    pr_number: int = 42,
    owner: str = "owner",
    repo_name: str = "myrepo",
) -> str:
    """Return the exact PR reviews URL the plugin builds."""
    return f"{_GH_BASE}/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"


# ---------------------------------------------------------------------------
# Task 11: Core unit tests — success path, missing PAT, invalid params
# ---------------------------------------------------------------------------


async def test_fetch_activity_success() -> None:
    """Full success path: all API responses stubbed, result has expected shape.

    Stubs the five GitHub API endpoints the plugin calls for a single commit,
    one authored PR, and one reviewed PR.  Verifies top-level shape and spot-
    checks key field values.

    _Requirements: 9.2, 9.3_
    """
    commit_item = _make_commit_item()
    pr_authored = {
        "number": 42,
        "title": "Add feature X",
        "state": "closed",
        "pull_request": {"merged_at": f"{_DATE}T12:00:00Z"},
    }
    reviewed_pr = {"number": 43, "title": "Review this"}
    review = {"state": "APPROVED", "submitted_at": f"{_DATE}T11:00:00Z"}

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commit_search_url(), _search_response([commit_item]))
        .with_http_response("GET", _commit_stats_url(), _commit_stats_response())
        .with_http_response("GET", _prs_authored_url(), _search_response([pr_authored]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([reviewed_pr]))
        .with_http_response("GET", _reviews_url(pr_number=43), _reviews_response([review]))
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "success"
    assert result.data["repo"] == _REPO
    assert result.data["date"] == _DATE
    assert result.data["github_username"] == _USERNAME

    commits = result.data["commits"]
    assert len(commits) == 1
    assert commits[0]["sha"] == _SHA
    assert commits[0]["stats"]["additions"] == 10
    assert commits[0]["stats"]["files_changed"] == 2

    pull_requests = result.data["pull_requests"]
    assert len(pull_requests) == 1
    assert pull_requests[0]["number"] == 42
    assert pull_requests[0]["merged"] is True

    reviews = result.data["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["pr_number"] == 43
    assert reviews[0]["state"] == "APPROVED"


async def test_missing_pat() -> None:
    """No PAT configured — result must carry code='auth_missing'.

    _Requirements: 9.2, 9.3_
    """
    host = FakeHostBuilder().build()  # No secret set — secrets.get returns None

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "auth_missing"


async def test_invalid_repo_format() -> None:
    """repo without an owner/name slash — result must carry code='invalid_params'.

    _Requirements: 9.2, 9.4_
    """
    host = FakeHostBuilder().build()

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": "nodash",
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "invalid_params"


async def test_date_end_before_date() -> None:
    """date_end earlier than date — result must carry code='invalid_params'.

    _Requirements: 9.2, 9.4_
    """
    host = FakeHostBuilder().build()

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": "2026-02-05",
            "date_end": "2026-02-04",
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "invalid_params"


async def test_default_date_is_yesterday() -> None:
    """Omitting 'date' — result.data['date'] must equal yesterday in UTC.

    Stubs commit search and PRs with yesterday's date embedded in the URL
    (because FakeHostBuilder matches by exact URL string).  No PRs or reviews
    are returned so the test focuses purely on date defaulting.

    _Requirements: 9.2, 9.3_
    """
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    commit_item = _make_commit_item()

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response(
            "GET",
            _commit_search_url(date=yesterday, date_end=yesterday),
            _search_response([commit_item]),
        )
        .with_http_response("GET", _commit_stats_url(), _commit_stats_response())
        .with_http_response(
            "GET",
            _prs_authored_url(date=yesterday, date_end=yesterday),
            _search_response([]),
        )
        .with_http_response(
            "GET",
            _prs_reviewed_url(date=yesterday, date_end=yesterday),
            _search_response([]),
        )
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {"op": "fetch_activity", "repo": _REPO, "user_email": _EMAIL},
        _CTX,
        host,
    )

    assert result.status == "success"
    assert result.data["date"] == yesterday


# ---------------------------------------------------------------------------
# Task 12: Error-path unit tests — HTTP errors and retry
# ---------------------------------------------------------------------------


async def test_invalid_pat_401() -> None:
    """401 on branches list — result must carry code='auth_error'.

    A 401 is non-retryable; the plugin maps it directly to an auth error.
    The branches call is the first HTTP request so the 401 fires there.

    _Requirements: 9.2, 9.4_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_error("GET", _branches_url(), 401)
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "auth_error"


async def test_forbidden_403() -> None:
    """403 on branches list — result must carry code='forbidden'.

    _Requirements: 9.2, 9.4_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_error("GET", _branches_url(), 403)
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "forbidden"


async def test_repo_not_found_404() -> None:
    """404 on branches list — result must carry code='not_found'.

    _Requirements: 9.2, 9.4_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_error("GET", _branches_url(), 404)
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "not_found"


async def test_rate_limit_429_retries_exhausted() -> None:
    """429 on branches list (all retries exhausted) — result must carry code='rate_limited'.

    The branches call is the first HTTP request so the 429 fires there.
    asyncio.sleep is suppressed by the autouse _no_retry_sleep fixture so all
    three retry attempts complete instantly without real delays.

    _Requirements: 9.2, 9.5_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_error("GET", _branches_url(), 429)
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "error"
    assert result.error["code"] == "rate_limited"


async def test_commit_stats_failure_degrades() -> None:
    """500 on the per-commit stats endpoint — commit still returned with zero stats.

    Verifies the graceful degradation path: failed stats are non-fatal.
    The commit is present in the result with zeroed stats, and host.log.warning
    is awaited to signal the degraded state.

    _Requirements: 9.2, 9.5_
    """
    commit_item = _make_commit_item()
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commit_search_url(), _search_response([commit_item]))
        .with_http_error("GET", _commit_stats_url(), 500)
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "success"
    assert len(result.data["commits"]) == 1
    assert result.data["commits"][0]["stats"] == {
        "additions": 0,
        "deletions": 0,
        "files_changed": 0,
    }
    host.log.warning.assert_awaited()


async def test_empty_activity() -> None:
    """All search endpoints return empty items — all three output lists are empty.

    Passes github_username explicitly so the PR and review endpoints are
    attempted; empty search results should produce empty lists, not an error.

    _Requirements: 9.2, 9.3_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commit_search_url(), _search_response([]))
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
            "github_username": _USERNAME,  # bypass username resolution from commits
        },
        _CTX,
        host,
    )

    assert result.status == "success"
    assert result.data["commits"] == []
    assert result.data["pull_requests"] == []
    assert result.data["reviews"] == []


async def test_commits_only_mode_when_email_not_resolvable() -> None:
    """Commits with null author.login — plugin falls back to commits-only mode.

    When GitHub cannot link the commit email to an account, all commit items
    carry ``"author": null``.  The plugin succeeds with an empty PR/review
    result and sets a diagnostic message explaining their absence.

    _Requirements: 9.2, 9.3_
    """
    commit_item = _make_commit_item(username=None)  # author field is null

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commit_search_url(), _search_response([commit_item]))
        .with_http_response("GET", _commit_stats_url(), _commit_stats_response())
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "success"
    assert result.data["github_username"] is None
    assert result.data["pull_requests"] == []
    assert result.data["reviews"] == []
    assert result.diagnostics is not None
    assert len(result.diagnostics) == 1
    assert "Pull request and review data is unavailable" in result.diagnostics[0]


# ---------------------------------------------------------------------------
# Task 13: Pagination unit test
# ---------------------------------------------------------------------------


async def test_pagination_two_pages() -> None:
    """Two pages of commit results — all commits across both pages are included.

    Page 1 returns exactly 30 items (the per_page limit), which causes
    ``_paginate_search`` to fetch page 2.  Page 2 returns 5 items (fewer than
    the limit), stopping iteration.  The final result must contain all 35
    commits, confirming the stopping condition is correct.

    All items share the same SHA so a single stats stub covers all 35 stat
    requests; this test is about pagination behaviour, not commit uniqueness.

    _Requirements: 5.6, 9.3_
    """
    # Unique SHAs required: fetch_commits deduplicates by SHA across branches,
    # so same-SHA items would collapse to one.  Stats stubs are omitted; the
    # graceful-degradation path leaves stats as-is (non-fatal) and this test
    # only asserts commit count, not stat values.
    page1_items = [_make_commit_item(sha=f"sha_p1_{i:03d}") for i in range(30)]
    page2_items = [_make_commit_item(sha=f"sha_p2_{i:03d}") for i in range(5)]

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commit_search_url(page=1), _search_response(page1_items))
        .with_http_response("GET", _commit_search_url(page=2), _search_response(page2_items))
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )

    plugin = GithubPlugin()
    result = await plugin.execute(
        {
            "op": "fetch_activity",
            "repo": _REPO,
            "user_email": _EMAIL,
            "date": _DATE,
            "date_end": _DATE_END,
        },
        _CTX,
        host,
    )

    assert result.status == "success"
    assert len(result.data["commits"]) == 35
