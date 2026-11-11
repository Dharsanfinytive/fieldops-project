"""Organization-scoped operational settings for GPS and communications."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, TypeAdapter
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.dependencies import AuthenticatedUser, get_current_user, require_permission
from ..auth.rbac import Permission
from ..database import get_db
from ..models import (
    CommunicationChannelConfiguration,
    CommunicationConfigurationAudit,
    Organization,
    TenantGPSConfiguration,
)
from ..services.dispatch_eligibility import LOCATION_MAX_AGE_SECONDS
from ..services.enterprise_audit import audit_log

router = APIRouter(prefix="/organization-settings", tags=["Organization Settings"])


class FieldOperationsUpdate(BaseModel):
    retention_days: int = Field(30, ge=1, le=90)
    location_freshness_seconds: int = Field(120, ge=30, le=120)
    reason: str = Field(..., min_length=10, max_length=500)


class ChannelStateUpdate(BaseModel):
    state: Literal["ENABLED", "DISABLED"]
    reason: str = Field(..., min_length=10, max_length=500)


class TestMessageRequest(BaseModel):
    channel: Literal["EMAIL", "SMS"]
    destination: str = Field(..., min_length=5, max_length=255)


def _tenant_id(user: AuthenticatedUser) -> str:
    tenant_id = str(user.tenant_id or "")
    if not tenant_id or tenant_id in {"__platform__", "**platform**"}:
        raise HTTPException(status_code=403, detail="Organization settings require an organization account")
    return tenant_id


@router.get("/field-operations")
def get_field_operations_settings(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tenant users may read the small operational policy used by technician clients."""
    tenant_id = _tenant_id(current_user)
    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    gps_config = db.query(TenantGPSConfiguration).filter(
        TenantGPSConfiguration.tenant_id == tenant_id
    ).first()
    settings = dict(org.settings or {})
    field_ops = settings.get("field_operations") or {}
    return {
        "retention_days": gps_config.retention_days if gps_config else 30,
        "location_freshness_seconds": int(field_ops.get("location_freshness_seconds", min(LOCATION_MAX_AGE_SECONDS, 120))),
        "max_location_accuracy_meters": 150,
        "tracking_scope": "available_or_active_job",
    }


@router.put("/field-operations")
def update_field_operations_settings(
    payload: FieldOperationsUpdate,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_permission(Permission.SETTINGS_MANAGE_ORG)),
    db: Session = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    org = db.query(Organization).filter(Organization.id == tenant_id).with_for_update().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    gps_config = db.query(TenantGPSConfiguration).filter(
        TenantGPSConfiguration.tenant_id == tenant_id
    ).with_for_update().first()
    old_retention = gps_config.retention_days if gps_config else 30
    if not gps_config:
        gps_config = TenantGPSConfiguration(tenant_id=tenant_id, retention_days=payload.retention_days)
        db.add(gps_config)

    settings = dict(org.settings or {})
    old_field_ops = dict(settings.get("field_operations") or {})
    new_field_ops = {
        **old_field_ops,
        "location_freshness_seconds": payload.location_freshness_seconds,
    }
    settings["field_operations"] = new_field_ops
    org.settings = settings
    gps_config.retention_days = payload.retention_days

    audit_log(
        db,
        action="FIELD_OPERATIONS_SETTINGS_UPDATED",
        tenant_id=tenant_id,
        user_id=current_user.user_id,
        role=getattr(current_user.role, "value", str(current_user.role)),
        entity_type="organization_settings",
        entity_id=tenant_id,
        old_value={
            "retention_days": old_retention,
            "location_freshness_seconds": int(old_field_ops.get("location_freshness_seconds", min(LOCATION_MAX_AGE_SECONDS, 120))),
        },
        new_value={
            "retention_days": payload.retention_days,
            "location_freshness_seconds": payload.location_freshness_seconds,
        },
        details={"reason": payload.reason.strip()},
        request=request,
    )
    db.commit()
    return {
        "retention_days": payload.retention_days,
        "location_freshness_seconds": payload.location_freshness_seconds,
        "max_location_accuracy_meters": 150,
        "tracking_scope": "available_or_active_job",
    }


