from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.sentiment_audit import SentimentAuditRecord


class SentimentAuditLogger:
    """
    Creates immutable audit records for sentiment-analysis events.

    Supported events:
        - sentiment_analysis
        - escalation
        - manager_action
    """

    ANALYSIS = "sentiment_analysis"
    ESCALATION = "escalation"
    MANAGER_ACTION = "manager_action"

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # LOG ANALYSIS
    # ------------------------------------------------------------------

    def log_analysis(
        self,
        analysis_result: dict[str, Any],
    ) -> SentimentAuditRecord:
        """
        Store one sentiment-analysis event.
        """

        return self._create_record(
            event_type=self.ANALYSIS,
            tenant_id=analysis_result["tenant_id"],
            customer_id=analysis_result.get("customer_id"),
            job_id=analysis_result.get("job_id"),
            input_text=analysis_result.get("input_text"),
            sentiment_label=analysis_result.get("sentiment_label"),
            confidence=analysis_result.get("confidence"),
            model_used=analysis_result.get("model_used"),
            cost=analysis_result.get("cost"),
            notes=analysis_result.get("notes"),
        )

    # ------------------------------------------------------------------
    # LOG ESCALATION
    # ------------------------------------------------------------------

    def log_escalation(
        self,
        escalation_data: dict[str, Any],
    ) -> SentimentAuditRecord:
        """
        Store one sentiment escalation event.
        """

        return self._create_record(
            event_type=self.ESCALATION,
            tenant_id=escalation_data["tenant_id"],
            customer_id=escalation_data.get("customer_id"),
            job_id=escalation_data.get("job_id"),
            manager_id=escalation_data.get("manager_id"),
            trigger_reason=escalation_data.get("trigger_reason"),
        )

    # ------------------------------------------------------------------
    # LOG MANAGER ACTION
    # ------------------------------------------------------------------

    def log_manager_action(
        self,
        action_data: dict[str, Any],
    ) -> SentimentAuditRecord:
        """
        Store one manager action against a sentiment escalation.
        """

        return self._create_record(
            event_type=self.MANAGER_ACTION,
            tenant_id=action_data["tenant_id"],
            customer_id=action_data.get("customer_id"),
            job_id=action_data.get("job_id"),
            manager_id=action_data.get("manager_id"),
            action=action_data.get("action"),
            notes=action_data.get("notes"),
        )

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------

    def search(
        self,
        *,
        tenant_id: str,
        customer_id: str | None = None,
        job_id: int | None = None,
        manager_id: str | None = None,
        sentiment_label: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[SentimentAuditRecord]:
        """
        Search sentiment audit records using optional filters.

        tenant_id is required to maintain tenant isolation.
        """

        query = (
            self.db.query(SentimentAuditRecord)
            .filter(
                SentimentAuditRecord.tenant_id == tenant_id
            )
        )

        if customer_id is not None:
            query = query.filter(
                SentimentAuditRecord.customer_id
                == str(customer_id)
            )

        if job_id is not None:
            query = query.filter(
                SentimentAuditRecord.job_id == job_id
            )

        if manager_id is not None:
            query = query.filter(
                SentimentAuditRecord.manager_id
                == str(manager_id)
            )

        if sentiment_label is not None:
            query = query.filter(
                SentimentAuditRecord.sentiment_label
                == sentiment_label
            )

        if start_date is not None:
            query = query.filter(
                SentimentAuditRecord.timestamp >= start_date
            )

        if end_date is not None:
            query = query.filter(
                SentimentAuditRecord.timestamp <= end_date
            )

        return (
            query
            .order_by(
                SentimentAuditRecord.timestamp.desc()
            )
            .all()
        )

    # ------------------------------------------------------------------
    # CANONICAL HASH
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_record_hash(
        *,
        tenant_id: str,
        event_type: str,
        customer_id: str | None,
        job_id: int | None,
        manager_id: str | None,
        input_text: str | None,
        sentiment_label: str | None,
        confidence: float | None,
        model_used: str | None,
        cost: float | Decimal | None,
        trigger_reason: str | None,
        action: str | None,
        notes: str | None,
        timestamp: datetime,
        sequence_number: int,
        previous_hash: str | None,
    ) -> str:
        """
        Generate the canonical SHA-256 hash.

        The timestamp is ALWAYS normalized to UTC before hashing.
        This prevents PostgreSQL timezone conversion from producing
        a different hash during verification.
        """

        # --------------------------------------------------------------
        # Normalize timestamp to UTC
        # --------------------------------------------------------------

        if timestamp.tzinfo is None:
            timestamp_utc = timestamp.replace(
                tzinfo=timezone.utc
            )
        else:
            timestamp_utc = timestamp.astimezone(
                timezone.utc
            )

        # --------------------------------------------------------------
        # Canonical record representation
        # --------------------------------------------------------------

        record_data = {
            "tenant_id": str(tenant_id),
            "event_type": str(event_type),
            "customer_id": (
                str(customer_id)
                if customer_id is not None
                else None
            ),
            "job_id": (
                int(job_id)
                if job_id is not None
                else None
            ),
            "manager_id": (
                str(manager_id)
                if manager_id is not None
                else None
            ),
            "input_text": input_text,
            "sentiment_label": sentiment_label,
            "confidence": (
                float(confidence)
                if confidence is not None
                else None
            ),
            "model_used": model_used,
            "cost": (
                str(cost)
                if cost is not None
                else None
            ),
            "trigger_reason": trigger_reason,
            "action": action,
            "notes": notes,
            "timestamp": timestamp_utc.isoformat(),
            "sequence_number": int(sequence_number),
            "previous_hash": previous_hash,
        }

        canonical_data = json.dumps(
            record_data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        return hashlib.sha256(
            canonical_data.encode("utf-8")
        ).hexdigest()

    # ------------------------------------------------------------------
    # CREATE RECORD
    # ------------------------------------------------------------------

    def _create_record(
        self,
        *,
        event_type: str,
        tenant_id: str,
        customer_id: str | None = None,
        job_id: int | None = None,
        manager_id: str | None = None,
        input_text: str | None = None,
        sentiment_label: str | None = None,
        confidence: float | None = None,
        model_used: str | None = None,
        cost: float | Decimal | None = None,
        trigger_reason: str | None = None,
        action: str | None = None,
        notes: str | None = None,
    ) -> SentimentAuditRecord:
        """
        Create and persist one immutable audit record.
        """

        if event_type not in {
            self.ANALYSIS,
            self.ESCALATION,
            self.MANAGER_ACTION,
        }:
            raise ValueError(
                f"Unsupported sentiment audit event type: {event_type}"
            )

        # --------------------------------------------------------------
        # Get previous record for this tenant
        # --------------------------------------------------------------

        previous_record = (
            self.db.query(SentimentAuditRecord)
            .filter(
                SentimentAuditRecord.tenant_id == tenant_id
            )
            .order_by(
                SentimentAuditRecord.sequence_number.desc()
            )
            .first()
        )

        previous_hash = (
            previous_record.record_hash
            if previous_record
            else None
        )

        sequence_number = (
            previous_record.sequence_number + 1
            if previous_record
            else 1
        )

        # --------------------------------------------------------------
        # Always create timestamp in UTC
        # --------------------------------------------------------------

        timestamp = datetime.now(timezone.utc)

        # --------------------------------------------------------------
        # Calculate hash using EXACT same method as verification
        # --------------------------------------------------------------

        record_hash = self._calculate_record_hash(
            tenant_id=tenant_id,
            event_type=event_type,
            customer_id=customer_id,
            job_id=job_id,
            manager_id=manager_id,
            input_text=input_text,
            sentiment_label=sentiment_label,
            confidence=confidence,
            model_used=model_used,
            cost=cost,
            trigger_reason=trigger_reason,
            action=action,
            notes=notes,
            timestamp=timestamp,
            sequence_number=sequence_number,
            previous_hash=previous_hash,
        )

        # --------------------------------------------------------------
        # Create record
        # --------------------------------------------------------------

        record = SentimentAuditRecord(
            tenant_id=tenant_id,
            event_type=event_type,
            customer_id=customer_id,
            job_id=job_id,
            manager_id=manager_id,
            input_text=input_text,
            sentiment_label=sentiment_label,
            confidence=confidence,
            model_used=model_used,
            cost=cost,
            trigger_reason=trigger_reason,
            action=action,
            notes=notes,
            timestamp=timestamp,
            sequence_number=sequence_number,
            previous_hash=previous_hash,
            record_hash=record_hash,
        )

        self.db.add(record)
        self.db.flush()

        return record

    # ------------------------------------------------------------------
    # VERIFY HASH CHAIN
    # ------------------------------------------------------------------

    def verify_hash_chain(
        self,
        tenant_id: str,
    ) -> dict[str, Any]:
        """
        Verify the complete SHA-256 hash chain for a tenant.
        """

        records = (
            self.db.query(SentimentAuditRecord)
            .filter(
                SentimentAuditRecord.tenant_id == tenant_id
            )
            .order_by(
                SentimentAuditRecord.sequence_number.asc()
            )
            .all()
        )

        if not records:
            return {
                "valid": True,
                "tenant_id": tenant_id,
                "record_count": 0,
                "message": "No audit records found.",
            }

        expected_sequence = 1
        expected_previous_hash = None

        for record in records:

            # ----------------------------------------------------------
            # Sequence validation
            # ----------------------------------------------------------

            if record.sequence_number != expected_sequence:
                return {
                    "valid": False,
                    "tenant_id": tenant_id,
                    "record_count": len(records),
                    "failed_record_id": record.id,
                    "reason": "Invalid sequence number.",
                    "expected_sequence": expected_sequence,
                    "actual_sequence": record.sequence_number,
                }

            # ----------------------------------------------------------
            # Previous hash validation
            # ----------------------------------------------------------

            if record.previous_hash != expected_previous_hash:
                return {
                    "valid": False,
                    "tenant_id": tenant_id,
                    "record_count": len(records),
                    "failed_record_id": record.id,
                    "reason": "Previous hash mismatch.",
                    "expected_previous_hash": expected_previous_hash,
                    "actual_previous_hash": record.previous_hash,
                }

            # ----------------------------------------------------------
            # Recalculate current hash
            # ----------------------------------------------------------

            calculated_hash = self._calculate_record_hash(
                tenant_id=record.tenant_id,
                event_type=record.event_type,
                customer_id=record.customer_id,
                job_id=record.job_id,
                manager_id=record.manager_id,
                input_text=record.input_text,
                sentiment_label=record.sentiment_label,
                confidence=record.confidence,
                model_used=record.model_used,
                cost=record.cost,
                trigger_reason=record.trigger_reason,
                action=record.action,
                notes=record.notes,
                timestamp=record.timestamp,
                sequence_number=record.sequence_number,
                previous_hash=record.previous_hash,
            )

            # ----------------------------------------------------------
            # Current hash validation
            # ----------------------------------------------------------

            if calculated_hash != record.record_hash:
                return {
                    "valid": False,
                    "tenant_id": tenant_id,
                    "record_count": len(records),
                    "failed_record_id": record.id,
                    "reason": "Record hash mismatch.",
                    "expected_hash": calculated_hash,
                    "actual_hash": record.record_hash,
                }

            expected_previous_hash = record.record_hash
            expected_sequence += 1

        return {
            "valid": True,
            "tenant_id": tenant_id,
            "record_count": len(records),
            "message": "Hash chain is valid.",
        }
