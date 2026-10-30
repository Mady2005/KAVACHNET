import enum, uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, Float, Uuid
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
    event_id: Mapped[str]=mapped_column(String(36), primary_key=True); timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True); source_module: Mapped[str]=mapped_column(String(20), index=True); event_type: Mapped[str]=mapped_column(String(100)); severity: Mapped[str]=mapped_column(String(20), index=True); security_risk: Mapped[float]=mapped_column(Float); situation_risk: Mapped[float|None]=mapped_column(Float, nullable=True); network_health: Mapped[float|None]=mapped_column(Float, nullable=True); priority: Mapped[str]=mapped_column(String(2)); recommended_action: Mapped[str]=mapped_column(String(20)); evidence: Mapped[dict]=mapped_column(JSON); confidence: Mapped[float|None]=mapped_column(Float, nullable=True); status: Mapped[str]=mapped_column(String(20), index=True); correlation_id: Mapped[str|None]=mapped_column(String(100), nullable=True, index=True); simulation: Mapped[bool]=mapped_column(Boolean, default=False)
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
