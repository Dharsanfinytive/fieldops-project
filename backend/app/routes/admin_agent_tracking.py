"""Read-only job and AI-agent workflow tracking for organization operators."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from ..auth.dependencies import AuthenticatedUser, get_current_user
from ..auth.rbac import UserRole
from ..database import get_db
from ..models import AuditEvent, InAppNotification, Job, JobMessage, SentimentThreadMessage
from ..models.job_closure import JobClosure
from ..models.transactional_email_outbox import TransactionalEmailOutbox
from ..models_legacy import NotificationChannelDelivery
from ..models.user import User
from ..models_legacy import Technician
from ..models.sentiment_escalation import SentimentEscalation


router = APIRouter(
    prefix="/api/admin/agent-tracking",
    tags=["Agent Tracking"],
)

ACCEPTED_STATUSES = {
    "ACCEPTED",
    "EN_ROUTE",
    "ON_SITE",
    "IN_PROGRESS",
    "COMPLETED",
    "CLOSED",
}
TERMINAL_STATUSES = {"COMPLETED", "CLOSED", "CANCELLED", "CANCELED"}


def _role_name(current_user: AuthenticatedUser) -> str:
    role = getattr(current_user.role, "value", current_user.role)
    return str(role or "").strip().lower()


def _can_view_agent_tracking(current_user: AuthenticatedUser) -> bool:
    return _role_name(current_user) in {"head", "super_admin"}


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def _stage(
    key: str,
    label: str,
    state: str,
    detail: str,
    timestamp: datetime | None = None,
) -> dict:
    return {
        "key": key,
        "label": label,
        "state": state,
        "detail": detail,
        "timestamp": _utc_iso(timestamp),
    }


def _track_job(job: Job) -> dict:
    status = str(job.status or "CREATED").strip().upper().replace(" ", "_")
    is_closed = status in {"CLOSED", "CANCELLED", "CANCELED"} or bool(
        job.closed_at or job.cancelled_at
    )
    is_completed = status in {"COMPLETED", "CLOSED"} or (
        bool(job.completed_at) and status not in {"CANCELLED", "CANCELED"}
    )
    is_accepted = (
        status in ACCEPTED_STATUSES
        or bool(job.en_route_at or job.on_site_at or job.work_started_at)
    )
    is_assigned = job.assigned_technician_id is not None or job.assigned_at is not None
    waiting_for_acceptance = is_assigned and not is_accepted and not is_closed

    if is_closed:
        current_agent = "Workflow complete" if job.closed_at or status == "CLOSED" else "Workflow cancelled"
        current_detail = "The job has reached a terminal state."
    elif is_completed:
        current_agent = "Closure Agent"
        current_detail = "The job is completed and awaiting final closure."
    elif waiting_for_acceptance:
        current_agent = "Dispatcher"
        current_detail = "Waiting for the assigned technician to accept."
    elif is_accepted and status in {"EN_ROUTE", "ON_SITE", "IN_PROGRESS"}:
        current_agent = "Technician Work"
        current_detail = "The technician is carrying out the assigned service."
    elif is_accepted:
        current_agent = "Communication Agent"
        current_detail = "The assignment was accepted; service updates are event-driven."
    else:
        current_agent = "Planning Agent"
        current_detail = "Preparing the job for technician dispatch."

    assigned_time = job.assigned_at
    stages = [
        _stage(
            "intake",
            "Request Received",
            "complete",
            "Customer created the job. The Communication Agent records the in-app customer update.",
            job.created_at,
        ),
        _stage(
            "planning",
            "Plan Job",
            "complete" if is_assigned or is_accepted or is_closed else "active",
            "Planning Agent selected technician candidates." if is_assigned or is_accepted else "Planning Agent is preparing ranked technician options.",
            assigned_time if is_assigned else None,
        ),
        _stage(
            "dispatch",
            "Assign Technician",
            "complete" if is_accepted or is_closed else "waiting" if waiting_for_acceptance else "active",
            "Technician accepted; dispatch is confirmed." if is_accepted else "Assignment notice sent; waiting for accept or reject." if waiting_for_acceptance else "Dispatcher offers ranked technicians and continues with the next group after rejection.",
            assigned_time,
        ),
        _stage(
            "communication",
            "Communicate",
            "event_driven" if is_accepted else "locked",
            "Sends event-driven in-app/push updates and chat; sends booking and completion email through the transactional outbox. SLA reminders stop when Start is pressed." if is_accepted else "Sends assignment/rejection updates; acceptance starts chat and booking email. SLA reminders stop when Start is pressed.",
            job.updated_at if is_accepted else None,
        ),
        _stage(
            "technician_work",
            "Track Work",
            "complete" if is_completed else "active" if status in {"EN_ROUTE", "ON_SITE", "IN_PROGRESS"} else "locked",
            "Completion is handed to Closure Agent for authorization." if is_completed else "Start and on-site actions create chat and notification updates; Start includes the latest GPS route estimate when available." if status in {"EN_ROUTE", "ON_SITE", "IN_PROGRESS"} else "After acceptance, Start and on-site actions create customer chat and notification updates.",
            job.work_started_at or job.on_site_at or job.en_route_at,
        ),
        _stage(
            "sentiment",
            "Check Customer Mood",
            "event_driven",
            "Analyzes customer messages and feedback as they arrive; review alerts are generated for high risk or unresolved concerns.",
        ),
    ]
    if is_completed:
        stages.append(
            _stage(
                "closure",
                "Close Job",
                "complete" if is_closed else "active",
                "Closure requirements are recorded and ready for review." if not is_closed else "Closure Agent recorded the final job closure.",
                job.closed_at or job.completed_at,
            )
        )

    technician = getattr(job, "technician", None)
    return {
        "job_id": job.id,
        "job_number": getattr(job, "job_number", None) or job.id,
        "service_type": job.service_type,
        "status": status,
        "priority": job.priority,
        "technician_name": getattr(technician, "technician_name", None),
        "customer_name": job.customer_name,
        "location": job.location,
        "created_at": _utc_iso(job.created_at),
        "updated_at": _utc_iso(job.updated_at),
        "assigned_at": _utc_iso(job.assigned_at),
        "accepted": is_accepted,
        "current_agent": current_agent,
        "current_detail": current_detail,
        "stages": stages,
    }


def _event(timestamp: datetime | None, agent: str, title: str, detail: str, kind: str, **extra) -> dict:
    return {
        "timestamp": _utc_iso(timestamp),
        "agent": agent,
        "title": title,
        "detail": detail,
        "kind": kind,
        **extra,
    }


def _communication_agent(event_type: str, channel: str | None = None) -> str:
    normalized = str(event_type or "").lower()
    if normalized in {"invoice_finalized", "invoice_ready", "closure", "job_closed"}:
        return "Closure Agent"
    if normalized.startswith("technician_job_reminder"):
        return "Communication Agent · Reminders"
    if channel == "email" and normalized == "booking_confirmation":
        return "Communication Agent"
    return "Communication Agent"


def _delivery_event_type(delivery: NotificationChannelDelivery, notification) -> tuple[str, int | None]:
    """Recover lifecycle event/version from the delivery idempotency key."""
    parts = str(delivery.idempotency_key or "").split(":")
    if len(parts) >= 7 and parts[0] == str(notification.job_id):
        try:
            version = int(parts[-1])
        except (TypeError, ValueError):
            version = None
        return parts[1], version
    return str(notification.type or "notification"), None


def _work_is_incomplete(*values: object) -> bool:
    text = " ".join(str(value or "") for value in values).lower()
    incomplete_signals = (
        "not finished",
        "unfinished",
        "not completed",
        "not complete",
        "incomplete",
        "not done",
        "not resolved",
        "issue remains unresolved",
        "work remains",
        "needs further work",
        "need more work",
        "waiting for spare parts",
        "waiting for parts",
        "temporary repair",
        "additional visit required",
    )
    return any(signal in text for signal in incomplete_signals)


@router.get("/{job_id}")
def get_agent_tracking_job(
    job_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    from fastapi import HTTPException, status

    if not _can_view_agent_tracking(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent tracking is restricted to organization operators.")

    query = db.query(Job).options(joinedload(Job.technician)).filter(Job.id == job_id)
    if _role_name(current_user) != "head":
        query = query.filter(Job.tenant_id == current_user.tenant_id)
    job = query.first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    tracked = _track_job(job)
    request_input = {
        "customer": job.customer_name,
        "service_type": job.service_type,
        "request": job.issue_description,
        "location": job.site_address or job.location,
        "priority": job.priority,
        "required_skill": job.required_skill,
        "preferred_visit": str(job.preferred_service_date) if job.preferred_service_date else None,
    }
    job_output = {
        "job_id": job.id,
        "job_number": tracked["job_number"],
        "status": tracked["status"],
        "technician": tracked["technician_name"],
        "assigned_at": tracked["assigned_at"],
        "attempt_count": getattr(job, "attempt_count", None),
        "sla_deadline": _utc_iso(getattr(job, "sla_deadline", None)),
    }
    events = [
        _event(job.created_at, "Intake Agent", "Request received", f"Service request received for {job.service_type or 'service'}.", "intake", input=request_input, output={"persisted_job": job_output, "note": "The application does not persist the Intake Agent's raw model response; this is the resulting saved job record."}),
        _event(job.assigned_at or job.created_at, "Planning Agent", "Job prepared for dispatch", "Request details were prepared for technician assignment.", "planning", input=request_input, output={"technician": tracked["technician_name"], "assignment_status": tracked["status"], "attempt_count": getattr(job, "attempt_count", None), "note": "Candidate rankings and the raw Planning Agent response are not available in the persisted job record."}),
    ]
    if job.assigned_at:
        events.append(_event(job.assigned_at, "Dispatcher Agent", "Technician assigned", f"{tracked['technician_name'] or 'A technician'} was assigned to the job.", "dispatch", input={"job_id": job.id, "service_type": job.service_type, "required_skill": job.required_skill, "location": job.site_address or job.location}, output={"technician": tracked["technician_name"], "assigned_at": tracked["assigned_at"], "attempt_count": getattr(job, "attempt_count", None), "note": "The selected technician is persisted; candidate scores and the raw dispatcher response are not retained."}))
    if job.en_route_at:
        events.append(_event(job.en_route_at, "Technician", "Travel started", "Technician started travelling to the service location.", "work"))
    if job.on_site_at:
        events.append(_event(job.on_site_at, "Technician", "Arrived on site", "Technician arrived at the service location.", "work"))
    if job.work_started_at:
        events.append(_event(job.work_started_at, "Technician", "Work started", "Technician started the service work.", "work"))
    if job.completed_at:
        events.append(_event(job.completed_at, "Technician", "Service completed", "Technician marked the service as complete.", "work"))
    if job.cancelled_at:
        events.append(_event(job.cancelled_at, "Dispatcher Agent", "Job cancelled", job.cancellation_reason or "The job was cancelled.", "dispatch", input={"job_id": job.id, "attempt_count": getattr(job, "attempt_count", None)}, output={"status": tracked["status"], "cancellation_reason": job.cancellation_reason, "cancelled_at": _utc_iso(job.cancelled_at)}))

    # The job card is an in-app progress card, so it is updated in place as
    # lifecycle events advance. Delivery rows retain each channel attempt and
    # its event version, which lets the timeline show the actual send history.
    related_tenants = {
        str(value) for value in (
            job.tenant_id,
            job.customer_tenant_id,
            getattr(getattr(job, "technician", None), "tenant_id", None),
        ) if value
    }
    notification_query = db.query(InAppNotification).filter(
        InAppNotification.job_id == str(job.id)
    )
    if _role_name(current_user) != "head":
        notification_query = notification_query.filter(InAppNotification.tenant_id.in_(related_tenants))
    notifications = notification_query.order_by(
        InAppNotification.created_at.asc(), InAppNotification.id.asc()
    ).all()
    notifications_by_id = {str(item.id): item for item in notifications}
    technician_recipient_ids = {
        str(item.recipient_id) for item in notifications
        if item.recipient_role == "technician" and item.recipient_id
    }
    technician_names = {
        str(item.tech_id): item.technician_name
        for item in db.query(Technician).filter(Technician.tech_id.in_(technician_recipient_ids)).all()
    } if technician_recipient_ids else {}
    user_recipient_ids = {
        str(item.recipient_id) for item in notifications
        if item.recipient_role not in {"customer", "technician"} and item.recipient_id
    }
    user_names = {
        str(item.id): f"{item.first_name or ''} {item.last_name or ''}".strip() or item.email
        for item in db.query(User).filter(User.id.in_(user_recipient_ids)).all()
    } if user_recipient_ids else {}

    def recipient_name(item: InAppNotification) -> str | None:
        role = str(item.recipient_role or "").lower()
        if role == "customer":
            return job.customer_name
        if role == "technician":
            return technician_names.get(str(item.recipient_id))
        return user_names.get(str(item.recipient_id))

    for item in notifications:
        kind = str(item.type or "notification")
        title = item.title or kind.replace("_", " ").title()
        events.append(_event(
            item.updated_at or item.created_at,
            _communication_agent(kind),
            title,
            item.body or "In-app notification recorded.",
            "notification",
            notification_id=str(item.id),
            channel="in_app",
            delivery_status=str(item.status or "UNREAD").lower(),
            recipient_role=item.recipient_role or "unknown",
            recipient_id=str(item.recipient_id) if item.recipient_id else None,
            recipient_name=recipient_name(item),
            event_type=kind,
            event_version=item.event_version,
        ))

    delivery_query = db.query(NotificationChannelDelivery).filter(
        NotificationChannelDelivery.notification_id.in_(list(notifications_by_id) or ["__none__"])
    )
    if _role_name(current_user) != "head":
        delivery_query = delivery_query.filter(NotificationChannelDelivery.tenant_id.in_(related_tenants))
    for delivery in delivery_query.order_by(
        NotificationChannelDelivery.created_at.asc(), NotificationChannelDelivery.id.asc()
    ).all():
        notification = notifications_by_id.get(str(delivery.notification_id))
        if notification is None:
            continue
        event_type, version = _delivery_event_type(delivery, notification)
        current_event = event_type == str(notification.type or "")
        detail = (
            notification.body or "Communication delivery recorded."
            if current_event else
            f"A {delivery.channel} delivery was recorded for {event_type.replace('_', ' ')}."
        )
        events.append(_event(
            delivery.updated_at or delivery.created_at,
            _communication_agent(event_type, delivery.channel),
            f"{event_type.replace('_', ' ').title()} · {delivery.channel.replace('_', ' ').title()}",
            detail,
            "delivery",
            notification_id=str(delivery.notification_id),
            channel=str(delivery.channel),
            delivery_status=str(delivery.status or "unknown").lower(),
            attempts=int(delivery.attempts or 0),
            recipient_role=notification.recipient_role or "unknown",
            recipient_id=str(delivery.recipient_id),
            recipient_name=recipient_name(notification),
            event_type=event_type,
            event_version=version,
            error=delivery.error_message,
        ))

    email_query = db.query(TransactionalEmailOutbox).filter(
        TransactionalEmailOutbox.job_id == job.id
    )
    if _role_name(current_user) != "head":
        email_query = email_query.filter(TransactionalEmailOutbox.tenant_id.in_(related_tenants))
    for email in email_query.order_by(
        TransactionalEmailOutbox.created_at.asc(), TransactionalEmailOutbox.id.asc()
    ).all():
        email_title = (
            "Booking confirmation email"
            if email.event_type == "booking_confirmation"
            else "Completion email"
            if email.event_type == "invoice_finalized"
            else f"{str(email.event_type).replace('_', ' ').title()} email"
        )
        events.append(_event(
            email.sent_at or email.updated_at or email.created_at,
            _communication_agent(email.event_type, "email"),
            email_title,
            f"Customer email delivery is {email.status}." + (f" {email.last_error}" if email.last_error else ""),
            "delivery",
            channel="email",
            delivery_status=str(email.status or "unknown").lower(),
            attempts=int(email.attempts or 0),
            recipient_role="customer",
            event_type=email.event_type,
            event_version=email.event_version,
            provider_message_id=email.provider_message_id,
            smtp_status=email.smtp_status,
        ))

    audit_query = db.query(AuditEvent).filter(AuditEvent.job_id == str(job.id))
    if _role_name(current_user) != "head":
        audit_query = audit_query.filter(AuditEvent.tenant_id == current_user.tenant_id)
    for record in audit_query.order_by(AuditEvent.timestamp.asc(), AuditEvent.id.asc()).all():
        events.append(_event(record.timestamp or record.created_at, "Workflow Log", record.event_type.replace("_", " ").title(), record.reason or str(record.details or "Job status updated."), "audit", input={"old_status": record.old_status, "actor_id": record.actor_id}, output={"new_status": record.new_status, "reason": record.reason, "details": record.details, "correlation_id": record.correlation_id}))

    message_query = db.query(JobMessage).filter(JobMessage.job_id == job.id)
    if _role_name(current_user) != "head":
        message_query = message_query.filter(JobMessage.tenant_id == current_user.tenant_id)
    for message in message_query.order_by(JobMessage.created_at.asc()).all():
        message_body = str(message.body or "")
        automatic = (
            message_body.startswith("Automatic job update from ")
            or "your technician for job #" in message_body.lower()
        )
        actor_name = getattr(getattr(message, "sender", None), "first_name", None)
        events.append(_event(
            message.created_at,
            "Communication Agent" if automatic else str(message.sender_role).title(),
            f"{message.sender_role.title()} chat message",
            message.body,
            "communication",
            message_id=message.id,
            sender_role=message.sender_role,
            channel="in_app_chat",
            delivery_status="delivered" if message.delivered_at else "sent",
            actor=actor_name,
            recipient_role="customer" if message.sender_role == "technician" else "technician",
        ))

    sentiment_query = db.query(SentimentThreadMessage).filter(
        SentimentThreadMessage.job_id == job.id,
        SentimentThreadMessage.tenant_id == job.tenant_id,
    )
    if current_user.role == UserRole.SUPER_ADMIN:
        sentiment_query = sentiment_query.filter(SentimentThreadMessage.tenant_id == current_user.tenant_id)
    sentiment_records = sentiment_query.order_by(SentimentThreadMessage.created_at.asc()).all()
    sentiment_ids = [record.id for record in sentiment_records]
    escalation_rows = db.query(SentimentEscalation).filter(
        SentimentEscalation.tenant_id == job.tenant_id,
        SentimentEscalation.source_sentiment_message_id.in_(sentiment_ids or [-1]),
    ).all()
    escalation_by_message = {
        row.source_sentiment_message_id: row for row in escalation_rows
    }
    for record in sentiment_records:
        mood = record.sentiment or "Analyzed"
        detail = record.summary or record.message
        escalation = escalation_by_message.get(record.id)
        categories = record.issue_categories or []
        events.append(_event(
            record.created_at,
            "Sentiment Agent",
            f"Customer mood: {mood}",
            detail,
            "sentiment",
            message_id=str(record.id),
            source_message_id=record.source_message_id,
            emotion=record.emotion,
            confidence=record.confidence,
            urgency=record.urgency,
            severity=record.severity,
            issue_categories=categories,
            requires_human=str(record.requires_human or "").lower() in {"true", "yes", "1"},
            analysis_source=getattr(record, "analysis_source", "AI"),
            language=getattr(record, "language", None),
            escalation_id=escalation.id if escalation else None,
            escalation_status=escalation.status if escalation else None,
            escalation_owner_id=escalation.assigned_manager_id if escalation else None,
            escalation_trigger=escalation.trigger_reason if escalation else None,
        ))
        if escalation:
            events.append(_event(
                escalation.created_at,
                "Escalation Service",
                "Human review requested",
                escalation.trigger_reason or "Sentiment policy requires human review.",
                "escalation",
                escalation_id=escalation.id,
                delivery_status=escalation.status.lower(),
                recipient_id=escalation.assigned_manager_id,
                recipient_role="dispatcher_or_tenant_admin" if escalation.assigned_manager_id else "unassigned_tenant_queue",
                severity="CRITICAL" if "CRITICAL_SAFETY_RISK" in str(escalation.trigger_reason) else "HIGH",
            ))

    events.sort(key=lambda item: item["timestamp"] or "")

    if tracked["status"] in {"COMPLETED", "CLOSED"} or (
        job.completed_at is not None
        and tracked["status"] not in {"CANCELLED", "CANCELED"}
    ):
        closure = (
            db.query(JobClosure)
            .filter(
                JobClosure.job_id == job.id,
                JobClosure.tenant_id == str(job.tenant_id),
            )
            .order_by(JobClosure.completed_at.desc(), JobClosure.id.desc())
            .first()
        )
        work_report = closure.work_report if closure and isinstance(closure.work_report, dict) else {}
        closure_decision = work_report.get("closure_agent")
        if not isinstance(closure_decision, dict):
            closure_decision = {}
        follow_up_reason = str(closure_decision.get("follow_up_reason") or "").strip()
        work_incomplete = _work_is_incomplete(
            closure.work_summary if closure else None,
            work_report.get("summary"),
            closure_decision.get("work_summary"),
            follow_up_reason,
        )
        checklist_data = closure.completion_checklist if closure else None
        checklist_items = checklist_data.get("items", []) if isinstance(checklist_data, dict) else []
        checklist_items = [item for item in checklist_items if isinstance(item, dict)]
        required_items = [item for item in checklist_items if item.get("required") is True]
        if work_incomplete:
            follow_up_item = {
                "id": "closure-follow-up",
                "label": follow_up_reason or "Complete the unfinished work.",
                "required": True,
                "status": "REQUIRED",
            }
            checklist_items.append(follow_up_item)
            required_items.append(follow_up_item)
        requirements_met = not work_incomplete and all(
            str(item.get("status", "")).upper() == "COMPLETED"
            for item in required_items
        )
        if work_incomplete:
            closure_detail = (
                f"Work is unfinished. Required follow-up: {follow_up_reason}"
                if follow_up_reason
                else "Work is unfinished and must be completed before closure."
            )
        elif required_items:
            closure_detail = (
                "All required completion checklist items were completed."
                if requirements_met
                else "Some required completion checklist items need attention."
            )
        else:
            closure_detail = "No additional required checklist items were recorded for this job."

        events.append(_event(
            closure.completed_at if closure else job.completed_at,
            "Closure Agent",
            "Closure requirements check",
            closure_detail,
            "closure",
            delivery_status="passed" if requirements_met else "review",
            input={
                "completion_checklist": checklist_items,
                "required_checklist_item_count": len(required_items),
                "after_image_count": len(closure.after_images or []) if closure else 0,
                "work_summary": closure.work_summary if closure else None,
            },
            output={
                "requirements_met": requirements_met,
                "work_incomplete": work_incomplete,
                "checklist_recorded": checklist_data is not None,
                "required_checklist_items": required_items,
                "closure_agent": closure_decision or None,
                "closure_reason": job.closure_reason,
                "closed_at": _utc_iso(job.closed_at),
            },
        ))
    return {
        **tracked,
        "customer_name": job.customer_name,
        "location": job.site_address or job.location,
        "preferred_service_date": str(job.preferred_service_date) if job.preferred_service_date else None,
        "preferred_service_time": str(job.preferred_service_time) if job.preferred_service_time else None,
        "events": events,
    }


@router.get("")
def get_agent_tracking(
    limit: int = Query(100, ge=1, le=250),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # The platform HEAD role is allowed to inspect jobs across organizations;
    # organization Super Admins remain scoped to their own tenant.
    if not _can_view_agent_tracking(current_user):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent tracking is restricted to organization operators.",
        )

    query = db.query(Job).options(joinedload(Job.technician))
    if _role_name(current_user) != "head":
        query = query.filter(Job.tenant_id == current_user.tenant_id)
    jobs = query.order_by(Job.updated_at.desc(), Job.id.desc()).limit(limit).all()
    tracked_jobs = [_track_job(job) for job in jobs]
    job_ids = [job.id for job in jobs]
    if job_ids:
        sentiment_query = db.query(SentimentThreadMessage).filter(
            SentimentThreadMessage.job_id.in_(job_ids),
        )
        if _role_name(current_user) != "head":
            sentiment_query = sentiment_query.filter(
                SentimentThreadMessage.tenant_id == current_user.tenant_id
            )
        sentiment_rows = sentiment_query.order_by(
            SentimentThreadMessage.created_at.desc(),
            SentimentThreadMessage.id.desc(),
        ).all()
        by_job: dict[int, list[SentimentThreadMessage]] = {}
        for record in sentiment_rows:
            by_job.setdefault(record.job_id, []).append(record)
        for tracked in tracked_jobs:
            records = by_job.get(tracked["job_id"], [])
            sentiment_stage = next(stage for stage in tracked["stages"] if stage["key"] == "sentiment")
            if records:
                latest = records[0]
                sentiment_stage["timestamp"] = _utc_iso(latest.created_at)
                sentiment_stage["detail"] = (
                    f"Analyzed {len(records)} customer message(s). Latest: {latest.sentiment or 'unknown'}; "
                    f"urgency {latest.urgency or 'unknown'}, severity {latest.severity or 'unknown'}. "
                    f"{('Human review required. ' if str(latest.requires_human or '').lower() in {'true', 'yes', '1'} else '')}"
                    "Open the workflow to review each analysis and escalation."
                )

    return {
        "jobs": tracked_jobs,
        "total": len(tracked_jobs),
        "active": sum(
            1
            for job in tracked_jobs
            if job["status"] not in TERMINAL_STATUSES
        ),
        "waiting_acceptance": sum(
            1
            for job in tracked_jobs
            if job["current_agent"] == "Dispatcher"
        ),
        "generated_at": _utc_iso(datetime.now(timezone.utc)),
    }
