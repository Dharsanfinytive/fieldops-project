"""Persist sentiment analysis provenance and escalation idempotency.

Revision ID: f02c9a47d631
Revises: 2d3db6c0e7a4, 30cd13a2cc68, a27d91c42e60, d24c6ef10871, e39a601d2b71
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f02c9a47d631"
down_revision: Union[str, Sequence[str], None] = (
    "2d3db6c0e7a4",
    "30cd13a2cc68",
    "a27d91c42e60",
    "d24c6ef10871",
    "e39a601d2b71",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Source IDs from customer-support tables may repeat across organizations.
    # Scope their uniqueness to the tenant boundary.
    op.drop_index("uq_sentiment_thread_source", table_name="sentiment_thread_messages")
    op.create_index(
        "uq_sentiment_thread_source",
        "sentiment_thread_messages",
        ["tenant_id", "source_type", "source_id"],
        unique=True,
    )
    op.add_column(
        "sentiment_thread_messages",
        sa.Column("language", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "sentiment_thread_messages",
        sa.Column(
            "analysis_source",
            sa.String(length=30),
            nullable=False,
            server_default="AI",
        ),
    )
    op.add_column(
        "sentiment_escalations",
        sa.Column("source_sentiment_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_sentiment_escalation_source_message",
        "sentiment_escalations",
        "sentiment_thread_messages",
        ["source_sentiment_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sentiment_escalations_source_sentiment_message_id",
        "sentiment_escalations",
        ["source_sentiment_message_id"],
        unique=True,
    )
    op.add_column(
        "retention_workflows",
        sa.Column("source_sentiment_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_retention_workflow_source_sentiment_message",
        "retention_workflows",
        "sentiment_thread_messages",
        ["source_sentiment_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_retention_workflows_source_sentiment_message_id",
        "retention_workflows",
        ["source_sentiment_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retention_workflows_source_sentiment_message_id",
        table_name="retention_workflows",
    )
    op.drop_constraint(
        "fk_retention_workflow_source_sentiment_message",
        "retention_workflows",
        type_="foreignkey",
    )
    op.drop_column("retention_workflows", "source_sentiment_message_id")
    op.drop_index(
        "ix_sentiment_escalations_source_sentiment_message_id",
        table_name="sentiment_escalations",
    )
    op.drop_constraint(
        "fk_sentiment_escalation_source_message",
        "sentiment_escalations",
        type_="foreignkey",
    )
    op.drop_column("sentiment_escalations", "source_sentiment_message_id")
    op.drop_column("sentiment_thread_messages", "analysis_source")
    op.drop_column("sentiment_thread_messages", "language")
    op.drop_index("uq_sentiment_thread_source", table_name="sentiment_thread_messages")
    op.create_index(
        "uq_sentiment_thread_source",
        "sentiment_thread_messages",
        ["source_type", "source_id"],
        unique=True,
    )
