"""Password reset token table (SHU-745).

One row per issued reset token. Stores only the sha256 hash of the
plaintext token; the plaintext appears only in the outbound email URL.
Single-use (`used_at`) and short-lived (`expires_at` defaults to one hour
from issue, configurable via `SHU_PASSWORD_RESET_TOKEN_TTL_SECONDS`).

Why a separate table (vs three columns on `users` like SHU-507's
verification flow):

* Reset has a *history* requirement — when a user requests a second
  reset, the older outstanding tokens are marked invalidated rather
  than overwritten, so the audit trail survives.
* Multiple outstanding tokens can briefly coexist (during the window
  between "user requested again" and "service marks older as
  superseded"), and rows let us reason about that state explicitly.

DB compromise threat model: a row leak gives the attacker hashes, never
plaintext tokens. The hot lookup is on `token_hash`; a sha256 collision
is computationally infeasible so the index is sufficient even without a
unique constraint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import TIMESTAMP

from shu.core.database import Base


class PasswordResetToken(Base):
    """One issued password reset token."""

    __tablename__ = "password_reset_token"

    __table_args__ = (
        Index("ix_password_reset_token_token_hash", "token_hash"),
        # "Find all outstanding tokens for this user" — used by the
        # invalidate-others sweep on a fresh request and on each successful
        # reset. Includes used_at so the partial scan stays selective.
        Index("ix_password_reset_token_user_id_used_at", "user_id", "used_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # sha256 hex of the plaintext token. Plaintext is never persisted —
    # it only ever appears in the outbound email URL.
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    # Set on successful reset. NULL = still outstanding (subject to expiry).
    # Tokens invalidated by a newer request also get used_at set so the
    # sweep does not have to distinguish "consumed" from "superseded" —
    # both are terminal.
    used_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # Best-effort attribution of who requested this reset. Logged for
    # incident response; not a security primitive.
    created_ip = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
