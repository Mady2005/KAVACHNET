import enum, uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, Float, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

def now(): return datetime.now(timezone.utc)
class Role(str, enum.Enum): ADMIN="ADMIN"; SECURITY_ANALYST="SECURITY_ANALYST"; EDGE_OPERATOR="EDGE_OPERATOR"; NETWORK_OPERATOR="NETWORK_OPERATOR"; VIEWER="VIEWER"
class User(Base):
    __tablename__="users"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=lambda:str(uuid.uuid4()))
    username: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    email: Mapped[str]=mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str]=mapped_column(String(255))
    role: Mapped[Role]=mapped_column(Enum(Role), default=Role.VIEWER)
    is_active: Mapped[bool]=mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_login_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
class AuditLog(Base):
    __tablename__="audit_logs"
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[str|None]=mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str]=mapped_column(String(100), index=True); resource_type: Mapped[str]=mapped_column(String(80)); resource_id: Mapped[str|None]=mapped_column(String(100), nullable=True)
    timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, index=True); outcome: Mapped[str]=mapped_column(String(30)); details: Mapped[dict]=mapped_column(JSON, default=dict)
class KavachEventRecord(Base):
    __tablename__="kavach_events"
    event_id: Mapped[str]=mapped_column(String(36), primary_key=True); timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True); source_module: Mapped[str]=mapped_column(String(20), index=True); event_type: Mapped[str]=mapped_column(String(100)); severity: Mapped[str]=mapped_column(String(20), index=True); security_risk: Mapped[float]=mapped_column(Float); situation_risk: Mapped[float]=mapped_column(Float); network_health: Mapped[float]=mapped_column(Float); priority: Mapped[str]=mapped_column(String(2)); recommended_action: Mapped[str]=mapped_column(String(20)); evidence: Mapped[dict]=mapped_column(JSON); confidence: Mapped[float|None]=mapped_column(Float, nullable=True); status: Mapped[str]=mapped_column(String(20), index=True); correlation_id: Mapped[str|None]=mapped_column(String(100), nullable=True, index=True); simulation: Mapped[bool]=mapped_column(Boolean, default=False)
    __table_args__=(Index("ix_events_timestamp_module", "timestamp", "source_module"),)
class DecisionRecord(Base):
    __tablename__="decision_records"
    id: Mapped[int]=mapped_column(Integer, primary_key=True); event_id: Mapped[str]=mapped_column(ForeignKey("kavach_events.event_id"), index=True); selected_action: Mapped[str]=mapped_column(String(20)); policy_rule_id: Mapped[str]=mapped_column(String(100)); explanation: Mapped[str]=mapped_column(Text); input_scores: Mapped[dict]=mapped_column(JSON); timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class SystemSetting(Base):
    __tablename__="system_settings"
    id: Mapped[int]=mapped_column(Integer, primary_key=True); key: Mapped[str]=mapped_column(String(100), unique=True); value: Mapped[dict]=mapped_column(JSON); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now); updated_by: Mapped[str|None]=mapped_column(ForeignKey("users.id"), nullable=True)
class DatasetRegistry(Base):
    __tablename__="dataset_registry"
    dataset_id: Mapped[str]=mapped_column(String(36), primary_key=True, default=lambda:str(uuid.uuid4())); name: Mapped[str]=mapped_column(String(200), index=True); owning_member: Mapped[str]=mapped_column(String(80)); source_url: Mapped[str|None]=mapped_column(String(500), nullable=True); license: Mapped[str|None]=mapped_column(String(200), nullable=True); description: Mapped[str]=mapped_column(Text); provenance: Mapped[dict]=mapped_column(JSON, default=dict); import_status: Mapped[str]=mapped_column(String(30), default="PLANNED"); schema_metadata: Mapped[dict]=mapped_column(JSON, default=dict); checksum: Mapped[str|None]=mapped_column(String(128), nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class RevokedToken(Base):
    __tablename__="revoked_tokens"
    jti: Mapped[str]=mapped_column(String(36), primary_key=True); expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)

class SensorObservation(Base):
    __tablename__="sensor_observations"
    observation_id: Mapped[str]=mapped_column(String(64), primary_key=True)
    sensor_id: Mapped[str]=mapped_column(String(128), index=True)
    timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    simulation: Mapped[bool]=mapped_column(Boolean, default=False, index=True)
    payload: Mapped[dict]=mapped_column(JSON)
    result: Mapped[dict]=mapped_column(JSON)
    ingested_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    event_id: Mapped[str|None]=mapped_column(ForeignKey("kavach_events.event_id"), nullable=True)
    __table_args__=(Index("ix_sensor_observations_sensor_timestamp", "sensor_id", "timestamp"),
                    Index("uq_sensor_observations_event_id", "event_id", unique=True))

class IncidentRecord(Base):
    __tablename__="incidents"
    incident_id: Mapped[str]=mapped_column(String(36), primary_key=True, default=lambda:str(uuid.uuid4()))
    title: Mapped[str]=mapped_column(String(240))
    category: Mapped[str]=mapped_column(String(100), index=True)
    severity: Mapped[str]=mapped_column(String(20), index=True)
    status: Mapped[str]=mapped_column(String(20), default="OPEN", index=True)
    source_module: Mapped[str]=mapped_column(String(20), default="EDGE_AI", index=True)
    source_id: Mapped[str]=mapped_column(String(128), index=True)
    first_observed: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    last_observed: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    event_count: Mapped[int]=mapped_column(Integer, default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    resolved_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[str|None]=mapped_column(Text, nullable=True)
    __table_args__=(
        CheckConstraint("severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_incidents_severity"),
        CheckConstraint("status IN ('OPEN','ACKNOWLEDGED','RESOLVED')", name="ck_incidents_status"),
        CheckConstraint("event_count >= 0", name="ck_incidents_event_count"),
        Index("ix_incidents_source_category_status", "source_id", "category", "status"),
    )

class IncidentEventLink(Base):
    """Each canonical event belongs to at most one incident; one incident can link many events."""
    __tablename__="incident_event_links"
    event_id: Mapped[str]=mapped_column(ForeignKey("kavach_events.event_id"), primary_key=True)
    incident_id: Mapped[str]=mapped_column(ForeignKey("incidents.incident_id"), index=True)
    observation_id: Mapped[str|None]=mapped_column(ForeignKey("sensor_observations.observation_id"), nullable=True)
    linked_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    __table_args__=(UniqueConstraint("observation_id", name="uq_incident_event_observation"),)

class IncidentStatusHistory(Base):
    __tablename__="incident_status_history"
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str]=mapped_column(ForeignKey("incidents.incident_id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[str|None]=mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    from_status: Mapped[str|None]=mapped_column(String(20), nullable=True)
    to_status: Mapped[str]=mapped_column(String(20))
    note: Mapped[str|None]=mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, index=True)

class IncidentCorrelationLock(Base):
    __tablename__="incident_correlation_locks"
    group_key: Mapped[str]=mapped_column(String(64), primary_key=True)
    lock_version: Mapped[int]=mapped_column(Integer, default=0)
