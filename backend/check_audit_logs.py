import sys
from app.database import SessionLocal
from app.models import Job, Organization, Technician
from app.models.enterprise_audit import EnterpriseAuditLog

def show_recent_jobs(limit=10):
    db = SessionLocal()
    try:
        jobs = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
        print(f"\n{'='*30} RECENT {len(jobs)} JOBS {'='*30}")
        header = f"{'Job ID':<8} | {'Status':<13} | {'Service':<18} | {'Customer':<20} | {'Tech ID':<8} | {'Created At'}"
        print(header)
        print("-" * len(header))
        for j in jobs:
            created = j.created_at.strftime("%Y-%m-%d %H:%M") if j.created_at else "N/A"
            tech = str(j.assigned_technician_id) if j.assigned_technician_id else "None"
            print(f"#{j.id:<7} | {j.status:<13} | {j.service_type[:17]:<18} | {j.customer_name[:19]:<20} | {tech:<8} | {created}")
        print("-" * len(header))
        print()
        print("💡 If you want to see any specific Job's full history use this command --->: python check_audit_logs.py <job_id>\n")
    finally:
        db.close()

def show_job_logs(job_id):
    db = SessionLocal()
    try:
        query = db.query(EnterpriseAuditLog).filter(
            EnterpriseAuditLog.entity_type == "job",
            EnterpriseAuditLog.entity_id == str(job_id)
        ).order_by(EnterpriseAuditLog.timestamp.asc())
        
        logs = query.all()
        print(f"\n{'='*25} AUDIT LOGS FOR JOB #{job_id} {'='*25}")
        if not logs:
            print("No audit logs found for this job.")
            return

        for l in logs:
            time_str = l.timestamp.strftime("%Y-%m-%d %H:%M:%S") if l.timestamp else "N/A"
            user = l.user_email or l.user_id or "System"
            print(f"[{time_str}] Action: {l.action:<25} | Role: {l.role or 'N/A':<12} | User: {user}")
            if l.new_value:
                print(f"   -> Details: {l.new_value}")
        print("=" * 70 + "\n")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        show_job_logs(sys.argv[1])
    else:
        show_recent_jobs()
