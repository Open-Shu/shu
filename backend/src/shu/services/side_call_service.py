"""
Side-Call Service for Shu RAG Backend.

This service provides optimized LLM calls for small, fast side-calls
like prompt assist, title generation, and UI summaries.
"""

import logging
import json
from typing import List, Optional, Dict

from ..models.llm_provider import Message
from .base_caller_service import BaseCallerService, CallerResult

# Re-export for backward compatibility
SideCallResult = CallerResult

logger = logging.getLogger(__name__)

# Constants for side-call configuration
SIDE_CALL_MODEL_SETTING_KEY = "side_call_model_config_id"


class SideCallService(BaseCallerService):
    """Service for managing optimized LLM side-calls."""

    SETTING_KEY = SIDE_CALL_MODEL_SETTING_KEY
    REQUEST_TYPE = "side_call"

    async def call(
        self,
        message_sequence: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        user_id: str = "system",
        config_overrides: Optional[Dict] = None,
        timeout_ms: Optional[int] = None,
    ) -> CallerResult:
        """
        Make an optimized side-call to the designated LLM.

        Args:
            message_sequence: Chat-style sequence of messages ({"role", "content"})
            system_prompt: Optional system prompt injected ahead of the sequence
            user_id: ID of the user making the request
            config_overrides: Optional configuration overrides for this call
            timeout_ms: Optional timeout override in milliseconds

        Returns:
            CallerResult with the response or error
        """
        return await self._call(
            message_sequence=message_sequence,
            system_prompt=system_prompt,
            user_id=user_id,
            config_overrides=config_overrides,
            timeout_ms=timeout_ms,
        )

    async def get_side_call_model(self):
        """Get the currently designated side-call model configuration."""
        return await self._get_designated_model()

    async def set_side_call_model(self, model_config_id: str, user_id: str) -> bool:
        """Set the designated side-call model configuration."""
        return await self._set_designated_model(model_config_id, user_id)

    async def clear_side_call_model(self, user_id: str) -> bool:
        """Clear the designated side-call model configuration."""
        return await self._clear_designated_model(user_id)

    async def propose_rag_query(
        self,
        current_user_query: str,
        prior_messages: Optional[List[Message]] = None,
        user_id: str = "system",
        timeout_ms: int = 1200,
    ) -> CallerResult:
        """
        Propose a retrieval-friendly RAG query using a minimal side-call.

        Sends only the current user query plus a few short prior snippets to
        preserve topic continuity without leaking full conversation context.

        Returns CallerResult where content is the rewritten query string.
        """
        try:
            raw_sequence: List[Dict[str, str]] = []
            if prior_messages:
                for msg in prior_messages[-7:-1]:
                    if not msg or not getattr(msg, "content", None):
                        continue
                    role = getattr(msg, "role", None)
                    if role not in ("user", "assistant"):
                        continue
                    raw_sequence.append({"role": role, "content": str(msg.content)})

            # TODO: Make the base prompt and the way we are sending the details to the LLM configurable.
            system_prompt = """
                You rewrite the user's message into a retrieval query for a document search engine.
                Input: (A) USER_MESSAGE the most recent user message; (B) MESSAGE_HISTORY some context on what the user and assistant talked about.
                Task: output ONLY a retrieval query that assists in returning the most relevant documents based on the user's request.

                Rules:
                    - Preserve and expand the subject (entities, IDs, synonyms, hyphen/space variants).
                    - Include prior topic if current message uses pronouns like 'it/its/they'.
                    - Remove instructions about response formatting, tone, or follow-up actions.
                    - Exclude words like 'explain', 'summarize', 'respond', 'answer', 'in detail', etc.
                    - Prefer concise tokens over sentences; avoid stop-words.
                    - Output ONLY the query (no prose, no quotes, no bullet points).
            """

            message_sequence = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"USER_MESSAGE:\n\n{current_user_query}"
                        },
                        {
                            "type": "input_text",
                            "text": f"MESSAGE_HISTORY (JSON):\n\n{json.dumps(raw_sequence)}"
                        },
                        {
                            "type": "input_text",
                            "text": "TASK: Produce the ENHANCED_SEARCH_QUERY as plain text only."
                        }
                    ]
                }
            ]

            return await self.call(
                message_sequence=message_sequence,
                system_prompt=system_prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            logger.error("propose_rag_query failed: %s", e)
            return CallerResult(content=current_user_query, success=False, error_message=str(e))

    async def distill_rag_query(
        self,
        current_user_query: str,
        user_id: str = "system",
        timeout_ms: int = 1200,
    ) -> CallerResult:
        """
        Distill a user's message down to retrieval-critical terms only.

        Removes instructions about output formatting and preserves just the entities,
        identifiers, and key subject phrases needed for document lookup.
        """
        try:
            trimmed_query = (current_user_query or "").strip()

            system_prompt = """
                You are a retrieval assistant, and are given the latest user request.
                Input: (A) USER_MESSAGE the most recent user message.
                Task: output ONLY the minimal factual query terms needed to search a knowledge base.

                Rules:
                    - Preserve entities, IDs, acronyms, and discriminating nouns.
                    - Remove instructions about response formatting, tone, or follow-up actions.
                    - Exclude words like 'explain', 'summarize', 'respond', 'answer', 'in detail', etc.
                    - Output a single plain-text line with no quotes, bullet points, or commentary.
                    - If nothing concrete remains, return an empty string.
            """

            message_sequence = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"USER_MESSAGE:\n\n{trimmed_query}",
                        },
                        {
                            "type": "input_text",
                            "text": "TASK: Produce the DISTILLED_QUERY as plain text only. Remove directives and keep only the subject matter terms.",
                        },
                    ],
                }
            ]

            return await self.call(
                message_sequence=message_sequence,
                system_prompt=system_prompt,
                user_id=user_id,
                timeout_ms=timeout_ms,
            )
        except Exception as e:
            logger.error("distill_rag_query failed: %s", e)
            return CallerResult(content=current_user_query, success=False, error_message=str(e))
