"""Private GitHub REST API client for the GitHub Shu plugin."""

from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Any

from shu_plugin_sdk import (
    HttpRequestFailed,
    NonRetryableError,
    RetryableError,
    RetryConfig,
    with_retry,
)


class _GithubClient:
    """Private HTTP client for the GitHub REST API.

    Instantiated once per ``execute()`` call, holding the resolved PAT and
    host reference so they don't need to be threaded through every helper.

    All requests are made through ``_get``, which:
    - Merges the required ``Authorization`` and ``User-Agent`` headers
    - Wraps the call in ``@with_retry`` for transparent 429/5xx handling
    - Maps ``HttpRequestFailed`` to ``RetryableError`` / ``NonRetryableError``
      so the retry decorator knows what to do

    URL query parameters are embedded directly in the ``url`` string (not via
    a ``params=`` kwarg) because ``FakeHostBuilder`` matches routes by the
    full ``(method, url)`` string including the query portion.

    # SDK-NOTE: FakeHostBuilder matches routes by exact (method, url) string.
    # Passing params as a kwarg would prevent test routes from matching.
    # A params-dict-aware lookup would make call sites cleaner.
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
            time.sleep(1)

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
        email: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch all commits authored by ``email`` in ``repo`` between ``date`` and ``date_end``.

        Iterates every branch using the repositories commits endpoint.  To
        avoid missing commits whose UTC committer timestamp crosses midnight
        (e.g. a commit at 18:40 -0600 is 00:40 UTC the next day), the API
        ``until`` is extended by one calendar day.  Results are then filtered
        locally against the commit's *author date* in its stored timezone so
        that only commits the author considers to be on ``date``..``date_end``
        are included.  Commits reachable from multiple branches are
        deduplicated by SHA.

        Args:
            repo:     Repository in ``owner/repo`` format.
            email:    Author email address to filter by.
            date:     Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end: End date (ISO 8601 YYYY-MM-DD, inclusive).

        Returns:
            Deduplicated commit items whose author date falls in the requested
            range.
        """
        owner, repo_name = repo.split("/", 1)
        branches = await self.fetch_branches(owner, repo_name)

        since = f"{date}T00:00:00Z"
        # Extend by one day so late-evening commits in western timezones
        # (which spill into the next UTC day) are not excluded by the API.
        until_dt = _date.fromisoformat(date_end) + timedelta(days=1)
        until = f"{until_dt.isoformat()}T23:59:59Z"

        seen_shas: set[str] = set()
        all_commits: list[dict[str, Any]] = []

        for branch in branches:
            base_url = (
                f"{self.BASE_URL}/repos/{owner}/{repo_name}/commits"
                f"?sha={branch}&author={email}&since={since}&until={until}"
            )
            print(base_url)
            items = await self._paginate_search(base_url, per_page=100)
            print(items)
            for item in items:
                sha: str = item.get("sha", "")
                if sha and sha not in seen_shas:
                    if _author_date_in_range(item, date, date_end):
                        seen_shas.add(sha)
                        all_commits.append(item)

        return all_commits

    async def fetch_commit_stats(
        self,
        owner: str,
        repo_name: str,
        sha: str,
    ) -> dict[str, int]:
        """Fetch diff statistics for a single commit.

        Queries the individual commit endpoint and extracts line-level and
        file-level diff counts from the response.

        Args:
            owner:     Repository owner (user or organisation login).
            repo_name: Repository name (without owner prefix).
            sha:       Full or abbreviated commit SHA.

        Returns:
            Dict with ``additions``, ``deletions``, and ``files_changed`` counts.
            Falls back to zero values if the stats fields are absent.
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
        }


    async def fetch_prs_authored(
        self,
        repo: str,
        user: str,
        date: str,
        date_end: str,
    ) -> list[dict[str, Any]]:
        """Fetch pull requests authored by ``user`` in ``repo`` for the date range.

        Uses the GitHub issues search API (PRs are a superset of issues).
        Diff stats (additions/deletions/changed_files) are not available from
        the search endpoint and are returned as 0.

        Args:
            repo:     Repository in ``owner/repo`` format.
            user:     GitHub username of the PR author.
            date:     Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end: End date (ISO 8601 YYYY-MM-DD, inclusive).

        Returns:
            List of PR dicts matching the output schema PR shape with
            ``role="author"``.
        """
        base_url = (
            f"{self.BASE_URL}/search/issues"
            f"?q=type:pr+repo:{repo}+author:{user}+updated:{date}..{date_end}"
        )
        items = await self._paginate_search(base_url)
        result = []
        for item in items:
            pr = item.get("pull_request", {})
            merged_at: str | None = pr.get("merged_at")
            result.append({
                "number": item["number"],
                "title": item["title"],
                "state": item["state"],
                "merged": merged_at is not None,
                "merged_at": merged_at,
                # Diff stats are not available from the search API.
                "additions": 0,
                "deletions": 0,
                "changed_files": 0,
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

    async def fetch_review_details(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        pr_title: str,
    ) -> list[dict[str, Any]]:
        """Fetch review records submitted on a specific pull request.

        Queries the PR reviews endpoint and maps each review to the output
        schema review shape.

        Args:
            owner:     Repository owner (user or organisation login).
            repo_name: Repository name (without owner prefix).
            pr_number: Pull request number.
            pr_title:  Pull request title (carried through for display purposes).

        Returns:
            List of review dicts with ``pr_number``, ``pr_title``, ``state``,
            ``submitted_at``, and ``role="reviewer"``.
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews"
        response = await self._get(url)
        reviews_raw: list[dict[str, Any]] = response.get("body", [])
        return [
            {
                "pr_number": pr_number,
                "pr_title": pr_title,
                "state": review.get("state", ""),
                "body": review.get("body", ""),
                "submitted_at": review.get("submitted_at", ""),
                "role": "reviewer",
            }
            for review in reviews_raw
        ]


    async def fetch_activity(
        self,
        repo: str,
        user_email: str,
        date: str,
        date_end: str,
        github_username: str | None,
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Fetch and assemble all GitHub activity for a user in a date range.

        Orchestrates all API calls in the correct dependency order:
        1. Commits are always fetched first (they carry the ``author.login``
           field needed to resolve the GitHub username).
        2. Username resolution — uses ``github_username`` directly if provided,
           otherwise extracts from commit items.
        3. Per-commit diff stats are fetched individually; failures are
           non-fatal (zero stats + warning).
        4. If a username was resolved, PRs authored, PRs reviewed, and review
           details are fetched. If not, the result is commits-only with a
           diagnostic message.

        Args:
            repo:            Repository in ``owner/repo`` format.
            user_email:      Author email used for commit search and username
                             resolution.
            date:            Start date (ISO 8601 YYYY-MM-DD, inclusive).
            date_end:        End date (ISO 8601 YYYY-MM-DD, inclusive).
            github_username: Explicit GitHub username override; skips
                             resolution from commit items when provided.

        Returns:
            A ``(data, diagnostics)`` tuple where ``data`` matches the plugin
            output schema and ``diagnostics`` is ``None`` or a single-entry
            list for the commits-only mode warning.

        Raises:
            RetryableError:    When retries are exhausted on a 429/5xx error.
            NonRetryableError: On a permanent API error (4xx other than 429).
        """
        owner, repo_name = repo.split("/", 1)

        raw_commit_items = await self.fetch_commits(repo, user_email, date, date_end)

        resolved_username = (
            github_username
            if github_username is not None
            else _extract_username_from_commits(raw_commit_items)
        )

        commits = []
        for item in raw_commit_items:
            sha: str = item["sha"]
            try:
                stats = await self.fetch_commit_stats(owner, repo_name, sha)
            except Exception:
                stats = {"additions": 0, "deletions": 0, "files_changed": 0}
                await self._host.log.warning(
                    f"Failed to fetch stats for commit {sha}; using zero stats"
                )
            commits.append({
                "sha": sha,
                "message": item["commit"]["message"],
                "committed_at": item["commit"]["committer"]["date"],
                "stats": stats,
            })

        diagnostics: list[str] | None = None
        if resolved_username is not None:
            pull_requests = await self.fetch_prs_authored(
                repo, resolved_username, date, date_end
            )
            reviewed_prs = await self.fetch_prs_reviewed(
                repo, resolved_username, date, date_end
            )
            reviews: list[dict[str, Any]] = []
            for pr in reviewed_prs:
                pr_reviews = await self.fetch_review_details(
                    owner, repo_name, pr["number"], pr["title"]
                )
                reviews.extend(pr_reviews)
        else:
            pull_requests = []
            reviews = []
            diagnostics = [
                f"Could not resolve a GitHub username for {user_email}. "
                "Pull request and review data is unavailable. "
                "To include this data, pass github_username explicitly."
            ]

        data: dict[str, Any] = {
            "repo": repo,
            "date": date,
            "date_end": date_end,
            "user_email": user_email,
            "github_username": resolved_username,
            "commits": commits,
            "pull_requests": pull_requests,
            "reviews": reviews,
        }
        return data, diagnostics


def _extract_username_from_commits(commit_items: list[dict[str, Any]]) -> str | None:
    """Return the GitHub login of the commit author, or None if unresolvable.

    Inspects the ``author.login`` field on raw GitHub commit search result
    items. GitHub links commit emails to user accounts server-side and
    populates this field even for private emails, so no extra API call is
    needed.

    Args:
        commit_items: Raw items list from a GitHub commit search response.

    Returns:
        The first non-null ``author.login`` found in the list, or ``None``
        if every item has a null ``author`` or null ``author.login`` — meaning
        GitHub could not link any commit to a GitHub account.
    """
    for item in commit_items:
        author = item.get("author")
        if author is not None and author.get("login") is not None:
            return author["login"]
    return None


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
        ``False`` otherwise.  Returns ``True`` on parse errors so no commits
        are silently dropped due to unexpected date formats.
    """
    try:
        author_date_str: str = (
            item.get("commit", {}).get("author", {}).get("date", "")
        )
        if not author_date_str:
            return True
        dt = datetime.fromisoformat(author_date_str.replace("Z", "+00:00"))
        local_date = dt.date().isoformat()
        return date <= local_date <= date_end
    except Exception:
        return True
