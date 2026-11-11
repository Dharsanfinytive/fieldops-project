from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.sentiment_escalation import SentimentEscalation
from datetime import datetime, timezone


router = APIRouter(
    prefix="/admin/escalations",
    tags=["Sentiment Escalations"],
)

@router.get("")
def get_escalations(
    db: Session = Depends(get_db),
):
    escalations = (
        db.query(SentimentEscalation)
        .order_by(SentimentEscalation.created_at.desc())
        .all()
    )

    return {
        "count": len(escalations),
        "escalations": escalations,
    }

@router.post("/{escalation_id}/acknowledge")
def acknowledge_escalation(
    escalation_id: int,
    db: Session = Depends(get_db),
):
    escalation = (
        db.query(SentimentEscalation)
        .filter(SentimentEscalation.id == escalation_id)
        .first()
    )

    if not escalation:
        raise HTTPException(
            status_code=404,
            detail="Escalation not found",
        )

    if escalation.status in {"ACKNOWLEDGED", "RESOLVED"}:
        return {
            "message": "Escalation already acknowledged",
            "escalation_id": escalation.id,
            "status": escalation.status,
            "acknowledged_at": escalation.acknowledged_at,
        }

    escalation.status = "ACKNOWLEDGED"
    escalation.acknowledged_at = datetime.now(timezone.utc)
    escalation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(escalation)

    return {
        "message": "Escalation acknowledged successfully",
        "escalation_id": escalation.id,
        "status": escalation.status,
        "acknowledged_at": escalation.acknowledged_at,
    }

@router.post("/{escalation_id}/resolve")
def resolve_escalation(
    escalation_id: int,
    resolution_notes: str | None = None,
    db: Session = Depends(get_db),
):
    escalation = (
        db.query(SentimentEscalation)
        .filter(SentimentEscalation.id == escalation_id)
        .first()
    )

    if not escalation:
        raise HTTPException(
            status_code=404,
            detail="Escalation not found",
        )

    if escalation.status == "RESOLVED":
        return {
            "message": "Escalation already resolved",
            "escalation_id": escalation.id,
            "status": escalation.status,
            "resolved_at": escalation.resolved_at,
            "resolution_notes": escalation.resolution_notes,
        }

    now = datetime.now(timezone.utc)

    escalation.status = "RESOLVED"
    escalation.resolved_at = now
    escalation.resolution_notes = resolution_notes
    escalation.updated_at = now

    db.commit()
    db.refresh(escalation)

    return {
        "message": "Escalation resolved successfully",
        "escalation_id": escalation.id,
        "status": escalation.status,
        "resolved_at": escalation.resolved_at,
        "resolution_notes": escalation.resolution_notes,
    }
