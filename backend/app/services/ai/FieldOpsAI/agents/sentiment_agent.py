"""
sentiment_agent.py

Sentiment Analysis Agent for FieldOps Commander AI,
migrated to inherit from BaseAgent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.ai.FieldOpsAI.agents.base import BaseAgent
from app.services.ai.FieldOpsAI.agents.sentiment_engine import SentimentEngine
from app.services.ai.FieldOpsAI.runtime.orchestrator import (
    AIOrchestrator,
    ai_orchestrator,
)
from app.services.ai.FieldOpsAI.schemas.agent_config import AgentConfig
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.sentiment import (
    SentimentContext,
    SentimentDecision,
)

logger = logging.getLogger(__name__)


class SentimentAgent(BaseAgent[SentimentDecision]):
    """
    AI agent responsible for customer sentiment analysis.
    """

    def __init__(
        self,
        config: AgentConfig,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        """
        Initialize the Sentiment Agent.
        """

        if config.agent_type != AITask.SENTIMENT:
            raise ValueError(
                "SentimentAgent requires an AITask.SENTIMENT configuration."
            )

        super().__init__(config)

        self.orchestrator = (
            ai_orchestrator
            if orchestrator is None
            else orchestrator
        )
        self.engine = SentimentEngine(orchestrator=self.orchestrator)

    async def run(
        self,
        context: dict[str, Any],
    ) -> SentimentDecision:
        """
        Execute the AI sentiment analysis task.

        The synchronous AIOrchestrator.execute call is moved to a
        worker thread so the async event loop is not blocked.
        """

        start_time = time.perf_counter()

        logger.info("Sentiment Agent run started.")

        sentiment_context = SentimentContext.model_validate(context)

        decision = await asyncio.to_thread(self.engine.analyze, sentiment_context)

        elapsed = time.perf_counter() - start_time

        logger.info(
            "Sentiment completed in %.2f sec | Sentiment=%s | Urgency=%s",
            elapsed,
            decision.sentiment,
            decision.urgency,
        )

        return decision

    def analyze(
        self,
        context: SentimentContext,
    ) -> SentimentDecision:
        """
        Compatibility adapter for legacy synchronous callers.

        This keeps the existing analyze() API working while the
        internal execution path uses BaseAgent.execute().
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "analyze() cannot be called from an active event loop. "
                "Use the asynchronous AgentLifecycle / execute path instead."
            )

        exec_context = context.model_dump(mode="json")
        exec_context["tenant_id"] = self.tenant_id

        async def _run_wrapped():
            if not self.is_setup:
                await self.setup()

            return await self.execute(exec_context)

        return asyncio.run(_run_wrapped())
