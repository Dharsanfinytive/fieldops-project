from typing import Optional
import asyncio
import logging
from types import SimpleNamespace

from sqlalchemy.orm import Session
import re
from app.models import Job, SentimentThreadMessage, User
from app.services.ai.integrations.sentiment_integration import (
    SentimentIntegration,
)
from app.services.dispatcher_alert_service import DispatcherAlertService
from app.sentiment.retention import RetentionWorkflowService
from app.sentiment.audit import SentimentAuditLogger
from app.models.sentiment_audit import SentimentAuditRecord

from app.sentiment.escalation import (
    SentimentEscalationService,
)

logger = logging.getLogger(__name__)

class RealTimeSentimentScorer:
    """
    Coordinates real-time sentiment analysis for incoming
    customer replies.

    Uses the existing 12.1 sentiment pipeline.
    """

    def __init__(
        self,
        db: Session,
    ):
        self.db = db
        self.sentiment_integration = SentimentIntegration()
        self.audit_logger = SentimentAuditLogger(db)
        self.escalation_service = SentimentEscalationService(db)


    def _get_previous_messages(
        self,
        customer_id: int | str,
        job_id: int | None,
        source_type: str | None = None,
    ) -> list[str]:
        """
        Return the previous 3 customer messages for the
        same customer and job.
        """

        records = (
            self.db.query(SentimentThreadMessage)
            .filter(
                SentimentThreadMessage.customer_id
                == str(customer_id),
                SentimentThreadMessage.job_id == job_id,
                *([SentimentThreadMessage.source_type == source_type] if source_type else []),
            )
            .order_by(
                SentimentThreadMessage.created_at.desc()
            )
            .limit(3)
            .all()
        )

        return [
            record.message
            for record in reversed(records)
        ]

    def _get_previous_analyses(
        self,
        customer_id: int | str,
        job_id: int | None,
        tenant_id: str,
        source_type: str | None = None,
    ) -> list[SentimentThreadMessage]:
        query = self.db.query(SentimentThreadMessage).filter(
            SentimentThreadMessage.tenant_id == str(tenant_id),
            SentimentThreadMessage.customer_id == str(customer_id),
            SentimentThreadMessage.job_id == job_id,
        )
        if source_type:
            query = query.filter(SentimentThreadMessage.source_type == source_type)
        return query.order_by(SentimentThreadMessage.created_at.desc()).limit(3).all()

    async def score_reply(
        self,
        reply_text: str,
        customer_id: int | str,
        job_id: int | None,
        channel: str,
        language: Optional[str] = None,
        context: Optional[list[str]] = None,
        source_message_id: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ):
        """
        Analyze and persist an incoming customer reply.
        """

        if not reply_text or not reply_text.strip():
            return None

        language = language or self._detect_language(reply_text)

        source_type = (source_type or channel).upper()
        supported_channels = {
            "EMAIL", "SMS", "CHAT", "WHATSAPP", "PORTAL", "SUPPORT_TICKET",
            "DISPATCH_NOTE", "JOB_CHAT", "SUPPORT_CHAT", "JOB_FEEDBACK", "SUPPORT_CASE",
        }

        if channel.upper() not in supported_channels:
            raise ValueError(
                f"Unsupported sentiment channel: {channel}"
            )

        channel = channel.upper()

        if self._is_automated_reply(reply_text):
            return None

        # Resolve and verify tenant ownership before deduplication or analysis.
        job = None
        if job_id is not None:
            job_candidate = self.db.query(Job).filter(Job.id == job_id).first()
            if isinstance(job_candidate, Job):
                job = job_candidate
                allowed_tenants = {
                    str(value) for value in (job.tenant_id, getattr(job, "customer_tenant_id", None)) if value
                }
                if tenant_id and str(tenant_id) not in allowed_tenants:
                    raise ValueError("Sentiment message tenant does not own the referenced job.")
                if job.customer_id and str(job.customer_id) != str(customer_id):
                    raise ValueError("Sentiment customer is not associated with the referenced job.")
                tenant_id = str(job.tenant_id)
            elif not tenant_id:
                tenant_id = self._get_tenant_id(job_id)
        elif not tenant_id:
            raise ValueError("tenant_id is required for sentiment messages without a job.")

        existing_query = self.db.query(SentimentThreadMessage).filter(
            SentimentThreadMessage.tenant_id == str(tenant_id),
        )
        if source_message_id:
            existing_query = existing_query.filter(
                SentimentThreadMessage.source_message_id == str(source_message_id)
            )
        elif source_id:
            existing_query = existing_query.filter(
                SentimentThreadMessage.source_type == source_type,
                SentimentThreadMessage.source_id == str(source_id),
            )
        else:
            existing_query = None
        existing = existing_query.first() if existing_query is not None else None

        
        previous_records = self._get_previous_analyses(
            customer_id=customer_id,
            job_id=job_id,
            tenant_id=str(tenant_id),
            source_type=source_type if job_id is None else None,
        )
        if existing is not None:
            previous_records = [row for row in previous_records if row.id != existing.id]
        previous_messages = context if context is not None else [
            row.message for row in reversed(previous_records)
        ]

        if existing is not None:
            # A Celery retry after commit reuses the persisted analysis and
            # resumes downstream work instead of silently dropping it.
            sentiment_record = existing
            result = SimpleNamespace(
                sentiment=existing.sentiment,
                confidence=existing.confidence,
                emotion=existing.emotion,
                urgency=existing.urgency,
                severity=existing.severity,
                issue_categories=existing.issue_categories or [],
                requires_human=str(existing.requires_human).lower() in {"true", "yes", "1"},
                summary=existing.summary,
                recommended_action=existing.recommended_action,
                analysis_source=getattr(existing, "analysis_source", "AI"),
            )
        else:
            result = await asyncio.to_thread(
                self.sentiment_integration.analyze,
                message=reply_text,
                channel=channel,
                language=language,
                previous_messages=previous_messages[-3:],
                tenant_id=str(tenant_id),
            )

        if existing is None:
            sentiment_record = SentimentThreadMessage(
            tenant_id=str(tenant_id),
            customer_id=str(customer_id),
            job_id=job_id,
            channel=channel,
            message=reply_text,
            sentiment=result.sentiment,
            confidence=result.confidence,
            emotion=getattr(
                result,
                "emotion",
                None,
            ),
            urgency=getattr(
                result,
                "urgency",
                None,
            ),
            requires_human=str(
                getattr(
                    result,
                    "requires_human",
                    None,
                )
            ),
            summary=getattr(
                result,
                "summary",
                None,
            ),
            severity=getattr(result, "severity", None),
            issue_categories=getattr(result, "issue_categories", []),
            recommended_action=getattr(result, "recommended_action", None),
            language=language,
            analysis_source=getattr(result, "analysis_source", "AI"),
            source_message_id=source_message_id,
            source_type=source_type,
            source_id=str(source_id) if source_id is not None else source_message_id,
            )

            self.db.add(sentiment_record)
            self.db.commit()
            self.db.refresh(sentiment_record)


        # Keep raw customer text in the operational record only. The
        # immutable audit trail records the model/result without copying
        # unredacted message content into a second table. The record ID is
        # used as an idempotency key so a Celery retry can complete a missed
        # audit write without creating duplicates.
        audit_key = f"sentiment_message:{sentiment_record.id}"
        if isinstance(self.db, Session):
            audit_exists = self.db.query(SentimentAuditRecord.id).filter(
                SentimentAuditRecord.tenant_id == str(tenant_id),
                SentimentAuditRecord.event_type == SentimentAuditLogger.ANALYSIS,
                SentimentAuditRecord.notes == audit_key,
            ).first()
            if not audit_exists:
                self.audit_logger.log_analysis({
                    "tenant_id": str(tenant_id),
                    "customer_id": str(customer_id),
                    "job_id": job_id,
                    "input_text": None,
                    "sentiment_label": result.sentiment,
                    "confidence": result.confidence,
                    "model_used": getattr(result, "analysis_source", "AI"),
                    "notes": audit_key,
                })

        sentiment_shift = self._has_sentiment_shift(
            previous_records,
            result.sentiment,
        )
        is_safety_risk = (
            str(getattr(result, "urgency", "")).upper() == "CRITICAL"
            or str(getattr(result, "severity", "")).upper() == "CRITICAL"
            or "SAFETY" in (getattr(result, "issue_categories", []) or [])
        )
        if job and (is_safety_risk or self._should_alert_negative(result) or sentiment_shift):
            DispatcherAlertService.trigger_sentiment_alert(
                db=self.db,
                job=job,
                sentiment=result.sentiment,
                confidence=result.confidence,
                alert_reason=("critical_safety_risk" if is_safety_risk else "positive_to_negative_shift" if sentiment_shift else "negative_high_confidence"),
                urgency=getattr(result, "urgency", "LOW"),
                severity=getattr(result, "severity", "LOW"),
                issue_categories=getattr(result, "issue_categories", []),
                source_sentiment_message_id=sentiment_record.id,
            )

        escalation = self.escalation_service.create_escalation(
            message=sentiment_record,
            suggested_action=getattr(result, "recommended_action", None),
        )

        if escalation:
            notifications = await self.escalation_service.notify_manager(escalation)
            notification_queued = (
                not isinstance(notifications, dict)
                or any(
                    notifications.get(key)
                    for key in ("dashboard", "sms", "email", "push")
                )
            )
            if isinstance(notifications, dict) and not notification_queued:
                raise RuntimeError(
                    f"Sentiment escalation {escalation.id} is durable but its human alert could not be queued."
                )
            customer = (
                self.db.query(User)
                .filter(
                    User.id == str(customer_id),
                    User.tenant_id == str(getattr(job, "customer_tenant_id", None) or tenant_id),
                )
                .first()
            )
            if (
                notification_queued
                and customer
                and isinstance(getattr(customer, "phone_number", None), str)
                and customer.phone_number
                and channel not in {"JOB_CHAT", "SUPPORT_CHAT", "JOB_FEEDBACK", "SUPPORT_CASE"}
            ):
                acknowledgement_queued = await self.escalation_service.send_auto_response(
                    customer_phone=customer.phone_number,
                    escalation=escalation,
                )
                if not acknowledgement_queued:
                    logger.warning(
                        "Sentiment escalation acknowledgement was not queued: escalation_id=%s",
                        escalation.id,
                    )

        if job:
            RetentionWorkflowService(self.db).create_workflow(
                tenant_id=job.tenant_id,
                customer_id=str(customer_id),
                sentiment=result.sentiment,
                confidence=result.confidence,
                message=reply_text,
                source_sentiment_message_id=sentiment_record.id,
            )

        return sentiment_record

    @staticmethod
    def _detect_language(message: str) -> str:
        """Detect supported scripts and common Spanish cues locally."""
        if any("\u0b80" <= char <= "\u0bff" for char in message):
            return "ta"
        if any("\u0900" <= char <= "\u097f" for char in message):
            return "hi"
        words = set(re.findall(r"[a-záéíóúüñ]+", message.lower()))
        if words & {
            "gracias", "hola", "bueno", "buena", "excelente", "problema",
            "queja", "retraso", "molesto", "molesta", "decepcionado",
            "decepcionada", "funciona", "servicio", "técnico", "técnica",
        }:
            return "es"
        return "en"

    def _get_tenant_id(
        self,
        job_id: int,
    ) -> str:
        """
        Get tenant ID from the associated job.
        """

        job = (
            self.db.query(Job)
            .filter(
                Job.id == job_id
            )
            .first()
        )

        if job is None:
            raise ValueError(
                f"Job {job_id} not found."
            )

        if not job.tenant_id:
            raise ValueError(
                f"Job {job_id} does not have a tenant_id."
            )

        return job.tenant_id

    def _is_automated_reply(self, message: str) -> bool:
        """
        Detect common automated replies such as
        out-of-office and bounce messages.
        """

        automated_patterns = [
            r"\bout of office\b",
            r"\bautomatic reply\b",
            r"\bauto[- ]?reply\b",
            r"\bautoreply\b",
            r"\bmail delivery failed\b",
            r"\bdelivery failed\b",
            r"\bmessage could not be delivered\b",
            r"\baddress not found\b",
            r"\buser unknown\b",
            r"\bmailbox unavailable\b",
            r"\bdelivery status notification\b",
        ]

        return any(
            re.search(
                pattern,
                message,
                re.IGNORECASE,
            )
            for pattern in automated_patterns
        )

    def _should_alert_negative(self, result) -> bool:
        """
        Return True when sentiment is negative
        with high confidence.
        """

        return (
            result.sentiment == "NEGATIVE"
            and result.confidence > 0.8
        )

    def _has_sentiment_shift(
        self,
        previous_messages: list,
        current_sentiment: str,
    ) -> bool:
        """
        Compare the current decision with the last persisted decision.
        Prior messages are never sent through the model again.
        """

        if current_sentiment != "NEGATIVE":
            return False

        if not previous_messages:
            return False

        previous = previous_messages[0]
        previous_sentiment = getattr(previous, "sentiment", None)
        if previous_sentiment is None and isinstance(previous, str):
            previous_result = self.sentiment_integration.analyze(
                message=previous,
                channel="SMS",
                language="en",
                previous_messages=[],
            )
            previous_sentiment = getattr(previous_result, "sentiment", None)
        return str(previous_sentiment or "").upper() == "POSITIVE"

    def get_job_sentiment_summary(
        self,
        job_id: int,
    ) -> dict:
        """
        Calculate average sentiment score and trend for a job.
        """

        records = (
            self.db.query(SentimentThreadMessage)
            .filter(
                SentimentThreadMessage.job_id == job_id
            )
            .order_by(
                SentimentThreadMessage.created_at.asc()
            )
            .all()
        )

        if not records:
            return {
                "job_id": job_id,
                "average_score": 0.0,
                "trend": "NEUTRAL",
            }

        sentiment_values = {
            "POSITIVE": 1.0,
            "MIXED": 0.5,
            "NEUTRAL": 0.0,
            "NEGATIVE": -1.0,
        }

        scores = [
            sentiment_values.get(
                record.sentiment,
                0.0,
            )
            for record in records
        ]

        average_score = sum(scores) / len(scores)

        if len(scores) < 2:
            trend = "NEUTRAL"
        else:
            previous_score = scores[-2]
            latest_score = scores[-1]

            if latest_score > previous_score:
                trend = "IMPROVING"
            elif latest_score < previous_score:
                trend = "DECLINING"
            else:
                trend = "STABLE"

        return {
            "job_id": job_id,
            "average_score": round(average_score, 3),
            "trend": trend,
        }
