"""Persisted, idempotent outbound communication using existing SMS delivery."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError

from app.models_legacy import InAppNotification, NotificationChannelDelivery
from app.services.ai.FieldOpsAI.agents.communication_agent.channels import (
    claim_delivery,
    enqueue_email,
    enqueue_push,
    enqueue_sms,
    mark_delivery,
)

logger = logging.getLogger(__name__)


def enqueue_tracked_recipient_delivery(
    db,
    *,
    tenant_id: str,
    recipient_id: str,
    recipient_role: str,
    channel: str,
    source_type: str,
    source_id: str,
    title: str,
    body: str,
    address: str,
    subject: str | None = None,
    html_body: str | None = None,
    plain_text: str | None = None,
) -> dict[str, int | str | bool]:
    """Queue an idempotent, delivery-tracked message to a tenant user."""
    tenant = str(tenant_id).strip()
    recipient = str(recipient_id).strip()
    role = str(recipient_role).strip().lower()
    channel = str(channel).strip().lower()
    if not tenant or not recipient or not address.strip() or not body.strip():
        raise ValueError("Tracked notification requires a tenant, recipient, address, and body.")
    if channel not in {"sms", "email", "push"}:
        raise ValueError("Tracked notification channel is not supported.")

    base_key = f"{tenant}:{source_type}:{source_id}:{role}:{recipient}"
    notification_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fieldops:notification:{base_key}"))
    idempotency_key = f"{base_key}:{channel}"
    notification = db.get(InAppNotification, notification_id)
    if notification is None:
        notification = InAppNotification(
            id=notification_id,
            tenant_id=tenant,
            job_id=None,
            type="sentiment_escalation",
            title=title[:200],
            body=body[:4000],
            status="UNREAD",
            priority="HIGH",
            recipient_id=recipient,
            recipient_role=role,
            group_key=f"{source_type}:{source_id}"[:100],
            notification_metadata={"source_type": source_type, "source_id": str(source_id)},
        )
        db.add(notification)
        db.flush()

    existing = db.query(NotificationChannelDelivery).filter(
        NotificationChannelDelivery.idempotency_key == idempotency_key,
        NotificationChannelDelivery.tenant_id == tenant,
    ).first()
    delivery_id = claim_delivery(
        db,
        tenant_id=tenant,
        notification_id=notification_id,
        channel=channel,
        recipient_id=recipient,
        key=idempotency_key,
    )
    if delivery_id is None:
        return {
            "status": str(existing.status) if existing else "duplicate_in_flight",
            "delivery_id": int(existing.id) if existing else 0,
            "queued": False,
        }
    db.commit()

    try:
        if channel == "sms":
            if len(body) > 160:
                raise ValueError("Tracked SMS must fit the approved 160-character limit.")
            enqueue_sms(address, body, int(delivery_id))
        elif channel == "email":
            if not subject or not html_body:
                raise ValueError("Tracked email requires a subject and HTML body.")
            enqueue_email(address, subject, html_body, plain_text or body, int(delivery_id))
        else:
            enqueue_push(title, body[:160], int(delivery_id))
    except Exception as exc:
        mark_delivery(db, int(delivery_id), "failed", f"Queue failed: {type(exc).__name__}"[:500])
        logger.exception("Unable to queue tracked sentiment delivery: delivery_id=%s", delivery_id)
        return {"status": "failed", "delivery_id": int(delivery_id), "queued": False}

    return {"status": "queued", "delivery_id": int(delivery_id), "queued": True}


def enqueue_tracked_sms(
    db,
    *,
    tenant_id: str,
    recipient_id: str,
    recipient_role: str,
    technician_id: str | None = None,
    to_number: str,
    body: str,
    source_type: str,
    source_id: str,
    job_id: int | str | None = None,
    title: str = "FieldOps update",
) -> dict[str, int | str | bool]:
    """Create a notification/delivery record before queuing an SMS.

    Stable source identity makes webhook and task retries safe. Existing failed
    deliveries can be reclaimed; queued or accepted rows are never duplicated.
    """
    tenant = str(tenant_id).strip()
    recipient = str(recipient_id).strip()
    role = str(recipient_role).strip().lower()
    if not tenant or not recipient or not to_number.strip() or not body.strip():
        raise ValueError("SMS delivery requires tenant, recipient, phone, and body.")
    if len(body) > 160:
        raise ValueError("Tracked SMS body must fit the approved 160-character limit.")
    if role not in {"customer", "technician"}:
        raise ValueError("SMS recipient role is not supported.")

    idempotency_key = (
        f"{tenant}:{source_type}:{source_id}:SMS:{role}:{recipient}"
    )
    existing = db.query(NotificationChannelDelivery).filter(
        NotificationChannelDelivery.idempotency_key == idempotency_key,
        NotificationChannelDelivery.tenant_id == tenant,
    ).first()

    if existing:
        delivery_id = claim_delivery(
            db,
            tenant_id=tenant,
            notification_id=existing.notification_id,
            channel="sms",
            recipient_id=recipient,
            key=idempotency_key,
        )
        if delivery_id is None:
            return {
                "status": str(existing.status),
                "delivery_id": existing.id,
                "queued": False,
            }
        db.commit()
    else:
        notification_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"fieldops:notification:{idempotency_key}")
        )
        notification = InAppNotification(
            id=notification_id,
            tech_id=(technician_id or None) if role == "technician" else None,
            customer_user_id=recipient if role == "customer" else None,
            tenant_id=tenant,
            job_id=str(job_id) if job_id is not None else None,
            type="communication_reply",
            title=title[:200],
            body=body[:4000],
            status="UNREAD",
            priority="NORMAL",
            recipient_id=recipient,
            recipient_role=role,
            group_key=f"communication:{source_type}:{source_id}"[:100],
            notification_metadata={
                "source_type": source_type,
                "source_id": str(source_id),
            },
        )
        db.add(notification)
        try:
            db.flush()
            delivery_id = claim_delivery(
                db,
                tenant_id=tenant,
                notification_id=notification_id,
                channel="sms",
                recipient_id=recipient,
                key=idempotency_key,
            )
            if delivery_id is None:
                db.rollback()
                return {"status": "duplicate", "queued": False}
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(NotificationChannelDelivery).filter(
                NotificationChannelDelivery.idempotency_key == idempotency_key,
                NotificationChannelDelivery.tenant_id == tenant,
            ).first()
            if existing is None:
                return {"status": "duplicate_in_flight", "queued": False}
            delivery_id = claim_delivery(
                db,
                tenant_id=tenant,
                notification_id=existing.notification_id,
                channel="sms",
                recipient_id=recipient,
                key=idempotency_key,
            )
            if delivery_id is None:
                return {
                    "status": str(existing.status),
                    "delivery_id": existing.id,
                    "queued": False,
                }
            db.commit()

    try:
        enqueue_sms(to_number, body, int(delivery_id))
    except Exception as exc:
        mark_delivery(
            db,
            int(delivery_id),
            "failed",
            f"Queue failed: {type(exc).__name__}"[:500],
        )
        logger.error(
            "Unable to queue tracked SMS delivery_id=%s source_type=%s error_type=%s",
            delivery_id,
            source_type,
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "delivery_id": int(delivery_id),
            "queued": False,
        }

    return {
        "status": "queued",
        "delivery_id": int(delivery_id),
        "queued": True,
    }
