"""Enforce one accepted broadcast offer per job."""

from alembic import op
import sqlalchemy as sa


revision = "9c17b441eaf2"
down_revision = "f02c9a47d631"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(sa.text(
        "SELECT job_id FROM job_assignments WHERE status = 'ACCEPTED' "
        "GROUP BY job_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce one accepted offer per job: duplicate ACCEPTED "
            f"records exist for job {duplicate[0]}. Reconcile them first."
        )
    duplicate_offer = bind.execute(sa.text(
        "SELECT job_id, technician_id FROM job_assignments "
        "WHERE status = 'PENDING' GROUP BY job_id, technician_id "
        "HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate_offer:
        raise RuntimeError(
            "Cannot enforce one pending offer per technician/job: duplicate "
            f"records exist for job {duplicate_offer[0]}, technician "
            f"{duplicate_offer[1]}. Reconcile them first."
        )
    op.create_index(
        "uq_job_assignments_one_accepted_per_job",
        "job_assignments",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACCEPTED'"),
        sqlite_where=sa.text("status = 'ACCEPTED'"),
    )
    op.create_index(
        "uq_job_assignments_one_active_offer_per_technician",
        "job_assignments",
        ["job_id", "technician_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
        sqlite_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_job_assignments_one_active_offer_per_technician",
        table_name="job_assignments",
    )
    op.drop_index("uq_job_assignments_one_accepted_per_job", table_name="job_assignments")