@router.get("/communications")
def get_communication_settings(
    current_user: AuthenticatedUser = Depends(require_permission(Permission.SETTINGS_MANAGE_ORG)),
    db: Session = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    rows = db.query(CommunicationChannelConfiguration).filter(
        CommunicationChannelConfiguration.tenant_id == tenant_id,
        func.upper(CommunicationChannelConfiguration.channel).in_(["EMAIL", "SMS"]),
    ).all()
    by_channel = {row.channel.upper(): row for row in rows}
    return {
        "channels": [
            {
                "channel": channel,
                "state": by_channel[channel].state if channel in by_channel else "ENABLED",
                "revision": by_channel[channel].revision if channel in by_channel else 0,
                "updated_at": by_channel[channel].updated_at if channel in by_channel else None,
                "updated_by": by_channel[channel].updated_by if channel in by_channel else "system_default",
            }
            for channel in ("EMAIL", "SMS")
        ]
    }


@router.put("/communications/{channel}")
def update_communication_channel(
    channel: str,
    payload: ChannelStateUpdate,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_permission(Permission.SETTINGS_MANAGE_ORG)),
    db: Session = Depends(get_db),
):
    normalized_channel = channel.strip().upper()
    if normalized_channel not in {"EMAIL", "SMS"}:
        raise HTTPException(status_code=400, detail="Only Email and SMS can be configured here")
    tenant_id = _tenant_id(current_user)
    row = db.query(CommunicationChannelConfiguration).filter(
        CommunicationChannelConfiguration.tenant_id == tenant_id,
        func.upper(CommunicationChannelConfiguration.channel) == normalized_channel,
    ).with_for_update().first()
    previous_state = row.state if row else "ENABLED"
    previous_revision = row.revision if row else 0
    if row is None:
        row = CommunicationChannelConfiguration(
            tenant_id=tenant_id,
            channel=normalized_channel,
            state=payload.state,
            revision=1,
            updated_by=current_user.user_id,
        )
        db.add(row)
    else:
        row.state = payload.state
        row.revision += 1
        row.updated_by = current_user.user_id

    if previous_state != payload.state:
        db.add(CommunicationConfigurationAudit(
            tenant_id=tenant_id,
            channel=normalized_channel,
            previous_state=previous_state,
            new_state=payload.state,
            previous_revision=previous_revision,
            new_revision=row.revision,
            actor_id=current_user.user_id,
            actor_tenant_id=tenant_id,
            reason=payload.reason.strip(),
            correlation_id=request.headers.get("X-Correlation-ID"),
        ))
        audit_log(
            db,
            action="COMMUNICATION_CHANNEL_UPDATED",
            tenant_id=tenant_id,
            user_id=current_user.user_id,
            role=getattr(current_user.role, "value", str(current_user.role)),
            entity_type="communication_channel",
            entity_id=normalized_channel,
            old_value={"state": previous_state, "revision": previous_revision},
            new_value={"state": payload.state, "revision": row.revision},
            details={"reason": payload.reason.strip()},
            request=request,
        )
    db.commit()
    db.refresh(row)
    return {
        "channel": normalized_channel,
        "state": row.state,
        "revision": row.revision,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.post("/communications/test")
async def send_test_message(
    payload: TestMessageRequest,
    current_user: AuthenticatedUser = Depends(require_permission(Permission.NOTIFICATIONS_SEND)),
    db: Session = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    row = db.query(CommunicationChannelConfiguration).filter(
        CommunicationChannelConfiguration.tenant_id == tenant_id,
        func.upper(CommunicationChannelConfiguration.channel) == payload.channel,
    ).first()
    if row and row.state != "ENABLED":
        raise HTTPException(status_code=409, detail=f"{payload.channel} is disabled for this organization")

    try:
        if payload.channel == "EMAIL":
            destination = TypeAdapter(EmailStr).validate_python(payload.destination)
            from ..services.email.email_service import EmailService
            sent = await EmailService().send_email(
                str(destination),
                "FieldOps notification test",
                "This is a test message from your FieldOps organization settings.",
            )
        else:
            from ..services.sms.twilio_service import TwilioSMSService
            service = TwilioSMSService()
            destination = service.validate_phone_number(payload.destination)
            result = service.send_sms(
                destination,
                "FieldOps test: your organization's SMS notifications are configured.",
            )
            sent = result.success
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:240]) from None

    if not sent:
        raise HTTPException(status_code=503, detail="The test message could not be delivered. Check provider configuration and try again.")
    return {"sent": True, "channel": payload.channel, "destination": str(destination)}
