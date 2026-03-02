"""Unit tests for the GitHub Shu plugin.

Uses ``FakeHostBuilder`` from ``shu_plugin_sdk`` for all host interactions so
no real GitHub API calls are made during the test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from shu_plugin_sdk import FakeHostBuilder
from shu_plugin_sdk.contracts import assert_plugin_contract

from .manifest import PLUGIN_MANIFEST
from .plugin import GithubPlugin

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Contract gate — do not modify.
# Runs the full SDK validation pipeline against GithubPlugin.
# Fails if the manifest, schemas, or op cross-references are invalid.
# ---------------------------------------------------------------------------


async def test_contract() -> None:
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
_DATE = "2026-02-26"
_DATE_END = "2026-02-26"
_PAT = "ghp_test_token"
_USERNAME = "testuser"
_SHA = "abc123def456"


async def _execute_fetch_activity(
    host: Any,
    *,
    repo: str = _REPO,
    date: str | None = _DATE,
    date_end: str | None = _DATE_END,
) -> Any:
    """Execute ``fetch_activity`` with common defaults used across tests."""
    params: dict[str, str] = {"op": "fetch_activity", "repo": repo}
    if date is not None:
        params["date"] = date
    if date_end is not None:
        params["date_end"] = date_end
    plugin = GithubPlugin()
    return await plugin.execute(
        params,
        _CTX,
        host,
    )


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


def _list_response(items: list) -> dict:
    """Return a plain-array API response (repos/commits, branches, etc.)."""
    return {"status_code": 200, "headers": {}, "body": items}


def _commit_detail_response(
    additions: int = 10,
    deletions: int = 5,
    files: int = 2,
    patches: list[str] | None = None,
) -> dict:
    """Return a commit detail API response with diff stats and file patches."""
    if patches is None:
        patches = [f"@@ -1,3 +1,4 @@\n+added line {i}" for i in range(files)]
    return {
        "status_code": 200,
        "headers": {},
        "body": {
            "stats": {"additions": additions, "deletions": deletions},
            "files": [
                {"filename": f"file{i}.py", "patch": patches[i] if i < len(patches) else ""}
                for i in range(files)
            ],
        },
    }


def _pr_detail_response(
    additions: int = 20,
    deletions: int = 3,
    changed_files: int = 4,
    merged: bool = True,
    merged_at: str | None = f"{_DATE}T12:00:00Z",
) -> dict:
    """Return a single-PR detail API response with diff/merge metadata."""
    return {
        "status_code": 200,
        "headers": {},
        "body": {
            "additions": additions,
            "deletions": deletions,
            "changed_files": changed_files,
            "merged": merged,
            "merged_at": merged_at,
        },
    }


def _pr_commits_response(shas: list[str] | None = None) -> dict:
    """Return a PR commits list API response."""
    if shas is None:
        shas = [_SHA]
    return {
        "status_code": 200,
        "headers": {},
        "body": [{"sha": sha} for sha in shas],
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


def _commits_url(
    username: str = _USERNAME,
    owner: str = "owner",
    repo_name: str = "myrepo",
    date: str = _DATE,
    date_end: str = _DATE_END,
    page: int = 1,
    branch: str = "main",
) -> str:
    """Return the exact repos/commits URL the plugin builds for a given branch/page.

    Must mirror the URL constructed by ``_GithubClient.fetch_commits``.
    """
    since = f"{date}T00:00:00Z"
    until = f"{date_end}T23:59:59Z"
    return (
        f"{_GH_BASE}/repos/{owner}/{repo_name}/commits"
        f"?sha={branch}&author={username}&since={since}&until={until}"
        f"&per_page=100&page={page}"
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


def _pr_detail_url(
    pr_number: int = 42,
    owner: str = "owner",
    repo_name: str = "myrepo",
) -> str:
    """Return the exact single-PR detail URL the plugin builds."""
    return f"{_GH_BASE}/repos/{owner}/{repo_name}/pulls/{pr_number}"


def _pr_commits_url(
    pr_number: int = 42,
    owner: str = "owner",
    repo_name: str = "myrepo",
    page: int = 1,
) -> str:
    """Return the exact PR commits list URL the plugin builds."""
    return (
        f"{_GH_BASE}/repos/{owner}/{repo_name}/pulls/{pr_number}/commits"
        f"?per_page=100&page={page}"
    )


def _review_comments_url(
    pr_number: int = 42,
    owner: str = "owner",
    repo_name: str = "myrepo",
    page: int = 1,
) -> str:
    """Return the exact PR review comments URL the plugin builds."""
    return (
        f"{_GH_BASE}/repos/{owner}/{repo_name}/pulls/{pr_number}/comments"
        f"?per_page=100&page={page}"
    )


# ---------------------------------------------------------------------------
# Task 11: Core unit tests — success path, missing PAT, invalid params
# ---------------------------------------------------------------------------


async def test_fetch_activity_success() -> None:
    """Full success path: all API responses stubbed, result has expected shape.

    Stubs all GitHub API endpoints the plugin calls for a single commit,
    one authored PR (with detail + commits), and one reviewed PR (with
    user-filtered reviews).  Verifies top-level shape, diff stats, file
    patches, commit SHAs, and review filtering.

    _Requirements: 9.2, 9.3_
    """
    commit_item = _make_commit_item()
    pr_authored = {
        "number": 42,
        "title": "Add feature X",
        "state": "closed",
        "created_at": f"{_DATE}T09:00:00Z",
        "pull_request": {},
    }
    reviewed_pr = {"number": 43, "title": "Review this"}
    # Two reviews on the PR: one by our user, one by someone else.
    user_review = {
        "id": 1001,
        "state": "APPROVED",
        "body": "LGTM",
        "submitted_at": f"{_DATE}T11:00:00Z",
        "user": {"login": _USERNAME},
    }
    other_review = {
        "id": 1002,
        "state": "CHANGES_REQUESTED",
        "body": "Needs work",
        "submitted_at": f"{_DATE}T11:30:00Z",
        "user": {"login": "otheruser"},
    }
    # Inline review comment belonging to the user's review.
    inline_comment = {
        "pull_request_review_id": 1001,
        "path": "src/app.py",
        "body": "Nice refactor here",
        "diff_hunk": "@@ -10,3 +10,4 @@\n+new line",
        "user": {"login": _USERNAME},
    }

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        # Commits
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commits_url(), _list_response([commit_item]))
        .with_http_response("GET", _commit_stats_url(), _commit_detail_response())
        # Authored PRs + enrichment
        .with_http_response("GET", _prs_authored_url(), _search_response([pr_authored]))
        .with_http_response("GET", _pr_detail_url(pr_number=42), _pr_detail_response())
        .with_http_response(
            "GET",
            _pr_commits_url(pr_number=42),
            _pr_commits_response(shas=["sha_pr_1", "sha_pr_2"]),
        )
        # Reviewed PRs + reviews (includes other user's review to test filtering)
        .with_http_response("GET", _prs_reviewed_url(), _search_response([reviewed_pr]))
        .with_http_response(
            "GET",
            _reviews_url(pr_number=43),
            _reviews_response([user_review, other_review]),
        )
        # Inline review comments
        .with_http_response(
            "GET",
            _review_comments_url(pr_number=43),
            _list_response([inline_comment]),
        )
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "success"
    assert result.data["repo"] == _REPO
    assert result.data["date"] == _DATE
    assert result.data["github_username"] == _USERNAME

    # Commit assertions — stats + file-level patches
    commits = result.data["commits"]
    assert len(commits) == 1
    assert commits[0]["sha"] == _SHA
    assert commits[0]["stats"]["additions"] == 10
    assert commits[0]["stats"]["files_changed"] == 2
    assert len(commits[0]["files"]) == 2
    assert commits[0]["files"][0]["filename"] == "file0.py"
    assert "@@ -1,3 +1,4 @@" in commits[0]["files"][0]["patch"]

    # PR assertions — real diff stats, commit SHAs, created_at
    pull_requests = result.data["pull_requests"]
    assert len(pull_requests) == 1
    assert pull_requests[0]["number"] == 42
    assert pull_requests[0]["merged"] is True
    assert pull_requests[0]["merged_at"] == f"{_DATE}T12:00:00Z"
    assert pull_requests[0]["created_at"] == f"{_DATE}T09:00:00Z"
    assert pull_requests[0]["additions"] == 20
    assert pull_requests[0]["deletions"] == 3
    assert pull_requests[0]["changed_files"] == 4
    assert pull_requests[0]["commit_shas"] == ["sha_pr_1", "sha_pr_2"]

    # Review assertions — only the target user's review, with inline comments
    reviews = result.data["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["pr_number"] == 43
    assert reviews[0]["state"] == "APPROVED"
    assert reviews[0]["body"] == "LGTM"
    assert len(reviews[0]["comments"]) == 1
    assert reviews[0]["comments"][0]["path"] == "src/app.py"
    assert reviews[0]["comments"][0]["body"] == "Nice refactor here"
    assert "@@ -10,3 +10,4 @@" in reviews[0]["comments"][0]["diff_hunk"]


async def test_reviewed_pr_filters_out_of_range_reviews() -> None:
    """Only reviews submitted inside ``date``..``date_end`` are returned.

    _Requirements: 9.2, 9.3_
    """
    reviewed_pr = {"number": 43, "title": "Review this"}
    in_range_review = {
        "id": 1001,
        "state": "APPROVED",
        "body": "Looks good",
        "submitted_at": f"{_DATE}T11:00:00Z",
        "user": {"login": _USERNAME},
    }
    out_of_range_review = {
        "id": 1002,
        "state": "COMMENTED",
        "body": "Old review",
        "submitted_at": "2026-02-20T09:00:00Z",
        "user": {"login": _USERNAME},
    }

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commits_url(), _list_response([]))
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([reviewed_pr]))
        .with_http_response(
            "GET",
            _reviews_url(pr_number=43),
            _reviews_response([in_range_review, out_of_range_review]),
        )
        .with_http_response(
            "GET",
            _review_comments_url(pr_number=43),
            _list_response([]),
        )
        .build()
    )

    result = await _execute_fetch_activity(host)

    assert result.status == "success"
    assert len(result.data["reviews"]) == 1
    assert result.data["reviews"][0]["state"] == "APPROVED"
    assert result.data["reviews"][0]["submitted_at"] == f"{_DATE}T11:00:00Z"


async def test_missing_pat() -> None:
    """No PAT configured — result must carry code='auth_missing'.

    _Requirements: 9.2, 9.3_
    """
    host = FakeHostBuilder().build()  # No secret set — secrets.get returns None
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "auth_missing"


async def test_invalid_repo_format() -> None:
    """Repo without an owner/name slash — result must carry code='invalid_params'.

    _Requirements: 9.2, 9.4_
    """
    host = FakeHostBuilder().build()
    result = await _execute_fetch_activity(host, repo="nodash")

    assert result.status == "error"
    assert result.error["code"] == "invalid_params"


async def test_date_end_before_date() -> None:
    """date_end earlier than date — result must carry code='invalid_params'.

    _Requirements: 9.2, 9.4_
    """
    host = FakeHostBuilder().build()
    result = await _execute_fetch_activity(
        host,
        date="2026-02-05",
        date_end="2026-02-04",
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
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    commit_item = _make_commit_item()

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response(
            "GET",
            _commits_url(date=yesterday, date_end=today),
            _list_response([commit_item]),
        )
        .with_http_response("GET", _commit_stats_url(), _commit_detail_response())
        .with_http_response(
            "GET",
            _prs_authored_url(date=yesterday, date_end=today),
            _search_response([]),
        )
        .with_http_response(
            "GET",
            _prs_reviewed_url(date=yesterday, date_end=today),
            _search_response([]),
        )
        .build()
    )

    result = await _execute_fetch_activity(host, date=None, date_end=None)

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
        .with_secret("github_username", _USERNAME)
        .with_http_error("GET", _branches_url(), 401)
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "auth_error"


async def test_forbidden_403() -> None:
    """403 on branches list — result must carry code='forbidden'.

    _Requirements: 9.2, 9.4_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_error("GET", _branches_url(), 403)
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "forbidden"


