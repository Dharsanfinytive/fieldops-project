from sqlalchemy.orm import Session
from . import models

def sync_technician_status(technician: models.Technician):
    """
    Retained for compatibility. Workload changes do not control availability.
    """
    return technician

def update_workload_count(db: Session, technician_id: int, delta: int):
    """
    Update workload count and sync status.
    delta can be positive or negative.
    """
    tech = db.query(models.Technician).filter(models.Technician.technician_id == technician_id).first()
    if tech:
        tech.current_jobs = max(0, tech.current_jobs + delta)
        db.add(tech)
        return tech
    return None


def count_accepted_open_jobs(db: Session, technician_id: int) -> int:
    """Count jobs that were accepted and have not been completed or rejected."""
    accepted_open_statuses = {
        "ACCEPTED", "ENROUTE", "ONSITE", "INPROGRESS", "PAUSED",
        "ONHOLD", "HOLD", "ESCALATED",
    }
    jobs = (
        db.query(models.Job)
        .filter(models.Job.assigned_technician_id == technician_id)
        .all()
    )
    return sum(
        1
        for job in jobs
        if str(job.status or "").strip().upper().replace("_", "").replace("-", "").replace(" ", "")
        in accepted_open_statuses
    )


def reconcile_workload_count(db: Session, technician_id: int):
    """Set the stored workload to the authoritative accepted open job count."""
    tech = (
        db.query(models.Technician)
        .filter(models.Technician.technician_id == technician_id)
        .with_for_update()
        .first()
    )
    if tech:
        tech.current_jobs = count_accepted_open_jobs(db, technician_id)
        db.add(tech)
    return tech
