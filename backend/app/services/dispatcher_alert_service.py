import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Job, DispatcherAlert, Technician
from app.sentiment.audit import SentimentAuditLogger

logger = logging.getLogger(__name__)


class DispatcherAlertService:

    @staticmethod
    def check_and_trigger_alert(
        db: Session,
        redis_client,
        job: Job,
    ):
        """
        Checks if the attempt count warrants an alert.

        Logic:
        - Trigger at attempt == 3 (WARNING)
        - Skip at attempt == 4
        - Trigger at attempt >= 5 every 2 attempts (CRITICAL)
        """

        attempt = job.attempt_count or 0

        if attempt < 3:
            return

        # Alert at 3, 5, 7, ...
        if attempt >= 5 and (attempt - 3) % 2 != 0:
            return

        if attempt == 4:
            return

        severity = "CRITICAL" if attempt >= 5 else "WARNING"

        # Get excluded technicians
        excluded_techs_data = []

        if redis_client:
            tech_ids = redis_client.smembers(
                f"job:excluded:{job.id}"
            )

            for tid_bytes in tech_ids:
                tid = (
                    tid_bytes.decode("utf-8")
                    if isinstance(tid_bytes, bytes)
                    else tid_bytes
                )

                tech = (
                    db.query(Technician)
                    .filter(Technician.tech_id == tid)
                    .first()
                )

                name = (
                    tech.technician_name
                    if tech
                    else f"Tech {tid}"
                )

                reason = redis_client.hget(
                    f"job:exclusion_reasons:{job.id}",
                    tid,
                )

                reason_str = (
                    reason.decode("utf-8")
                    if isinstance(reason, bytes)
                    else (reason or "Unknown")
                )

                excluded_techs_data.append(
                    {
                        "name": name,
                        "reason": reason_str,
                    }
                )

        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        alert = DispatcherAlert(
            id=alert_id,
            tenant_id=job.tenant_id,
            type="repeated_redispatch",
            severity=severity,
            job_id=job.id,
            attempt_count=attempt,
            max_attempts=5,
            excluded_technicians=excluded_techs_data,
            recommended_action="Manual assignment or job review",
            acknowledged=0,
            created_at=now,
        )

        db.add(alert)
        db.commit()

        payload = {
            "alert_id": alert_id,
            "type": "repeated_redispatch",
            "severity": severity,
            "job_id": str(job.id),
            "job_title": (
                f"{job.service_type} - {job.location}"
            ),
            "attempt_count": attempt,
            "max_attempts": 5,
            "excluded_technicians": excluded_techs_data,
            "recommended_action": (
                "Manual assignment or job review"
            ),
            "created_at": now.isoformat(),
            "acknowledged": False,
        }

        logger.info(
            "DispatcherAlertService: [DASHBOARD] "
            "Broadcasting redispatch alert %s for job %s",
            alert_id,
            job.id,
        )

        from app.services.socket_manager import sio

        async def broadcast():
            try:
                await sio.emit(
                    "redispatch:alert",
                    payload,
                )
            except Exception as se:
                logger.error(
                    "Failed to emit socket.io alert: %s",
                    se,
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broadcast())
        except RuntimeError:
            new_loop = asyncio.new_event_loop()

            try:
                new_loop.run_until_complete(
                    broadcast()
                )
            finally:
                new_loop.close()

        logger.info(
            "DispatcherAlertService: [EMAIL] "
            "Alert sent for job %s - Attempt %s - Action Required",
            job.id,
            attempt,
        )

    @staticmethod
    def trigger_sentiment_alert(
        db: Session,
        job: Job,
        sentiment: str,
        confidence: float,
        alert_reason: str = "negative_high_confidence",
        urgency: str = "LOW",
        severity: str = "LOW",
        issue_categories: list[str] | None = None,
        source_sentiment_message_id: int | None = None,
    ):
        """
        Create and broadcast an alert for negative customer sentiment.

        Alert is generated when:
        - sentiment is NEGATIVE
        - confidence is greater than 0.8

        The alert_reason identifies why the alert was triggered.
        """

        safety_risk = (
            str(urgency).upper() == "CRITICAL"
            or str(severity).upper() == "CRITICAL"
            or "SAFETY" in (issue_categories or [])
        )
        if not safety_risk and not (
            alert_reason == "positive_to_negative_shift"
            or (sentiment == "NEGATIVE" and confidence > 0.8)
        ):
            return None
        alert_severity = "CRITICAL" if safety_risk else (
            str(severity or "LOW").upper()
            if str(severity or "LOW").upper() in {"HIGH", "CRITICAL"}
            else "HIGH"
        )

        source_marker = (
            f"sentiment_message:{source_sentiment_message_id}:{alert_reason}"
            if source_sentiment_message_id is not None
            else None
        )
        if source_marker:
            existing = db.query(DispatcherAlert).filter(
                DispatcherAlert.job_id == job.id,
                DispatcherAlert.tenant_id == job.tenant_id,
                DispatcherAlert.type == "customer_sentiment",
                DispatcherAlert.recommended_action == source_marker,
            ).first()
            if existing:
                return existing

        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        alert = DispatcherAlert(
            id=alert_id,
            tenant_id=job.tenant_id,
            type="customer_sentiment",
            severity=alert_severity,
            job_id=job.id,
            attempt_count=job.attempt_count or 0,
            max_attempts=5,
            excluded_technicians=[],
            recommended_action=source_marker or alert_reason,
            acknowledged=0,
            created_at=now,
        )

        db.add(alert)
        audit_logger = SentimentAuditLogger(db)

        audit_logger.log_escalation(
            {
                "tenant_id": job.tenant_id,
                "job_id": job.id,
                "trigger_reason": alert_reason,
            }
        )
        db.commit()

        payload = {
            "alert_id": alert_id,
            "type": "customer_sentiment",
            "severity": "CRITICAL",
            "job_id": str(job.id),
            "sentiment": sentiment,
            "confidence": confidence,
            "alert_reason": alert_reason,
            "urgency": str(urgency).upper(),
            "severity": alert_severity,
            "issue_categories": issue_categories or [],
            "sentiment_message_id": source_sentiment_message_id,
            "recommended_action": alert_reason,
            "created_at": now.isoformat(),
            "acknowledged": False,
        }

        logger.info(
            "DispatcherAlertService: "
            "Sentiment alert created for job %s "
            "with confidence %s",
            job.id,
            confidence,
        )

        # --------------------------------------------------
        # Real-time dashboard notification
        # --------------------------------------------------

        from app.services.socket_manager import sio

        async def broadcast():
            try:
                await sio.emit(
                    "sentiment:alert",
                    payload,
                    room=f"tenant:{job.tenant_id}:dispatchers",
                )

                logger.info(
                    "DispatcherAlertService: "
                    "Sentiment alert broadcasted for job %s",
                    job.id,
                )

            except Exception as se:
                logger.error(
                    "Failed to emit sentiment socket alert: %s",
                    se,
                )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(broadcast())

        except RuntimeError:
            new_loop = asyncio.new_event_loop()

            try:
                new_loop.run_until_complete(
                    broadcast()
                )
            finally:
                new_loop.close()

        return alert
