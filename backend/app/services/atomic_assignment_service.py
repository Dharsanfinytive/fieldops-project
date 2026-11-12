"""Database-authoritative acceptance for broadcast job offers."""

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import exists, update
from sqlalchemy.orm import Session

from app.models import AuditEvent, InAppNotification, Job, JobAssignment, Technician


@dataclass(frozen=True)
class AssignmentResult:
    job_id: int
    technician_id: int
    organization_id: str
    status: str = "ASSIGNED"
    already_accepted: bool = False


class AssignmentConflict(Exception):
    """The offer is invalid or another technician won the job."""


def accept_broadcast_offer(
    db: Session, *, job_id: int, tenant_id: str, technician_id: int
) -> AssignmentResult:
    """Atomically claim an unassigned job through an active persisted offer.

    The conditional UPDATE takes the PostgreSQL row lock. Concurrent claimants
    re-evaluate the predicate after waiting, so only one can receive a row.
    All related records are committed together by the caller's transaction.
    """
    try:
        existing = (
            db.query(Job)
            .filter(
                Job.id == job_id,
                Job.tenant_id == tenant_id,
                Job.assigned_technician_id == technician_id,
                Job.status == "ASSIGNED",
            )
            .first()
        )
        accepted = (
            db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.technician_id == technician_id,
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.status == "ACCEPTED",
            )
            .first()
        )
        if existing is not None and accepted is not None:
            return AssignmentResult(job_id, technician_id, tenant_id, already_accepted=True)

        active_offer = exists().where(
            JobAssignment.job_id == Job.id,
            JobAssignment.technician_id == technician_id,
            JobAssignment.tenant_id == tenant_id,
            JobAssignment.status == "PENDING",
            JobAssignment.assigned_at.is_not(None),
            Technician.technician_id == JobAssignment.technician_id,
            Technician.tenant_id == JobAssignment.tenant_id,
        )
        now = datetime.now(timezone.utc)
        claim = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.tenant_id == tenant_id,
                Job.assigned_technician_id.is_(None),
                Job.status.in_(("CREATED", "QUEUED", "UNASSIGNED", "OFFERED")),
                active_offer,
            )
            .values(
                assigned_technician_id=technician_id,
                status="ASSIGNED",
                assigned_at=now,
            )
            .returning(Job.id, Job.assigned_technician_id, Job.tenant_id)
            .execution_options(synchronize_session=False)
        )
        winner = db.execute(claim).first()
        if winner is None:
            db.rollback()
            raise AssignmentConflict("This job has already been assigned or the offer is no longer active")

        offer = (
            db.query(JobAssignment)
            .filter(
                JobAssignment.job_id == job_id,
                JobAssignment.technician_id == technician_id,
                JobAssignment.tenant_id == tenant_id,
                JobAssignment.status == "PENDING",
            )
            .with_for_update()
            .first()
        )
        if offer is None:
            db.rollback()
            raise AssignmentConflict("No active offer exists for this technician")
        offer.status = "ACCEPTED"
        offer.responded_at = now
        offer.is_current = False
        losing_offers = db.query(JobAssignment).filter(
            JobAssignment.job_id == job_id,
            JobAssignment.status == "PENDING",
            JobAssignment.id != offer.id,
        ).all()
        for losing_offer in losing_offers:
            losing_offer.status = "LOST"
            losing_offer.responded_at = now
            losing_offer.is_current = False
            losing_technician = db.query(Technician).filter(
                Technician.technician_id == losing_offer.technician_id,
                Technician.tenant_id == losing_offer.tenant_id,
            ).first()
            if losing_technician is not None and losing_technician.tenant_id:
                db.add(InAppNotification(
                    id=str(uuid.uuid4()),
                    tenant_id=str(losing_technician.tenant_id),
                    tech_id=losing_technician.tech_id,
                    recipient_role="technician",
                    recipient_id=str(losing_technician.tech_id or losing_technician.technician_id),
                    group_key=f"job_{job_id}_lost",
                    job_id=str(job_id),
                    type="JOB_OFFER_LOST",
                    title="Job No Longer Available",
                    body=f"Job #{job_id} was accepted by another technician.",
                    status="UNREAD",
                    priority="NORMAL",
                    notification_metadata={"event_id": f"job:{job_id}:offer-lost:{losing_offer.technician_id}"},
                ))
        db.add(AuditEvent(
            tech_id=str(technician_id), tenant_id=tenant_id,
            event_type="JOB_ASSIGNMENT_CONFIRMED", old_status="OFFERED",
            new_status="ASSIGNED", job_id=str(job_id),
            details={"technician_id": technician_id, "organization_id": tenant_id},
            timestamp=now,
        ))
        db.commit()
        return AssignmentResult(job_id, technician_id, tenant_id)
    except AssignmentConflict:
        raise
    except Exception:
        db.rollback()
        raise
