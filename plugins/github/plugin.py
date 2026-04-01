"""GitHub plugin for Shu — fetches daily commit, PR, and review activity.

This plugin queries the GitHub REST API to collect a user's activity for a
given repository and date range. It is the first plugin to adopt ``shu-plugin-sdk``
in production and therefore serves as an end-to-end validation of the SDK.

Authentication uses a Personal Access Token (PAT) stored in the host secrets
store under key ``github_pat``. OAuth integration is deferred to SHU-292.

Recommended token scope: fine-grained PAT with read-only ``contents``,
``pull_requests``, and ``issues`` permissions.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from typing import Any

from shu_plugin_sdk import (
    HttpRequestFailed,
    NonRetryableError,
    PluginResult,
    RetryableError,
)

from .client import _GithubClient

_REPO_RE = re.compile(r"^[^/]+/[^/]+$")


def _check_repo_format(repo: str) -> PluginResult | None:
    """Return an error result if ``repo`` is not in ``owner/repo`` format, else None."""
    if not _REPO_RE.match(repo):
        return PluginResult.err("repo must be in 'owner/repo' format.", code="invalid_params")
    return None


def _resolve_dates(params: dict[str, Any]) -> tuple[str, str]:
    """Return ``(date, date_end)`` with defaults applied.

    ``date`` defaults to yesterday (UTC); ``date_end`` defaults to today (UTC).
    """
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    date: str = params.get("date") or yesterday
    date_end: str = params.get("date_end") or now.strftime("%Y-%m-%d")
    return date, date_end


def _check_date_order(date: str, date_end: str) -> PluginResult | None:
    """Return an error result if either date is malformed or ``date_end`` is earlier than ``date``, else None."""
    try:
        d_start = date_type.fromisoformat(date)
        d_end = date_type.fromisoformat(date_end)
    except ValueError as e:
        return PluginResult.err(f"Invalid date: {e}", code="invalid_params")
    if d_end < d_start:
        return PluginResult.err("date_end must not be before date.", code="invalid_params")
    return None


def _map_api_error(exc: HttpRequestFailed, repo: str) -> PluginResult:
    """Map a GitHub API error to an appropriate ``PluginResult``."""
    if exc.error_category == "auth_error":
        return PluginResult.err("GitHub PAT is invalid or expired.", code="auth_error")
    if exc.error_category == "forbidden":
        return PluginResult.err("GitHub PAT lacks required permissions.", code="forbidden")
    if exc.error_category == "not_found":
        return PluginResult.err(f"Repository not found: {repo}", code="not_found")
    return PluginResult.err(str(exc), code=exc.error_category)


async def _resolve_pat(host: Any) -> tuple[str, PluginResult | None]:
    """Fetch and validate the GitHub PAT from the secrets store.

    Returns ``(pat, None)`` when the PAT is present, or ``("", error_result)``
    when it is missing or blank.
    """
    raw: str | None = await host.secrets.get("github_pat")
    pat = (raw or "").strip()
    if not pat:
        return "", PluginResult.err(
            "GitHub PAT not configured. "
            "Store your token via host.secrets under key 'github_pat'.",
            code="auth_missing",
        )
    return pat, None


# ---------------------------------------------------------------------------
# Per-op schemas
#
# _OP_SCHEMAS powers get_schema_for_op(), the forward-compatible per-op
# validation interface. get_schema() reuses the fetch_activity entry so the
# combined schema and the per-op schema are always in sync.
# ---------------------------------------------------------------------------

_OP_SCHEMAS: dict[str, dict[str, Any]] = {
    "fetch_activity": {
        "title": "Fetch Activity",
        "description": "Retrieve commits, PRs, and reviews for a repository.",
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository in owner/repo format",
            },
            "date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "Start date (ISO 8601 YYYY-MM-DD); defaults to yesterday UTC",
            },
            "date_end": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "End date (ISO 8601 YYYY-MM-DD); defaults to today (UTC)",
            },
        },
        "required": ["repo"],
        "additionalProperties": False,
    },
}


class GithubPlugin:
    """Shu plugin that fetches a user's daily GitHub activity.

    Implements the Shu Plugin Protocol:
    - ``name`` and ``version`` class attributes matched to the manifest
    - ``get_schema()``         — combined JSON Schema for all ops (Loader compatibility)
    - ``get_output_schema()``  — constrained output schema (prevents LLM context bloat)
    - ``get_schema_for_op()``  — per-op schema (forward-compatible interface)
    - ``execute()``            — async op dispatcher
    """

    name: str = "github"
    version: str = "1"

    def get_schema(self) -> dict[str, Any]:
        """Return the combined JSON Schema for all ops.

        Keeps the plugin compatible with the current Shu Loader, which uses
        ``properties.op.enum`` to discover available operations.
        """
        return _OP_SCHEMAS["fetch_activity"]

    def get_output_schema(self) -> dict[str, Any]:
        """Return the JSON Schema for the ``data`` field of a successful result.

        All nested objects set ``additionalProperties: false`` to prevent
        unbounded data from flowing into the LLM context.
        """
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "date": {"type": "string"},
                "date_end": {"type": "string"},
                "github_username": {"type": "string"},
                "commits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sha": {"type": "string"},
                            "message": {"type": "string"},
                            "committed_at": {
                                "type": "string",
                                "description": "ISO 8601 timestamp",
                            },
                            "stats": {
                                "type": "object",
                                "properties": {
                                    "additions": {"type": "integer"},
                                    "deletions": {"type": "integer"},
                                    "files_changed": {"type": "integer"},
                                },
                                "additionalProperties": False,
                            },
                            "files": {
                                "type": "array",
                                "description": "Changed files with unified-diff patches",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "filename": {"type": "string"},
                                        "patch": {
                                            "type": "string",
                                            "description": "Unified diff hunks (changed lines only)",
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "pull_requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer"},
                            "title": {"type": "string"},
                            "state": {"type": "string"},
                            "created_at": {
                                "type": "string",
                                "description": "ISO 8601 timestamp of PR creation",
                            },
                            "merged": {"type": "boolean"},
                            "merged_at": {"type": ["string", "null"]},
                            "additions": {"type": "integer"},
                            "deletions": {"type": "integer"},
                            "changed_files": {"type": "integer"},
                            "commit_shas": {
                                "type": "array",
                                "description": "SHA hashes of commits in this PR",
                                "items": {"type": "string"},
                            },
                            "role": {
                                "type": "string",
                                "description": "Always 'author' for this list",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pr_number": {"type": "integer"},
                            "pr_title": {"type": "string"},
                            "state": {"type": "string"},
                            "body": {
                                "type": "string",
                                "description": "Top-level review body (may be empty for inline-only reviews)",
                            },
                            "submitted_at": {
                                "type": "string",
                                "description": "ISO 8601 timestamp",
                            },
                            "comments": {
                                "type": "array",
                                "description": "Inline review comments attached to this review",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "path": {
                                            "type": "string",
                                            "description": "File path the comment is on",
                                        },
                                        "body": {
                                            "type": "string",
                                            "description": "Comment text",
                                        },
                                        "diff_hunk": {
                                            "type": "string",
                                            "description": "Diff context surrounding the comment",
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                            },
                            "role": {
                                "type": "string",
                                "description": "Always 'reviewer' for this list",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }

    def get_schema_for_op(self, op_name: str) -> dict[str, Any] | None:
        """Return the JSON Schema for a specific op, or None for unknown ops.

        This is the forward-compatible replacement for ``get_schema()``. Shu
        will use this method for precise per-op parameter validation once
        support is rolled out in the Executor.

        Args:
            op_name: The op name to look up.

        Returns:
            A valid JSON Schema dict for the op's parameters, or ``None`` if
            the op name is not recognised.

        """
        return _OP_SCHEMAS.get(op_name)

    async def execute(
        self,
        params: dict[str, Any],
        _context: Any,
        host: Any,
    ) -> PluginResult:
        """Execute the requested op and return a structured result.

        Args:
            params:  Validated input parameters (always contains ``op``).
            _context: Execution context (user_id, agent_key, etc.).
            host:    Host capability object — ``host.log``, ``host.http``,
                     ``host.secrets``.

        Returns:
            A ``PluginResult`` whose ``data`` shape matches ``get_output_schema()``.

        """
        op = (params.get("op") or "").strip()
        if op != "fetch_activity":
            return PluginResult.err(f"Unsupported op: '{op}'", code="invalid_op")

        repo: str = params.get("repo", "")
        if err := _check_repo_format(repo):
            return err

        date, date_end = _resolve_dates(params)
        if err := _check_date_order(date, date_end):
            return err

        pat, err = await _resolve_pat(host)
        if err:
            return err

        github_username = ((await host.secrets.get("github_username")) or "").strip()
        if not github_username:
            return PluginResult.err(
                "GitHub username not configured. "
                "Store your username via host.secrets under key 'github_username'.",
                code="auth_missing",
            )

        client = _GithubClient(pat, host)
        try:
            data, diagnostics = await client.fetch_activity(
                repo, date, date_end, github_username
            )
        except RetryableError as exc:
            cause = exc.__cause__
            if isinstance(cause, HttpRequestFailed) and cause.error_category == "rate_limited":
                return PluginResult.err(
                    "GitHub rate limit exceeded after retries.", code="rate_limited"
                )
            status = cause.status_code if isinstance(cause, HttpRequestFailed) else "unknown"
            return PluginResult.err(
                f"GitHub server error after retries: {status}", code="server_error"
            )
        except NonRetryableError as exc:
            cause = exc.__cause__
            if isinstance(cause, HttpRequestFailed):
                return _map_api_error(cause, repo)
            return PluginResult.err(str(exc), code="tool_error")
        except HttpRequestFailed as exc:
            return _map_api_error(exc, repo)

        return PluginResult.ok(data=data, diagnostics=diagnostics)
