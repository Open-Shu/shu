"""
Knowledge Objects (KO) model and helpers.

This defines the canonical KO schema used by plugins and host adapters.
Implementation Status: Partial (model + ID helper)
Limitations/Known Issues: Write adapter not wired yet; see TASK-112
Security Vulnerabilities: None known; content redaction policy TBD at adapter level
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
import hashlib


class KnowledgeObject(BaseModel):
    id: Optional[str] = Field(default=None, description="Deterministic ID; computed if not provided")
    type: str = Field(..., description="Domain type, e.g., 'email', 'doc', 'thread'")
    source: Dict[str, Optional[str]] = Field(..., description="Origin info: {plugin, account?}")
    external_id: str = Field(..., description="Stable ID from the source system")
    title: Optional[str] = Field(None, description="Short title/subject")
    content: str = Field(..., description="Primary textual content for indexing")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata incl. raw payload if needed")
    permissions: Optional[Dict[str, Any]] = Field(default=None, description="RBAC/visibility controls; inherits from KB by default")
    lineage: Optional[Dict[str, Any]] = Field(default=None, description="Provenance and version info")

    class Config:
        extra = "ignore"


def deterministic_ko_id(namespace: str, external_id: str) -> str:
    """Compute a deterministic KO ID from a namespace and an external_id.

    Namespace should include plugin + account (if applicable) to avoid collisions.
    """
    h = hashlib.sha256()
    h.update(namespace.encode("utf-8"))
    h.update(b"|")
    h.update(external_id.encode("utf-8"))
    return h.hexdigest()

