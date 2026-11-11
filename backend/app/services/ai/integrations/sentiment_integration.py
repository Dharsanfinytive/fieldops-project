"""
sentiment_integration.py

Integration layer between the backend
communication workflow and the AI Sentiment Service.
"""

import logging

from app.services.ai.FieldOpsAI.services.sentiment_service import (
    SentimentService,
)
from app.services.ai.FieldOpsAI.schemas.sentiment import SentimentDecision
from app.services.ai.FieldOpsAI.schemas.sentiment import SentimentContext
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

logger = logging.getLogger(__name__)


class SentimentIntegration:
    """
    Adapter for customer sentiment analysis.
    """

    def __init__(
        self,
        service: SentimentService | None = None,
    ) -> None:
        # The production path creates a tenant-scoped SentimentAgent from the
        # runtime registry. The service remains injectable for focused callers.
        self.service = service

    def analyze(
        self,
        message: str,
        channel: str,
        language: str = "en",
        previous_messages: list[str] | None = None,
        tenant_id: str | None = None,
    ) -> SentimentDecision:
        """
        Analyze customer sentiment.
        """

        if self.service is not None:
            return self.service.analyze_customer_message(
                message=message,
                language=language,
                previous_messages=previous_messages,
                channel=channel,
            )

        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("A tenant_id is required for SentimentAgent execution.")

        from app.services.ai.FieldOpsAI.config.agent_config_manager import AgentConfigManager
        from app.services.ai.FieldOpsAI.runtime.agent_registry import create_default_agent_registry

        context = SentimentContext(
            message=message,
            channel=channel,
            language=language,
            previous_messages=(previous_messages or [])[-3:],
        )
        try:
            registry = create_default_agent_registry()
            agent = registry.create(
                agent_type=AITask.SENTIMENT,
                tenant_id=str(tenant_id),
                config_manager=AgentConfigManager(),
            )
            return agent.analyze(context)
        except Exception:
            # Registry/configuration failure must not disable deterministic
            # safety classification for a customer message.
            logger.exception("SentimentAgent runtime unavailable; using the validated engine fallback")
            return SentimentService().engine._keyword_fallback(context)
