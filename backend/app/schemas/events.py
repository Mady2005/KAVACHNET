import enum
from datetime import datetime, timezone
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
class SourceModule(str, enum.Enum): SECURITY="SECURITY"; EDGE_AI="EDGE_AI"; NETWORK="NETWORK"; SYSTEM="SYSTEM"
class Severity(str, enum.Enum): INFO="INFO"; LOW="LOW"; MEDIUM="MEDIUM"; HIGH="HIGH"; CRITICAL="CRITICAL"
class Priority(str, enum.Enum): P0="P0"; P1="P1"; P2="P2"; P3="P3"
class Action(str, enum.Enum): ALLOW="ALLOW"; WARN="WARN"; SANITIZE="SANITIZE"; BLOCK="BLOCK"; QUARANTINE="QUARANTINE"; ALERT="ALERT"; SUMMARIZE="SUMMARIZE"; COMPRESS="COMPRESS"; PRIORITIZE="PRIORITIZE"; STORE="STORE"; FORWARD="FORWARD"
class EventStatus(str, enum.Enum): NEW="NEW"; ACKNOWLEDGED="ACKNOWLEDGED"; QUEUED="QUEUED"; DELIVERED="DELIVERED"; BLOCKED="BLOCKED"; RESOLVED="RESOLVED"
class KavachEvent(BaseModel):
    model_config=ConfigDict(extra="forbid", allow_inf_nan=False)
    event_id: UUID; timestamp: datetime; source_module: SourceModule; event_type: str=Field(min_length=1,max_length=100); severity: Severity; security_risk: float=Field(ge=0,le=1); situation_risk: float=Field(ge=0,le=1); network_health: float=Field(ge=0,le=100); priority: Priority; recommended_action: Action; evidence: dict[str,object]=Field(default_factory=dict); confidence: float|None=Field(default=None,ge=0,le=1); status: EventStatus=EventStatus.NEW; correlation_id: str|None=Field(default=None,max_length=100); simulation: bool=False
    @field_validator("timestamp")
    @classmethod
    def utc_aware(cls,v):
        if v.tzinfo is None or v.utcoffset() is None: raise ValueError("timestamp must include a timezone")
        return v.astimezone(timezone.utc)

class DecisionResult(BaseModel):
    """Policy recommendation returned by the central decision engine.

    This records a recommendation; module owners remain responsible for
    authorizing and executing any action with external effects.
    """
    model_config=ConfigDict(extra="forbid", allow_inf_nan=False)
    selected_action: Action
    policy_rule_id: str=Field(min_length=1,max_length=100)
    explanation: str=Field(min_length=1)
    input_scores: dict[str,float]
    timestamp: datetime=Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("timestamp")
    @classmethod
    def decision_timestamp_utc(cls,v):
        if v.tzinfo is None or v.utcoffset() is None: raise ValueError("timestamp must include a timezone")
        return v.astimezone(timezone.utc)
