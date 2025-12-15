import logging
from typing import Callable, Dict, List, Optional

from shu.models.llm_provider import Conversation
from .chat_types import ChatContext, ChatMessage

logger = logging.getLogger(__name__)

class ContextWindowManager:
    """
    Helper that encapsulates the context-window logic previously inside ChatService.
    """

    def __init__(
        self,
        llm_service,
        db_session,
        config_manager,
        *,
        recent_message_limit: int = 10,
        summary_max_tokens: int = 200,
        summary_temperature: float = 0.3,
        summary_prompt: str = (
            "Please provide a concise summary of the following conversation, focusing on key topics, "
            "decisions, and important information that would be useful for continuing the conversation:\n\n"
            "{conversation_text}\n\nSummary:"
        ),
        token_estimator: Optional[Callable[[str], int]] = None,
    ):
        self.llm_service = llm_service
        self.db_session = db_session
        self.config_manager = config_manager
        self.recent_message_limit = recent_message_limit
        self.summary_max_tokens = summary_max_tokens
        self.summary_temperature = summary_temperature
        self.summary_prompt = summary_prompt
        self._token_estimator_override = token_estimator

    async def manage_context_window(
        self,
        messages: List[ChatMessage],
        *,
        conversation: Conversation,
        max_tokens: int,
        recent_message_limit_override: Optional[int] = None,
    ) -> List[ChatMessage]:
        """
        Apply pruning/summarization to the message list.
        """

        def estimate_tokens(text: str) -> int:
            if self._token_estimator_override:
                return self._token_estimator_override(text)
            return len(text.split()) * 1.3

        total_tokens = sum(estimate_tokens(getattr(msg, "content", "")) for msg in messages)
        if total_tokens <= max_tokens:
            return messages

        logger.info(
            "Context window management needed: %s tokens > %s limit",
            total_tokens,
            max_tokens,
        )

        limit = recent_message_limit_override or self.recent_message_limit
        limit = max(1, limit)
        recent_messages = messages[-limit:]
        older_messages = messages[:-limit]

        managed_messages: List[ChatMessage] = []

        if older_messages:
            # TODO: The side caller should do this, rather than a random configured provider.
            summary = await self._summarize_conversation_history(older_messages)
            if summary:
                managed_messages.append(
                    ChatMessage(id=None, role="system", content=f"Previous conversation summary: {summary}", created_at=None, attachments=[], metadata=None)
                )

        managed_messages.extend(recent_messages)

        final_tokens = sum(estimate_tokens(getattr(msg, "content", "")) for msg in managed_messages)
        logger.info(
            "Context window managed: %s -> %s tokens",
            total_tokens,
            final_tokens,
        )

        return managed_messages

    async def _summarize_conversation_history(
        self,
        messages: List[ChatMessage],
    ) -> Optional[str]:
        if not messages:
            return None

        try:
            conversation_text = "\n".join(
                [
                    f"{getattr(msg, 'role', '').title()}: {getattr(msg, 'content', '')}"
                    for msg in messages
                ]
            )

            if "{conversation_text}" in self.summary_prompt:
                summary_prompt = self.summary_prompt.replace(
                    "{conversation_text}", conversation_text
                )
            else:
                summary_prompt = f"{self.summary_prompt}\n\n{conversation_text}\n\nSummary:"

            providers = await self.llm_service.get_active_providers()
            if not providers:
                logger.warning("No active LLM providers available for summarization")
                return None

            provider = providers[0]
            if not provider.models:
                logger.warning("No models available for summarization")
                return None

            model = next((m for m in provider.models if m.is_active), None)
            if not model:
                logger.warning("No active models available for summarization")
                return None

            client = await self.llm_service.get_client(provider.id)
            summary_params: Dict[str, float] = {
                "max_tokens": self.summary_max_tokens,
                "temperature": self.summary_temperature,
            }

            responses = await client.chat_completion(
                messages=ChatContext.from_dicts([{"role": "user", "content": summary_prompt}]),
                model=model.model_name,
                stream=False,
                model_overrides=None,
                llm_params=summary_params,
            )

            summary = (responses[-1].content or "").strip()
            logger.info("Generated conversation summary for context management")
            return summary

        except Exception as exc:
            logger.warning("Failed to summarize conversation history: %s", exc)
            return None
