"""Plugin Registry v0 model(s) for Agent Foundation MVP.

Minimal DB-backed registry of plugin with versioning and schema hashes.
This is intentionally simple and will be extended in EPIC-PLUGIN-ECOSYSTEM.
"""

from sqlalchemy import JSON, Boolean, Column, String, UniqueConstraint

from .base import BaseModel


class PluginDefinition(BaseModel):
    """Minimal plugin definition for registry v0.

    Fields:
    - name: human-friendly and programmatic identifier (e.g., "gmail_digest")
    - version: semantic-ish tag (e.g., "v0")
    - enabled: whether calls are allowed
    - schema_hash: hash of input schema (and optionally output) for audit
    - input_schema: JSON schema for inputs (optional)
    - output_schema: JSON schema for outputs (optional)
    - created_by: user id (string) that registered this plugin (optional)
    """

    __tablename__ = "plugin_definitions"

    name = Column(String(100), nullable=False, index=True)
    version = Column(String(50), nullable=False, default="v0")
    enabled = Column(Boolean, nullable=False, default=True, index=True)

    schema_hash = Column(String(64), nullable=True)
    input_schema = Column(JSON, nullable=True)
    output_schema = Column(JSON, nullable=True)

    # Optional per-plugin overrides for rate limits and quotas
    limits = Column(JSON, nullable=True)

    created_by = Column(String(36), nullable=True, index=True)

    __table_args__ = (UniqueConstraint("name", "version", name="uq_plugin_name_version"),)

    def __repr__(self) -> str:
        return f"<PluginDefinition(name={self.name}, version={self.version}, enabled={self.enabled})>"