async def test_repo_not_found_404() -> None:
    """404 on branches list — result must carry code='not_found'.

    _Requirements: 9.2, 9.4_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_error("GET", _branches_url(), 404)
        .build()
    )
    result = await _execute_fetch_activity(host)

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
        .with_secret("github_username", _USERNAME)
        .with_http_error("GET", _branches_url(), 429)
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "rate_limited"


async def test_commit_detail_failure_degrades() -> None:
    """500 on the per-commit detail endpoint — commit still returned with zero stats and empty files.

    Verifies the graceful degradation path: failed detail is non-fatal.
    The commit is present in the result with zeroed stats and empty files list.

    _Requirements: 9.2, 9.5_
    """
    commit_item = _make_commit_item()
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commits_url(), _list_response([commit_item]))
        .with_http_error("GET", _commit_stats_url(), 500)
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "success"
    assert len(result.data["commits"]) == 1
    assert result.data["commits"][0]["stats"] == {
        "additions": 0,
        "deletions": 0,
        "files_changed": 0,
    }
    assert result.data["commits"][0]["files"] == []


async def test_empty_activity() -> None:
    """All search endpoints return empty items — all three output lists are empty.

    _Requirements: 9.2, 9.3_
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commits_url(), _list_response([]))
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "success"
    assert result.data["commits"] == []
    assert result.data["pull_requests"] == []
    assert result.data["reviews"] == []


