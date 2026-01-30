from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_db_release(session: AsyncSession, expected: str | None) -> dict[str, Any]:
    """Check runtime Alembic DB version against an expected release string.

    Returns a dict with keys:
      - ok: bool
      - expected: Optional[str]
      - current: Optional[str]
      - mismatch: bool
      - error: Optional[str]
    """
    result: dict[str, Any] = {
        "ok": True,
        "expected": expected,
        "current": None,
        "mismatch": False,
        "error": None,
    }

    if not expected:
        return result

    try:
        current = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        result["current"] = current
        if current != expected:
            result["ok"] = False
            result["mismatch"] = True
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    return result
