from datetime import datetime, timedelta, timezone
import logging
import re


from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.sentiment import SentimentThreadMessage
from app.models.sentiment_escalation import SentimentEscalation
from app.models.user import User
from app.models import Job
from app.sentiment.audit import SentimentAuditLogger

logger = logging.getLogger(__name__)


class SentimentEscalationService:
    """
    Handles negative-sentiment escalation detection,
    manager assignment, suppression, and escalation records.
    """

    COMPLAINT_KEYWORDS = {
        "terrible",
        "worst",
        "never again",
        "cancel",
        "refund",
    }

    ACKNOWLEDGE_SLA_MINUTES = 15
    RESOLVE_SLA_HOURS = 2
    SUPPRESSION_MINUTES = 30

    def __init__(self, db: Session):
        self.db = db
        self.audit_logger = SentimentAuditLogger(db)

    def detect_triggers(
        self,
        message: SentimentThreadMessage,
    ) -> list[str]:
        """
        Detect all escalation triggers for a customer message.
        """

        triggers: list[str] = []

        issue_categories = message.issue_categories or []
        if (
            str(message.urgency or "").upper() == "CRITICAL"
            or str(message.severity or "").upper() == "CRITICAL"
            or "SAFETY" in issue_categories
        ):
            triggers.append("CRITICAL_SAFETY_RISK")

        if (
            str(message.sentiment or "").upper() == "NEGATIVE"
            and str(message.urgency or "").upper() == "HIGH"
        ):
            triggers.append("HIGH_URGENCY_NEGATIVE")

        if str(message.severity or "").upper() == "HIGH":
            triggers.append("HIGH_SEVERITY_REVIEW")

        # 1. Negative sentiment with confidence > 0.8
        if (
            message.sentiment.upper() == "NEGATIVE"
            and message.confidence > 0.8
        ):
            triggers.append("NEGATIVE_SENTIMENT")

        # 2. Explicit complaint keywords
        message_text = message.message.lower()

        if any(
            re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", message_text)
            for keyword in self.COMPLAINT_KEYWORDS
        ):
            triggers.append("COMPLAINT_KEYWORD")

        # 3. Two or more negative replies in the same job/thread
        negative_count = 0
        if message.job_id is not None:
            negative_count = (
                self.db.query(SentimentThreadMessage)
                .filter(
                    and_(
                        SentimentThreadMessage.job_id == message.job_id,
                        SentimentThreadMessage.tenant_id == message.tenant_id,
                        SentimentThreadMessage.customer_id == message.customer_id,
                        SentimentThreadMessage.sentiment.ilike("NEGATIVE"),
                    )
                )
                .count()
            )

        if negative_count >= 2 or "REPEATED_COMPLAINT" in issue_categories:
            triggers.append("REPEATED_NEGATIVE")

        # 4. Customer explicitly requests human/agent
        requires_human = str(
            message.requires_human or ""
        ).lower()

        human_request = any(
            re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", message_text)
            for phrase in (
                "human",
                "agent",
                "speak to someone",
                "talk to someone",
                "manager",
            )
        )

        if requires_human in {"true", "yes", "1"} or human_request:
            triggers.append("HUMAN_REQUEST")

        return triggers

    def is_suppressed(self, job_id: int | None, customer_id: str | None = None) -> bool:
        """
        Prevent re-escalation for the same job within 30 minutes.
        """

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=self.SUPPRESSION_MINUTES
        )

        filters = [
            SentimentEscalation.job_id == job_id,
            SentimentEscalation.created_at >= cutoff,
        ]
        if customer_id is not None:
            filters.append(SentimentEscalation.customer_id == customer_id)
        existing = self.db.query(SentimentEscalation.id).filter(*filters).first()

        return existing is not None

    def assign_manager(self, tenant_id: str) -> User | None:
        """
        Assign a tenant-scoped dispatcher or organization admin using round-robin order.
        """

        owners = (
            self.db.query(User)
            .filter(
                User.tenant_id == tenant_id,
                func.lower(User.role).in_(("dispatcher", "super_admin")),
                User.is_active.is_(True),
                User.is_on_duty.is_(True),
            )
            .order_by(User.id)
            .all()
        )

        if not owners:
            return None

        # Use the most recently assigned manager for this tenant.
        last_assignment = (
            self.db.query(SentimentEscalation)
            .filter(
                SentimentEscalation.tenant_id == tenant_id,
                SentimentEscalation.assigned_manager_id.isnot(None),
            )
            .order_by(SentimentEscalation.created_at.desc())
            .first()
        )

        if not last_assignment:
            return owners[0]

        manager_ids = [manager.id for manager in owners]

        try:
            last_index = manager_ids.index(
                last_assignment.assigned_manager_id
            )
        except ValueError:
            return owners[0]

        next_index = (last_index + 1) % len(owners)

        return owners[next_index]

    def get_manager_contact(self, manager: User) -> dict:
        """
        Get manager contact details for escalation notifications.
        """

        return {
            "manager_id": manager.id,
            "email": manager.email,
            "phone_number": manager.phone_number,
        }

    def build_escalation_payload(
        self,
        escalation: SentimentEscalation,
    ) -> dict:
        """
        Build the notification payload for the assigned manager.
        """

        return {
            "escalation_id": escalation.id,
            "customer_name": escalation.customer_name,
            "job_id": escalation.job_id,
            "sentiment_score": escalation.sentiment_score,
            "sentiment_label": escalation.sentiment_label,
            "reply_text": escalation.reply_text,
            "technician_name": escalation.technician_name,
            "trigger_reason": escalation.trigger_reason,
            "suggested_action": escalation.suggested_action,
            "status": escalation.status,
            "acknowledge_deadline": escalation.acknowledge_deadline,
            "resolve_deadline": escalation.resolve_deadline,
        }

    def check_sla_breach(
        self,
        escalation: SentimentEscalation,
    ) -> str | None:
        """
        Check whether an open escalation has breached
        its acknowledgement or resolution SLA.
        """

        now = datetime.now(timezone.utc)

        if escalation.status == "OPEN":
            if now >= escalation.resolve_deadline:
                return "RESOLVE_SLA_BREACHED"

            if now >= escalation.acknowledge_deadline:
                return "ACKNOWLEDGE_SLA_BREACHED"

        return None

    def create_escalation(
        self,
        message: SentimentThreadMessage,
        technician_name: str | None = None,
        suggested_action: str | None = None,
    ) -> SentimentEscalation | None:
        """
        Create an escalation record when a message requires escalation.
        """

        triggers = self.detect_triggers(message)

        if not triggers:
            return None

        message_id = getattr(message, "id", None)
        if isinstance(message_id, (int, str)) and message_id:
            existing = (
                self.db.query(SentimentEscalation)
                .filter(
                    SentimentEscalation.source_sentiment_message_id == message_id
                )
                .first()
            )
            if isinstance(existing, SentimentEscalation):
                return existing

        if "CRITICAL_SAFETY_RISK" not in triggers and self.is_suppressed(message.job_id, message.customer_id):
            return None

        manager = self.assign_manager(message.tenant_id)
        job = (
            self.db.query(Job)
            .filter(Job.id == message.job_id, Job.tenant_id == message.tenant_id)
            .first()
            if message.job_id is not None and isinstance(message_id, (int, str)) and message_id
            else None
        )
        customer_tenant_id = str(
            getattr(job, "customer_tenant_id", None) or message.tenant_id
        )
        customer = (
            self.db.query(User)
            .filter(
                User.id == message.customer_id,
                User.tenant_id == customer_tenant_id,
            )
            .first()
        )

        customer_name = (
    f"{customer.first_name} {customer.last_name}"
    if customer
    else "Unknown"
)
        customer_phone = (
    customer.phone_number
    if customer
    else None
)

        now = datetime.now(timezone.utc)

        escalation = SentimentEscalation(
            tenant_id=message.tenant_id,
            job_id=message.job_id,
            customer_id=message.customer_id,
            source_sentiment_message_id=message.id,
            customer_name=customer_name,
            technician_name=technician_name,
            reply_text=message.message,
            sentiment_label=message.sentiment,
            sentiment_score=message.confidence,
            trigger_reason=",".join(triggers),
            suggested_action=suggested_action,
            assigned_manager_id=manager.id if manager else None,
            status="OPEN",
            created_at=now,
            acknowledge_deadline=(
                now + timedelta(
                    minutes=self.ACKNOWLEDGE_SLA_MINUTES
                )
            ),
            resolve_deadline=(
                now + timedelta(
                    hours=self.RESOLVE_SLA_HOURS
                )
            ),
            updated_at=now,
        )

        self.db.add(escalation)
        self.db.flush()

        self.audit_logger.log_escalation(
            {
                "tenant_id": message.tenant_id,
                "customer_id": message.customer_id,
                "job_id": message.job_id,
                "manager_id": (
                    manager.id if manager else None
                ),
                "trigger_reason": ",".join(triggers),
            }
        )

        self.db.commit()
        self.db.refresh(escalation)

        return escalation

    def generate_auto_response(self) -> str:
        return (
            "Your concern has been escalated to a manager "
            "who will contact you within 15 minutes"
        )

    async def send_auto_response(
        self,
        customer_phone: str | None,
        escalation: SentimentEscalation | None = None,
    ) -> bool:
        if not customer_phone:
            return False

        try:
            from app.services.twilio_sms import dispatch_twilio_message

            response_text = self.generate_auto_response()
            if escalation is not None:
                try:
                    response_text = self._generate_communication_agent_response(
                        escalation
                    )
                except Exception:
                    # A safe, factual response keeps escalation acknowledgements
                    # available if AI configuration or guardrail auditing fails.
                    logger.exception(
                        "Communication Agent unavailable for sentiment escalation %s",
                        escalation.id,
                    )
                    response_text = (
                        "Your message has been sent to our support team. "
                        "We will follow up as soon as possible."
                    )

                from app.services.communication_delivery import enqueue_tracked_sms

                delivery = enqueue_tracked_sms(
                    self.db,
                    tenant_id=str(escalation.tenant_id),
                    recipient_id=str(escalation.customer_id),
                    recipient_role="customer",
                    to_number=customer_phone,
                    body=response_text,
                    source_type="SENTIMENT_ESCALATION_ACK",
                    source_id=str(escalation.id),
                    job_id=escalation.job_id,
                    title="We are reviewing your concern",
                )
                return bool(
                    delivery.get("queued")
                    or delivery.get("status")
                    in {"queued", "sending", "accepted", "sent", "delivered"}
                )

            await dispatch_twilio_message(
                body=response_text,
                to_phone=customer_phone,
            )

            return True

        except Exception:
            logger.exception(
                "Failed to send escalation auto-response."
            )
            return False

    def _generate_communication_agent_response(
        self,
        escalation: SentimentEscalation,
    ) -> str:
        """Ask Communication Agent for a grounded escalation acknowledgement."""
        from app.models import Job
        from app.services.agent_communication import AgentCommunicationCoordinator
        from app.services.ai.FieldOpsAI.schemas.communication import (
            AgentCommunicationRequest,
            CommunicationContext,
            CommunicationRecipient,
        )

        job = (
            self.db.query(Job)
            .filter(
                Job.id == escalation.job_id,
                Job.tenant_id == escalation.tenant_id,
            )
            .first()
            if escalation.job_id is not None
            else None
        )
        customer_tenant_id = str(
            getattr(job, "customer_tenant_id", None) or escalation.tenant_id
        )
        customer = (
            self.db.query(User)
            .filter(
                User.id == str(escalation.customer_id),
                User.tenant_id == customer_tenant_id,
            )
            .first()
        )
        allowed_statuses = {
            "CREATED", "ASSIGNED", "EN_ROUTE", "ON_SITE",
            "WORK_IN_PROGRESS", "COMPLETED", "CANCELLED",
        }
        status = str(getattr(job, "status", "CREATED") or "CREATED").upper()
        if status == "IN_PROGRESS":
            status = "WORK_IN_PROGRESS"
        if status not in allowed_statuses:
            status = "CREATED"

        request_id = f"sentiment-escalation-{escalation.id}"
        manager_context = (
            "An authorized tenant owner has been assigned to review this concern."
            if escalation.assigned_manager_id
            else "No individual owner is assigned yet. Keep the concern in the tenant escalation queue and do not claim that a team has been notified."
        )
        context = CommunicationContext(
            job_id=str(escalation.job_id or f"support-{escalation.id}"),
            correlation_id=request_id,
            notification_type="sentiment_escalation",
            recipient_type=CommunicationRecipient.CUSTOMER,
            channel="SMS",
            locale="en",
            customer_id=str(escalation.customer_id),
            customer_name=(
                f"{customer.first_name} {customer.last_name}".strip()
                if customer
                else None
            ),
            job_status=status,
            job_title=job.service_type if job else None,
            sentiment=(
                escalation.sentiment_label
                if escalation.sentiment_label in {"POSITIVE", "NEUTRAL", "NEGATIVE"}
                else "NEGATIVE"
            ),
            additional_context=(
                f"The customer's concern was escalated for human review. "
                f"{manager_context} Acknowledge the concern empathetically. "
                "Do not promise a specific resolution or response time."
            ),
        )
        request = AgentCommunicationRequest(
            requester_agent="SENTIMENT_AGENT",
            request_id=request_id,
            context=context,
        )
        result = AgentCommunicationCoordinator(
            db=self.db,
            tenant_id=str(escalation.tenant_id),
        ).generate(request)
        decision = result.decision
        if decision.channel != "SMS" or decision.output.channel != "SMS":
            raise ValueError("Communication Agent returned a non-SMS response.")
        return decision.output.text

    async def notify_manager(
        self,
        escalation: SentimentEscalation,
    ) -> dict:
        """
        Send escalation notifications to the assigned manager.

        Channels:
        - SMS
        - Email
        - Dashboard

        Push is currently not sent because the existing FCM
        helper is designed around technician notification flow.
        """

        # --------------------------------------------------
        # 1. Find assigned manager
        # --------------------------------------------------

        if not escalation.assigned_manager_id:
            result = {
                "manager_id": None,
                "sms": False,
                "email": False,
                "push": False,
                "dashboard": False,
            }
            return result

        manager = (
            self.db.query(User)
            .filter(
                User.id == escalation.assigned_manager_id,
                User.tenant_id == escalation.tenant_id,
                User.is_active.is_(True),
                func.lower(User.role).in_(("dispatcher", "super_admin")),
            )
            .first()
        )

        if not manager:
            return {
                "manager_id": None,
                "sms": False,
                "email": False,
                "push": False,
                "dashboard": False,
            }

        if (
            isinstance(getattr(manager, "tenant_id", None), str)
            and manager.tenant_id
            and manager.tenant_id != str(escalation.tenant_id)
        ):
            logger.error("Refusing cross-tenant sentiment escalation owner delivery: escalation_id=%s", escalation.id)
            return {
                "manager_id": None,
                "sms": False,
                "email": False,
                "push": False,
                "dashboard": False,
            }

        payload = self.build_escalation_payload(
            escalation
        )

        result = {
            "manager_id": manager.id,
            "manager_email": manager.email,
            "manager_phone": manager.phone_number,
            "payload": payload,
            "sms": False,
            "email": False,
            "push": False,
            "dashboard": False,
        }

        # --------------------------------------------------
        # 2. Notification message
        # --------------------------------------------------

        subject = "FieldOps Escalation: " + (f"Job #{escalation.job_id}" if escalation.job_id else "Customer support chat")

        message = (
            "Customer escalation requires your attention.\n\n"
            f"Job ID: {escalation.job_id or 'Customer support chat'}\n"
            f"Customer: {escalation.customer_name}\n"
            f"Sentiment: {escalation.sentiment_label}\n"
            f"Confidence: {escalation.sentiment_score}\n"
            f"Trigger: {escalation.trigger_reason}\n"
            f"Reply: {escalation.reply_text}\n\n"
            "Please acknowledge this escalation "
            "within 15 minutes."
        )

        # Channel requests are persisted before Celery delivery. Stable source
        # identity makes retries safe and exposes delivery state in tracking.
        from html import escape
        from app.services.communication_delivery import enqueue_tracked_recipient_delivery

        sms_body = f"FieldOps concern #{escalation.id} needs your review. Please check the dashboard."
        plain_text = (
            f"Customer escalation requires your attention.\n\n{message}"
        )
        body_html = (
            "<h2>FieldOps Escalation</h2>"
            f"<p><strong>Job ID:</strong> {escape(str(escalation.job_id or 'Customer support chat'))}</p>"
            f"<p><strong>Customer:</strong> {escape(str(escalation.customer_name or 'Customer'))}</p>"
            f"<p><strong>Sentiment:</strong> {escape(str(escalation.sentiment_label or 'Unknown'))}</p>"
            f"<p><strong>Confidence:</strong> {escape(str(escalation.sentiment_score))}</p>"
            f"<p><strong>Trigger:</strong> {escape(str(escalation.trigger_reason or 'Review required'))}</p>"
            f"<p><strong>Customer Reply:</strong> {escape(str(escalation.reply_text or ''))}</p>"
            "<p>Please acknowledge this escalation within 15 minutes.</p>"
        )

        if not isinstance(self.db, Session):
            # Keep the notification contract usable with lightweight
            # repository doubles; production sessions use the tracked outbox.
            if manager.phone_number:
                try:
                    from app.services.twilio_sms import dispatch_twilio_message
                    await dispatch_twilio_message(body=sms_body, to_phone=manager.phone_number)
                    result["sms"] = True
                except Exception:
                    logger.exception("Failed to send manager escalation SMS")
            if manager.email:
                try:
                    from app.services.notification_services import SMTPEmailService
                    email_service = SMTPEmailService()
                    await email_service.send_email(
                        to_email=manager.email,
                        subject=subject,
                        body=plain_text,
                        html_body=body_html,
                    )
                    result["email"] = True
                except Exception:
                    logger.exception("Failed to send manager escalation email")
            try:
                from app.services.socket_manager import default_ws_manager
                await default_ws_manager.broadcast("sentiment_escalation", payload)
                result["dashboard"] = True
            except Exception:
                logger.exception("Failed to broadcast manager escalation")
            if manager.fcm_token:
                try:
                    from firebase_admin import messaging
                    notification = messaging.Notification(title=subject, body=sms_body)
                    message = messaging.Message(notification=notification, token=manager.fcm_token)
                    messaging.send(message)
                    result["push"] = True
                except Exception:
                    logger.exception("Failed to send manager escalation push")
            return result

        if manager.phone_number:
            sms_result = enqueue_tracked_recipient_delivery(
                self.db,
                tenant_id=str(escalation.tenant_id),
                recipient_id=str(manager.id),
                recipient_role=str(manager.role),
                channel="sms",
                source_type="SENTIMENT_ESCALATION_OWNER",
                source_id=str(escalation.id),
                title="FieldOps escalation",
                body=sms_body,
                address=manager.phone_number,
            )
            result["sms"] = bool(sms_result.get("queued")) or str(sms_result.get("status")) in {"queued", "sending", "accepted", "sent", "delivered"}

        if manager.email:
            email_result = enqueue_tracked_recipient_delivery(
                self.db,
                tenant_id=str(escalation.tenant_id),
                recipient_id=str(manager.id),
                recipient_role=str(manager.role),
                channel="email",
                source_type="SENTIMENT_ESCALATION_OWNER",
                source_id=str(escalation.id),
                title="FieldOps escalation",
                body=plain_text,
                address=manager.email,
                subject=subject,
                html_body=body_html,
                plain_text=plain_text,
            )
            result["email"] = bool(email_result.get("queued")) or str(email_result.get("status")) in {"queued", "sending", "accepted", "sent", "delivered"}

        if manager.fcm_token:
            push_result = enqueue_tracked_recipient_delivery(
                self.db,
                tenant_id=str(escalation.tenant_id),
                recipient_id=str(manager.id),
                recipient_role=str(manager.role),
                channel="push",
                source_type="SENTIMENT_ESCALATION_OWNER",
                source_id=str(escalation.id),
                title="FieldOps escalation",
                body=sms_body,
                address=manager.fcm_token,
            )
            result["push"] = bool(push_result.get("queued")) or str(push_result.get("status")) in {"queued", "sending", "accepted", "sent", "delivered"}

        # --------------------------------------------------
        # 6. Dashboard
        # --------------------------------------------------

        try:
            from app.services.socket_manager import sio

            await sio.emit(
                "sentiment_escalation",
                {
                    "type": "sentiment_escalation",
                    "payload": payload,
                },
                room=f"tenant:{escalation.tenant_id}:dispatchers",
            )

            result["dashboard"] = True

        except Exception:
            logger.exception(
                "Failed to broadcast escalation to dashboard. "
                "job_id=%s manager_id=%s",
                escalation.job_id,
                manager.id,
            )

        return result
