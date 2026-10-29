"""Idempotent correlation of persisted canonical Edge AI events."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    IncidentCorrelationLock, IncidentEventLink, IncidentRecord, IncidentStatusHistory,
    KavachEventRecord, SensorObservation,
)
from app.schemas.events import KavachEvent

from .correlation_policy import DEFAULT_POLICY, CorrelationPolicy, correlation_group, highest_severity

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("OPEN", "ACKNOWLEDGED")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stored_event(record: KavachEventRecord) -> KavachEvent:
    return KavachEvent.model_validate({
        "event_id": record.event_id, "timestamp": _utc(record.timestamp),
        "source_module": record.source_module, "event_type": record.event_type,
        "severity": record.severity, "security_risk": record.security_risk,
        "situation_risk": record.situation_risk, "network_health": record.network_health,
        "priority": record.priority, "recommended_action": record.recommended_action,
        "evidence": record.evidence, "confidence": record.confidence,
        "status": record.status, "correlation_id": record.correlation_id,
        "simulation": record.simulation,
    })


def _lock_group(db: Session, group_key: str) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        insert = sqlite_insert
    elif dialect == "postgresql":
        insert = postgresql_insert
    else:
        raise RuntimeError(f"Incident correlation does not support database dialect {dialect}")
    db.execute(insert(IncidentCorrelationLock).values(group_key=group_key, lock_version=0)
               .on_conflict_do_nothing(index_elements=[IncidentCorrelationLock.group_key]))
    # This write serializes same-group decisions on SQLite and row-locks on PostgreSQL.
    db.execute(update(IncidentCorrelationLock)
               .where(IncidentCorrelationLock.group_key == group_key)
               .values(lock_version=IncidentCorrelationLock.lock_version + 1))


def correlate_persisted_event(
    db: Session,
    event_id: str,
    *,
    actor_user_id: str | None = None,
    policy: CorrelationPolicy = DEFAULT_POLICY,
) -> IncidentRecord | None:
    """Attach an eligible persisted event to one active incident or create one.

    Event writes happen through the shared events endpoint first. This operation
    commits incident, link, observation reference, and initial status history
    together. Replaying it is safe because event_id is the association PK.
    """
    record = db.get(KavachEventRecord, event_id)
    if record is None:
        raise ValueError("Only a persisted canonical event can be correlated")
    event = _stored_event(record)
    group = correlation_group(event)
    if group is None:
        return None
    source_id, category = group
    timestamp = _utc(event.timestamp)
    group_key = hashlib.sha256(f"EDGE_AI\0{source_id}\0{category}".encode("utf-8")).hexdigest()

    try:
        _lock_group(db, group_key)
        # Recheck after taking the group lock: concurrent callers may have won.
        linked = db.get(IncidentEventLink, event_id)
        if linked:
            incident = db.get(IncidentRecord, linked.incident_id)
            observation_id = db.scalar(select(SensorObservation.observation_id).where(SensorObservation.event_id == event_id))
            if incident and linked.observation_id is None and observation_id:
                linked.observation_id = observation_id
                db.commit()
            else:
                db.rollback()
            return incident

        candidates = db.scalars(select(IncidentRecord).where(
            IncidentRecord.source_module == "EDGE_AI",
            IncidentRecord.source_id == source_id,
            IncidentRecord.category == category,
            IncidentRecord.status.in_(ACTIVE_STATUSES),
        ).with_for_update()).all()
        window = timedelta(seconds=policy.window_seconds)
        compatible = []
        for candidate in candidates:
            first, last = _utc(candidate.first_observed), _utc(candidate.last_observed)
            span_start, span_end = min(first, timestamp), max(last, timestamp)
            span = span_end - span_start
            if span <= window:
                compatible.append((span, candidate.incident_id, candidate))
        if compatible:
            incident = min(compatible, key=lambda item: (item[0], item[1]))[2]
            incident.first_observed = min(_utc(incident.first_observed), timestamp)
            incident.last_observed = max(_utc(incident.last_observed), timestamp)
            incident.severity = highest_severity(incident.severity, event.severity.value)
            incident.event_count += 1
            incident.updated_at = datetime.now(timezone.utc)
        else:
            incident = IncidentRecord(
                incident_id=str(uuid4()),
                title=f"{source_id}: {category.replace('_', ' ')}",
                category=category, severity=event.severity.value, status="OPEN",
                source_module="EDGE_AI", source_id=source_id,
                first_observed=timestamp, last_observed=timestamp, event_count=1,
            )
            db.add(incident)
            db.flush()
            db.add(IncidentStatusHistory(
                incident_id=incident.incident_id, actor_user_id=actor_user_id,
                from_status=None, to_status="OPEN", note="Incident created from canonical event.",
            ))

        observation_id = db.scalar(select(SensorObservation.observation_id).where(SensorObservation.event_id == event_id))
        db.add(IncidentEventLink(event_id=event_id, incident_id=incident.incident_id,
                                 observation_id=observation_id))
        db.commit()
        return incident
    except IntegrityError:
        db.rollback()
        # A competing request may have associated the same event while we were
        # waiting. The unique event key is the final idempotency guarantee.
        linked = db.get(IncidentEventLink, event_id)
        if linked:
            return db.get(IncidentRecord, linked.incident_id)
        raise
    except Exception:
        db.rollback()
        logger.exception("Incident correlation failed for canonical event %s", event_id)
        raise
