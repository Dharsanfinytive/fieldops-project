from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.retention import (
    RetentionCRMTask,
    RetentionDiscountCode,
    RetentionServiceCredit,
    RetentionWorkflow,
)


class RetentionWorkflowService:
    CHURN_KEYWORDS = (
        "cancel subscription",
        "switching to competitor",
        "never using again",
    )

    def __init__(self, db: Session, *, notifier: Callable[..., Any] | None = None):
        self.db = db
        self.notifier = notifier

    @classmethod
    def detect_trigger(cls, sentiment: str | None, confidence: float | None, message: str | None) -> dict[str, Any] | None:
        text = (message or "").lower()
        keyword = next((item for item in cls.CHURN_KEYWORDS if item in text), None)
        negative = (sentiment or "").upper() == "NEGATIVE" and (confidence or 0) > 0.7
        if not negative and not keyword:
            return None
        return {
            "trigger_type": "churn_keyword" if keyword else "negative_sentiment",
            "trigger": keyword or "negative_sentiment",
        }

    @staticmethod
    def branch_workflow(confidence: float | None, customer_lifetime_value: float = 0) -> dict[str, str]:
        confidence = confidence or 0
        severity = "critical" if confidence >= 0.9 else "high" if confidence >= 0.8 else "moderate"
        branch = "high_value_escalation" if customer_lifetime_value >= 1000 else "standard_recovery"
        return {"severity": severity, "branch": branch}

    def create_workflow(self, *, tenant_id: str, customer_id: str, sentiment: str | None = None,
                        confidence: float | None = None, message: str | None = None,
                        customer_lifetime_value: float = 0,
                        source_sentiment_message_id: int | None = None) -> RetentionWorkflow | None:
        if source_sentiment_message_id is not None:
            existing = self.db.query(RetentionWorkflow).filter(
                RetentionWorkflow.tenant_id == tenant_id,
                RetentionWorkflow.source_sentiment_message_id == source_sentiment_message_id,
            ).first()
            if existing:
                return existing
        trigger = self.detect_trigger(sentiment, confidence, message)
        if trigger is None:
            return None
        branch = self.branch_workflow(confidence, customer_lifetime_value)
        workflow = RetentionWorkflow(
            tenant_id=tenant_id, customer_id=customer_id, sentiment=sentiment,
            source_sentiment_message_id=source_sentiment_message_id,
            confidence=confidence, message=message, customer_lifetime_value=customer_lifetime_value,
            trigger_type=trigger["trigger_type"], **branch,
        )
        self.db.add(workflow)
        self.db.commit()
        self.db.refresh(workflow)
        return workflow

    def generate_discount_code(self, workflow: RetentionWorkflow, percentage: int | None = None) -> RetentionDiscountCode:
        percentage = percentage or (20 if workflow.severity == "critical" else 15 if workflow.severity == "high" else 10)
        percentage = max(10, min(20, percentage))
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "SAVE-" + "".join(secrets.choice(alphabet) for _ in range(10))
            if self.db.get(RetentionDiscountCode, code) is None:
                break
        discount = RetentionDiscountCode(
            code=code, workflow_id=workflow.id, tenant_id=workflow.tenant_id,
            customer_id=workflow.customer_id, percentage=percentage,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7), usage_limit=1,
        )
        self.db.add(discount)
        return discount

    def create_call_task(self, workflow: RetentionWorkflow) -> RetentionCRMTask:
        task = RetentionCRMTask(
            workflow_id=workflow.id, tenant_id=workflow.tenant_id,
            customer_id=workflow.customer_id,
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        self.db.add(task)
        return task

    def apply_service_credit(self, workflow: RetentionWorkflow, amount: float | None = None) -> RetentionServiceCredit:
        credit = RetentionServiceCredit(
            workflow_id=workflow.id, tenant_id=workflow.tenant_id,
            customer_id=workflow.customer_id, amount=amount or (100.0 if workflow.branch == "high_value_escalation" else 25.0),
            reason="Sentiment retention recovery",
        )
        self.db.add(credit)
        return credit

    def assign_specialist(self, workflow: RetentionWorkflow) -> str:
        return "retention-specialist" if workflow.branch == "high_value_escalation" else "retention-team"

    def execute(self, workflow: RetentionWorkflow) -> dict[str, Any]:
        discount = self.generate_discount_code(workflow)
        task = self.create_call_task(workflow)
        credit = self.apply_service_credit(workflow)
        specialist = self.assign_specialist(workflow)
        actions = ["apology_message", "discount", "follow_up_call", "service_credit", "specialist_assignment"]
        if self.notifier:
            self.notifier(workflow=workflow, discount_code=discount.code)
        workflow.actions = actions
        workflow.status = "executed"
        self.db.commit()
        return {"workflow_id": workflow.id, "actions": actions, "discount_code": discount.code,
                "crm_task_id": task.id, "credit_id": credit.id, "specialist": specialist}

    def track_outcome(self, workflow_id: str, retained: bool) -> RetentionWorkflow:
        workflow = self.db.get(RetentionWorkflow, workflow_id)
        if workflow is None:
            raise ValueError("Retention workflow not found")
        workflow.outcome = "retained" if retained else "churned"
        self.db.commit()
        self.db.refresh(workflow)
        return workflow

    def success_rate(self, tenant_id: str | None = None) -> float:
        query = self.db.query(RetentionWorkflow).filter(RetentionWorkflow.outcome.isnot(None))
        if tenant_id:
            query = query.filter(RetentionWorkflow.tenant_id == tenant_id)
        outcomes = query.all()
        return sum(item.outcome == "retained" for item in outcomes) / len(outcomes) if outcomes else 0.0