# ---------------------------------------------------------------------------
# Task 13: Pagination unit test
# ---------------------------------------------------------------------------


async def test_pagination_two_pages() -> None:
    """Two pages of commit results — all commits across both pages are included.

    Page 1 returns exactly 100 items (the per_page limit for the repos/commits
    endpoint), which causes ``_paginate_search`` to fetch page 2.  Page 2
    returns 5 items (fewer than the limit), stopping iteration.  The final
    result must contain all 105 commits.

    _Requirements: 5.6, 9.3_
    """
    # Unique SHAs required: fetch_commits deduplicates by SHA across branches,
    # so same-SHA items would collapse to one.  Stats stubs are omitted; the
    # graceful-degradation path leaves stats as-is (non-fatal) and this test
    # only asserts commit count, not stat values.
    page1_items = [_make_commit_item(sha=f"sha_p1_{i:03d}") for i in range(100)]
    page2_items = [_make_commit_item(sha=f"sha_p2_{i:03d}") for i in range(5)]

    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", _USERNAME)
        .with_http_response("GET", _branches_url(), _branches_response())
        .with_http_response("GET", _commits_url(page=1), _list_response(page1_items))
        .with_http_response("GET", _commits_url(page=2), _list_response(page2_items))
        .with_http_response("GET", _prs_authored_url(), _search_response([]))
        .with_http_response("GET", _prs_reviewed_url(), _search_response([]))
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "success"
    assert len(result.data["commits"]) == 105


# ---------------------------------------------------------------------------
# github_username validation
# ---------------------------------------------------------------------------


async def test_missing_github_username() -> None:
    """No github_username configured — result must carry code='auth_missing'.

    PAT is present but github_username secret is absent (returns None).
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "auth_missing"
    assert "username" in result.error["message"].lower()


async def test_blank_github_username() -> None:
    """github_username set to whitespace — result must carry code='auth_missing'.

    PAT is present but github_username is blank after stripping.
    """
    host = (
        FakeHostBuilder()
        .with_secret("github_pat", _PAT)
        .with_secret("github_username", "   ")
        .build()
    )
    result = await _execute_fetch_activity(host)

    assert result.status == "error"
    assert result.error["code"] == "auth_missing"
    assert "username" in result.error["message"].lower()
