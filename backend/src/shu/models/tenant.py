"""Tenant catalog model.

Global catalog of tenant identifiers. The control plane owns provisioning;
the app reads this table but never writes to it during normal request
handling. RLS is intentionally NOT enabled here — this is global
infrastructure that every tenant-scoped FK references.
"""

from datetime import datetime

from sqlalchemy import Uuid, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from shu.core.database import Base


# Inherits Base directly rather than BaseModel because:
#   - no updated_at: tenant rows are immutable once provisioned by the CP
#   - no auto-uuid default on id: the CP supplies the id externally; auto-
#     generating one would silently mask bugs where the caller forgot to pass it
#   - created_at uses a server-side default so the data-migration insert
#     (raw SQL, no Python-side default fires) still gets a timestamp
class Tenant(Base):
    """Global catalog of tenant identifiers.

    Intentionally minimal — only an id and a creation timestamp. Provisioning
    is owned by the control plane; the app reads this table but never writes
    to it during normal request handling.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
