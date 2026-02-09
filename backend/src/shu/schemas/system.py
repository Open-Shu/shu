from pydantic import BaseModel


class VersionInfo(BaseModel):
    version: str
    git_sha: str | None = None
    build_timestamp: str | None = None
    db_release: str | None = None
    environment: str
