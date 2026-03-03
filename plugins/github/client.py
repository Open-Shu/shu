"""Private GitHub REST API client for the GitHub Shu plugin."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from shu_plugin_sdk import (
    HttpRequestFailed,
    NonRetryableError,
    RetryableError,
    RetryConfig,
    with_retry,
)


def _split_repo(repo: str) -> tuple[str, str]:
    """Split ``owner/repo`` into ``(owner, repo_name)``."""
    owner, repo_name = repo.split("/", 1)
    return owner, repo_name


class _GithubClient:
    """Private HTTP client for the GitHub REST API.

    Instantiated once per ``execute()`` call, holding the resolved PAT and
    host reference so they don't need to be threaded through every helper.

    All requests are made through ``_get``, which:
    - Merges the required ``Authorization`` and ``User-Agent`` headers
    - Wraps the call in ``@with_retry`` for transparent 429/5xx handling
    - Maps ``HttpRequestFailed`` to ``RetryableError`` / ``NonRetryableError``
      so the retry decorator knows what to do

    URL query parameters are embedded directly in the ``url`` string rather
    than via ``params=`` for historical reasons.
    """

    BASE_URL: str = "https://api.github.com"

    def __init__(self, pat: str, host: Any) -> None:
        """Initialise the client with an authenticated PAT and a host handle.

        Args:
            pat:  GitHub Personal Access Token (already validated as non-blank).
            host: Shu host capability object providing ``http`` and ``log``.

        """
        self._pat = pat
        self._host = host

    async def _get(
        self,
        url: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a GET request to the GitHub API with retry/backoff.

        Merges ``Authorization: Bearer {pat}`` and ``User-Agent: shu-github-plugin``
        with any caller-supplied ``extra_headers``, then delegates to
        ``host.http.fetch``.

        Transient errors (429, 5xx) are wrapped in ``RetryableError`` and
        retried up to three times with exponential backoff. Permanent errors
        (4xx other than 429) are wrapped in ``NonRetryableError`` and fail
        immediately.

        Args:
            url:           Full URL including any query parameters.
            extra_headers: Additional headers to merge (e.g. Accept overrides).

        Returns:
            The response dict from ``host.http.fetch``.

        Raises:
            RetryableError:    After all retries are exhausted on a transient error.
            NonRetryableError: On the first permanent (non-retryable) error.

        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._pat}",
            "User-Agent": "shu-github-plugin",
        }
        if extra_headers:
            headers.update(extra_headers)

        @with_retry(RetryConfig(max_retries=3, base_delay=2.0))
        async def _do_get() -> dict[str, Any]:
            try:
                return await self._host.http.fetch("GET", url, headers=headers)
            except HttpRequestFailed as e:
                if e.is_retryable:
                    raise RetryableError(str(e)) from e
                raise NonRetryableError(str(e)) from e

        return await _do_get()

    async def _paginate_search(
        self,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a GitHub endpoint.

        Handles both search envelopes (``{"items": [...]}`` body) and plain
        array responses.  Appends ``&per_page=N&page={n}`` to ``base_url``
        (using ``?`` if the URL has no existing query string) and iterates
        until a page returns fewer than ``per_page`` items.

        Args:
            base_url:      URL without pagination parameters.
            extra_headers: Additional headers forwarded to ``_get``.
            per_page:      Page size; defaults to 30 (GitHub search cap).

        Returns:
            Combined list of all items across all pages.

        """
        all_items: list[dict[str, Any]] = []
        page = 1

        while True:
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}per_page={per_page}&page={page}"
            response = await self._get(url, extra_headers=extra_headers)
            body = response.get("body", {})
            items: list[dict[str, Any]] = body if isinstance(body, list) else body.get("items", [])
            all_items.extend(items)
            if len(items) < per_page:
                break
            page += 1

        return all_items

    async def fetch_branches(self, owner: str, repo_name: str) -> list[str]:
        """Return all branch names for ``owner/repo_name``.

        GitHub's commit search API only indexes commits on the default branch
        unless a ``branch:`` qualifier is supplied.  Callers use the branch
        list to run a per-branch search so no commits are missed.

        Args:
            owner:     Repository owner login.
            repo_name: Repository name (without owner prefix).

        Returns:
            List of branch name strings.

        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/branches"
        items = await self._paginate_search(url, per_page=100)
        return [item["name"] for item in items if item.get("name")]

    async def fetch_commits(
        self,
        repo: str,
        username: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch all commits authored by ``username`` in ``repo`` between ``date`` and ``date_end``.

        Iterates every branch using the repositories commits endpoint.  The
        ``author`` query parameter accepts a GitHub login.  Results are
        filtered locally against the commit's *author date* in its stored
        timezone so that only commits the author considers to be on
        ``date``..``date_end`` are included.  Commits reachable from multiple
        branches are deduplicated by SHA.

        Args:
            repo:     Repository in ``owner/repo`` format.
            username: GitHub username to filter commits by.
            date:     Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end: End date (ISO 8601 YYYY-MM-DD, inclusive).

        Returns:
            Deduplicated commit items whose author date falls in the requested
            range.

        """
        owner, repo_name = _split_repo(repo)
        branches = await self.fetch_branches(owner, repo_name)

        # Widen server-side UTC window; exact range is enforced locally by _author_date_in_range.
        since_date = date_type.fromisoformat(date) - timedelta(days=1)
        until_date = date_type.fromisoformat(date_end) + timedelta(days=1)
        since = f"{since_date.isoformat()}T00:00:00Z"
        until = f"{until_date.isoformat()}T23:59:59Z"

        seen_shas: set[str] = set()
        all_commits: list[dict[str, Any]] = []

        for branch in branches:
            base_url = (
                f"{self.BASE_URL}/repos/{owner}/{repo_name}/commits"
                f"?sha={branch}&author={username}&since={since}&until={until}"
            )
            items = await self._paginate_search(base_url, per_page=100)
            for item in items:
                sha: str = item.get("sha", "")
                if sha and sha not in seen_shas and _author_date_in_range(item, date, date_end):
                    seen_shas.add(sha)
                    all_commits.append(item)

        return all_commits

    async def fetch_commit_detail(
        self,
        owner: str,
        repo_name: str,
        sha: str,
    ) -> dict[str, Any]:
        """Fetch diff statistics and file-level patches for a single commit.

        Queries the individual commit endpoint and extracts line-level stats
        and the unified-diff patch for each changed file.

        Args:
            owner:     Repository owner (user or organisation login).
            repo_name: Repository name (without owner prefix).
            sha:       Full or abbreviated commit SHA.

        Returns:
            Dict with ``additions``, ``deletions``, ``files_changed`` counts,
            and a ``files`` list of ``{filename, patch}`` dicts.

        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/commits/{sha}"
        response = await self._get(url)
        body: dict[str, Any] = response.get("body", {})
        stats: dict[str, Any] = body.get("stats", {})
        files: list[Any] = body.get("files", [])
        return {
            "additions": stats.get("additions", 0),
            "deletions": stats.get("deletions", 0),
            "files_changed": len(files),
            "files": [
                {
                    "filename": f.get("filename", ""),
                    "patch": f.get("patch", ""),
                }
                for f in files
            ],
        }

    async def fetch_pr_detail(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
    ) -> dict[str, Any]:
        """Fetch diff stats for a single pull request.

        Queries ``GET /repos/{owner}/{repo}/pulls/{number}`` and extracts
        ``additions``, ``deletions``, ``changed_files``, and merge state.

        Args:
            owner:     Repository owner login.
            repo_name: Repository name (without owner prefix).
            pr_number: Pull request number.

        Returns:
            Dict with ``additions``, ``deletions``, ``changed_files``,
            ``merged``, and ``merged_at``.

        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/pulls/{pr_number}"
        response = await self._get(url)
        body: dict[str, Any] = response.get("body", {})
        return {
            "additions": body.get("additions", 0),
            "deletions": body.get("deletions", 0),
            "changed_files": body.get("changed_files", 0),
            "merged": bool(body.get("merged", False)),
            "merged_at": body.get("merged_at"),
        }

    async def fetch_pr_commits(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[str]:
        """Fetch commit SHAs for a single pull request.

        Queries ``GET /repos/{owner}/{repo}/pulls/{number}/commits``
        (paginated) and returns the list of commit SHA strings.

        Args:
            owner:     Repository owner login.
            repo_name: Repository name (without owner prefix).
            pr_number: Pull request number.

        Returns:
            List of commit SHA strings in the PR.

        """
        url = (
            f"{self.BASE_URL}/repos/{owner}/{repo_name}"
            f"/pulls/{pr_number}/commits"
        )
        items = await self._paginate_search(url, per_page=100)
        return [item["sha"] for item in items if item.get("sha")]


    async def fetch_prs_authored(
        self,
        repo: str,
        user: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch pull requests authored by ``user`` in ``repo`` for the date range.

        Uses the GitHub issues search API to discover PRs, then enriches each
        with real diff stats from the individual PR endpoint and the list of
        commit SHAs from the PR commits endpoint.  Stat/commit failures are
        non-fatal (graceful degradation).

        Args:
            repo:     Repository in ``owner/repo`` format.
            user:     GitHub username of the PR author.
            date:     Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end: End date (ISO 8601 YYYY-MM-DD, inclusive).

        Returns:
            List of PR dicts matching the output schema PR shape with
            ``role="author"``.

        """
        owner, repo_name = _split_repo(repo)
        base_url = (
            f"{self.BASE_URL}/search/issues"
            f"?q=type:pr+repo:{repo}+author:{user}+updated:{date}..{date_end}"
        )
        items = await self._paginate_search(base_url)
        result = []
        for item in items:
            pr_number: int = item["number"]

            try:
                detail = await self.fetch_pr_detail(owner, repo_name, pr_number)
            except Exception:
                detail = {
                    "additions": 0,
                    "deletions": 0,
                    "changed_files": 0,
                    "merged": False,
                    "merged_at": None,
                }
                await self._host.log.warning(
                    f"Failed to fetch detail for PR #{pr_number}; using zero stats"
                )

            try:
                commit_shas = await self.fetch_pr_commits(owner, repo_name, pr_number)
            except Exception:
                commit_shas = []
                await self._host.log.warning(
                    f"Failed to fetch commits for PR #{pr_number}; using empty list"
                )

            result.append({
                "number": pr_number,
                "title": item["title"],
                "state": item["state"],
                "created_at": item.get("created_at", ""),
                "merged": detail["merged"],
                "merged_at": detail["merged_at"],
                "additions": detail["additions"],
                "deletions": detail["deletions"],
                "changed_files": detail["changed_files"],
                "commit_shas": commit_shas,
                "role": "author",
            })
        return result

    async def fetch_prs_reviewed(
        self,
        repo: str,
        user: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch pull requests reviewed by ``user`` in ``repo`` for the date range.

        Returns minimal PR stubs (number + title) used by the caller to
        drive ``fetch_review_details`` calls. The role and review-level
        fields are populated by that step.

        Args:
            repo:     Repository in ``owner/repo`` format.
            user:     GitHub username of the reviewer.
            date:     Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end: End date (ISO 8601 YYYY-MM-DD, inclusive).

        Returns:
            List of dicts with ``number`` and ``title`` for each reviewed PR.

        """
        base_url = (
            f"{self.BASE_URL}/search/issues"
            f"?q=type:pr+repo:{repo}+reviewed-by:{user}+updated:{date}..{date_end}"
        )
        items = await self._paginate_search(base_url)
        return [{"number": item["number"], "title": item["title"]} for item in items]

    async def fetch_review_comments(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """Fetch all inline review comments on a pull request.

        Queries ``GET /repos/{owner}/{repo}/pulls/{number}/comments``
        (paginated).  Each comment includes ``pull_request_review_id`` which
        links it to a top-level review submission.

        Args:
            owner:     Repository owner login.
            repo_name: Repository name (without owner prefix).
            pr_number: Pull request number.

        Returns:
            Raw list of review comment dicts from the GitHub API.

        """
        url = (
            f"{self.BASE_URL}/repos/{owner}/{repo_name}"
            f"/pulls/{pr_number}/comments"
        )
        return await self._paginate_search(url, per_page=100)

    async def fetch_review_details(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        pr_title: str,
        user: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch review records submitted by ``user`` on a specific pull request.

        Queries the PR reviews endpoint, filters to only reviews authored by
        ``user`` in the requested date range, then fetches inline review
        comments and attaches them to each review by ``pull_request_review_id``.

        Args:
            owner:     Repository owner (user or organisation login).
            repo_name: Repository name (without owner prefix).
            pr_number: Pull request number.
            pr_title:  Pull request title (carried through for display purposes).
            user:      GitHub username to filter reviews by.
            date:      Start date string ``YYYY-MM-DD`` (inclusive).
            date_end:  End date string ``YYYY-MM-DD`` (inclusive).

        Returns:
            List of review dicts with ``pr_number``, ``pr_title``, ``state``,
            ``submitted_at``, ``comments``, and ``role="reviewer"`` — only
            for ``user`` and within ``date``..``date_end``.

        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
        reviews_raw: list[dict[str, Any]] = await self._paginate_search(url)

        user_reviews = [
            r for r in reviews_raw
            if (r.get("user") or {}).get("login") == user
            and _timestamp_in_range(r.get("submitted_at"), date, date_end)
        ]
        if not user_reviews:
            return []

        # Fetch inline review comments and group by review id.
        try:
            all_comments = await self.fetch_review_comments(
                owner, repo_name, pr_number
            )
        except Exception:
            all_comments = []
            await self._host.log.warning(
                f"Failed to fetch review comments for PR #{pr_number}"
            )

        comments_by_review: dict[int, list[dict[str, Any]]] = {}
        for c in all_comments:
            rid = c.get("pull_request_review_id")
            if rid is not None:
                comments_by_review.setdefault(rid, []).append({
                    "path": c.get("path", ""),
                    "body": c.get("body", ""),
                    "diff_hunk": c.get("diff_hunk", ""),
                })

        return [
            {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "state": review.get("state", ""),
                "body": review.get("body", ""),
                "submitted_at": review.get("submitted_at", ""),
                "comments": comments_by_review.get(review.get("id", -1), []),
                "role": "reviewer",
            }
            for review in user_reviews
        ]


    async def fetch_activity(
        self,
        repo: str,
        date: str,
        date_end: str,
        github_username: str,
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Fetch and assemble all GitHub activity for a user in a date range.

        Orchestrates all API calls in the correct dependency order:
        1. Commits by GitHub username.
        2. Per-commit diff stats (failures are non-fatal).
        3. PRs authored and reviewed, plus review details.

        ``github_username`` is guaranteed present (enforced by ``op_auth``
        secrets preflight).

        Args:
            repo:            Repository in ``owner/repo`` format.
            date:            Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end:        End date (ISO 8601 YYYY-MM-DD, inclusive).
            github_username: GitHub username for all queries.

        Returns:
            A ``(data, diagnostics)`` tuple where ``data`` matches the plugin
            output schema and ``diagnostics`` is always ``None``.

        Raises:
            RetryableError:    When retries are exhausted on a 429/5xx error.
            NonRetryableError: On a permanent API error (4xx other than 429).

        """
        owner, repo_name = _split_repo(repo)

        raw_commit_items = await self.fetch_commits(repo, github_username, date, date_end)

        commits = []
        for item in raw_commit_items:
            sha: str = item["sha"]
            try:
                detail = await self.fetch_commit_detail(owner, repo_name, sha)
            except Exception:
                detail = {
                    "additions": 0,
                    "deletions": 0,
                    "files_changed": 0,
                    "files": [],
                }
                await self._host.log.warning(
                    f"Failed to fetch detail for commit {sha}; using zero stats"
                )
            commits.append({
                "sha": sha,
                "message": item["commit"]["message"],
                "committed_at": item["commit"]["committer"]["date"],
                "stats": {
                    "additions": detail["additions"],
                    "deletions": detail["deletions"],
                    "files_changed": detail["files_changed"],
                },
                "files": detail["files"],
            })

        pull_requests = await self.fetch_prs_authored(
            repo, github_username, date, date_end
        )
        reviewed_prs = await self.fetch_prs_reviewed(
            repo, github_username, date, date_end
        )
        reviews: list[dict[str, Any]] = []
        for pr in reviewed_prs:
            pr_reviews = await self.fetch_review_details(
                owner,
                repo_name,
                pr["number"],
                pr["title"],
                github_username,
                date,
                date_end,
            )
            reviews.extend(pr_reviews)

        data: dict[str, Any] = {
            "repo": repo,
            "date": date,
            "date_end": date_end,
            "github_username": github_username,
            "commits": commits,
            "pull_requests": pull_requests,
            "reviews": reviews,
        }
        return data, None


def _author_date_in_range(item: dict[str, Any], date: str, date_end: str) -> bool:
    """Return True if the commit's author date (in its stored timezone) falls within ``date``..``date_end``.

    GitHub stores author dates with their original timezone offset
    (e.g. ``2026-02-26T18:40:28-06:00``).  Converting to UTC before comparing
    would incorrectly shift late-evening commits to the next calendar day.
    By calling ``.date()`` on the timezone-aware datetime we recover the
    *local* calendar date as the author experienced it.

    Args:
        item:     Raw commit item from the GitHub API.
        date:     Start date string ``YYYY-MM-DD`` (inclusive).
        date_end: End date string ``YYYY-MM-DD`` (inclusive).

    Returns:
        ``True`` if the commit's local author date is within the range,
        ``False`` otherwise, including when the date field is missing or
        unparsable.

    """
    author_date_str: str = (
        item.get("commit", {}).get("author", {}).get("date", "")
    )
    if not author_date_str:
        return False
    try:
        dt = datetime.fromisoformat(author_date_str.replace("Z", "+00:00"))
        local_date = dt.date().isoformat()
        return date <= local_date <= date_end
    except ValueError:
        return False


def _timestamp_in_range(timestamp: Any, date: str, date_end: str) -> bool:
    """Return True if an ISO timestamp falls within ``date``..``date_end``.

    Args:
        timestamp: Timestamp string in ISO 8601 form (e.g. ``...Z``).
        date: Start date string ``YYYY-MM-DD`` (inclusive).
        date_end: End date string ``YYYY-MM-DD`` (inclusive).

    Returns:
        ``True`` when the timestamp parses and its date is within range.
        ``False`` when missing, unparsable, or out of range.

    """
    if not isinstance(timestamp, str) or not timestamp:
        return False
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    local_date = dt.date().isoformat()
    return date <= local_date <= date_end
