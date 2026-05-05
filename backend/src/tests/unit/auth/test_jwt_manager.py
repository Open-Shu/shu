"""Unit tests for jwt_manager helpers (SHU-745)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from shu.auth.jwt_manager import is_token_revoked_by_password_change


class TestIsTokenRevokedByPasswordChange:
    """SHU-745 session-invalidation gate: a JWT's `iat` is rejected when
    it predates the user's most recent password change. Used by both the
    JWT auth middleware (access tokens) and the /auth/refresh endpoint
    (refresh tokens).
    """

    def test_no_password_change_means_no_invalidation(self) -> None:
        # Existing accounts that never reset have password_changed_at=None;
        # all their tokens stay valid until natural expiry.
        iat = int(datetime.now(UTC).timestamp())
        assert is_token_revoked_by_password_change(iat, None) is False

    def test_no_iat_means_no_invalidation(self) -> None:
        # A token without an iat claim (legacy or hand-crafted) is left
        # alone by this gate; other auth checks handle malformed tokens.
        password_changed_at = datetime.now(UTC)
        assert is_token_revoked_by_password_change(None, password_changed_at) is False

    def test_token_issued_well_before_reset_is_revoked(self) -> None:
        password_changed_at = datetime.now(UTC)
        old_iat = int((password_changed_at - timedelta(hours=2)).timestamp())
        assert is_token_revoked_by_password_change(old_iat, password_changed_at) is True

    def test_token_issued_well_after_reset_is_not_revoked(self) -> None:
        password_changed_at = datetime.now(UTC) - timedelta(minutes=10)
        new_iat = int(datetime.now(UTC).timestamp())
        assert is_token_revoked_by_password_change(new_iat, password_changed_at) is False

    def test_token_iat_in_same_wall_clock_second_as_reset_is_not_revoked(self) -> None:
        """Regression: JWT iat is integer seconds, password_changed_at is
        microsecond-precision. Without flooring both sides, a token issued
        in the same second as the reset (iat second == floor of
        password_changed_at) compares as strictly less and produces a
        false-positive 401 — the user's freshly issued token bounces on
        its first request. The helper floors before comparing, so a
        same-second iat is allowed through.
        """
        # A reset that happens 500ms into the wall-clock second.
        same_second = datetime(2026, 5, 5, 12, 0, 0, 500000, tzinfo=UTC)
        # A token issued during that same wall-clock second has iat
        # rounded down to second boundary (12:00:00.000) — that's how
        # python-jose serialises datetimes into JWT claims.
        iat_at_second_boundary = int(same_second.replace(microsecond=0).timestamp())
        assert (
            is_token_revoked_by_password_change(iat_at_second_boundary, same_second) is False
        )

    def test_naive_password_changed_at_is_treated_as_utc(self) -> None:
        """Defensive: SQLite drops timezone info on read. The helper
        normalises a naive datetime to UTC before comparing so the gate
        behaves consistently across drivers.
        """
        # Build the naive datetime by stripping tzinfo from a tz-aware
        # one — keeps the value identical, just without the tzinfo flag,
        # which is what the SQLite driver hands back from a TIMESTAMP
        # column. (Constructing `datetime(...)` directly without tzinfo
        # would also work but trips DTZ001.)
        naive = datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        old_iat = int(datetime(2026, 5, 5, 11, 0, 0, tzinfo=UTC).timestamp())
        assert is_token_revoked_by_password_change(old_iat, naive) is True

    def test_token_one_second_before_reset_is_revoked(self) -> None:
        password_changed_at = datetime(2026, 5, 5, 12, 0, 0, 500000, tzinfo=UTC)
        # Token from the previous second (11:59:59) is genuinely older.
        old_iat = int(datetime(2026, 5, 5, 11, 59, 59, tzinfo=UTC).timestamp())
        assert is_token_revoked_by_password_change(old_iat, password_changed_at) is True
