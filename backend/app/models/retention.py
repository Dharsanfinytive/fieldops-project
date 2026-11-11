from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text

from ..database import Base


class RetentionWorkflow(Base):
    __tablename__ = "retention_workflows"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(50), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False, index=True)
    source_sentiment_message_id = Column(
        Integer,
        ForeignKey("sentiment_thread_messages.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    sentiment = Column(String(30), nullable=True)
    confidence = Column(Float, nullable=True)
    message = Column(Text, nullable=True)
    trigger_type = Column(String(30), nullable=False)
    severity = Column(String(20), nullable=False)
    branch = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    customer_lifetime_value = Column(Float, nullable=False, default=0)
    actions = Column(JSON, nullable=False, default=list)
    outcome = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class RetentionDiscountCode(Base):
    __tablename__ = "retention_discount_codes"

    code = Column(String(32), primary_key=True)
    workflow_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False, index=True)
    percentage = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    usage_limit = Column(Integer, nullable=False, default=1)
    usage_count = Column(Integer, nullable=False, default=0)


class RetentionCRMTask(Base):
    __tablename__ = "retention_crm_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False, index=True)
    task_type = Column(String(30), nullable=False, default="retention_call")
    status = Column(String(20), nullable=False, default="open")
    due_at = Column(DateTime(timezone=True), nullable=False)


class RetentionServiceCredit(Base):
    __tablename__ = "retention_service_credits"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    reason = Column(String(255), nullable=False)
    applied = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
