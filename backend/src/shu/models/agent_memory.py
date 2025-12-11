"""
Agent Memory v0 model for Agent Foundation MVP.

Minimal per-user, per-agent scoped key/value store.
"""

from sqlalchemy import Column, String, JSON, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship

from .base import BaseModel


class AgentMemory(BaseModel):
    """Key/value memory scoped to (user_id, agent_key, key).

    - user_id: FK to users.id
    - agent_key: string identifier for the agent (e.g., "morning_briefing")
    - key: arbitrary key name within the agent's namespace
    - value: JSON payload
    """

    __tablename__ = "agent_memory"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_key = Column(String(100), nullable=False, index=True)
    key = Column(String(200), nullable=False, index=True)

    value = Column(JSON, nullable=True)

    # Index uniqueness across the scope
    __table_args__ = (
        UniqueConstraint("user_id", "agent_key", "key", name="uq_agent_memory_scope_key"),
    )

    # Optional relationship for convenience
    user = relationship("User", backref="agent_memory_entries")

    def __repr__(self) -> str:
        return f"<AgentMemory(user_id={self.user_id}, agent_key={self.agent_key}, key={self.key})>"

