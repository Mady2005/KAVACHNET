import enum
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.events import Priority

class NetworkScenario(str, enum.Enum):
    HEALTHY="healthy"
    DEGRADED="degraded"
    DISCONNECTED="disconnected"
    RECOVERY="recovery"

class NetworkState(str, enum.Enum):
    HEALTHY="healthy"
    DEGRADED="degraded"
    DISCONNECTED="disconnected"

class MessageState(str, enum.Enum):
    PENDING="PENDING"
    IN_FLIGHT="IN_FLIGHT"
    DELIVERED="DELIVERED"
    RETRY_WAIT="RETRY_WAIT"
    FAILED="FAILED"
    EXPIRED="EXPIRED"

class DeliveryHandling(str, enum.Enum):
    SEND="SEND"
    SEND_PRIORITY="SEND_PRIORITY"
    SEND_COMPRESSED="SEND_COMPRESSED"
    DEFER="DEFER"

class CommunicationDecision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    network_state: NetworkState
    message_priority: Priority
    recommended_handling: DeliveryHandling
    reason: str
    compression_selected: bool
    delivery_deferred: bool
    queue_depth: int=Field(ge=0)

class TelemetryInput(BaseModel):
    model_config=ConfigDict(extra="forbid", allow_inf_nan=False)
    latency_ms: float=Field(ge=0,le=60000)
    packet_loss_pct: float=Field(ge=0,le=100)
    bandwidth_mbps: float=Field(ge=0,le=1000000)
    connected: bool
    jitter_ms: float|None=Field(default=None,ge=0,le=60000)
    delivery_failure_rate_pct: float=Field(default=0,ge=0,le=100)
    reason: str=Field(default="Custom simulated telemetry",min_length=1,max_length=500)

class ScenarioRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    scenario: NetworkScenario
    reason: str|None=Field(default=None,max_length=500)

class MessageCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    payload: dict[str,object]
    priority: Priority=Priority.P2
    idempotency_key: str|None=Field(default=None,min_length=1,max_length=100)

class NetworkTelemetryOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    scenario: str
    state: str
    timestamp: datetime
    latency_ms: float
    packet_loss_pct: float
    bandwidth_mbps: float
    connected: bool
    jitter_ms: float|None
    delivery_failure_rate_pct: float
    health_score: float
    reason: str
    simulated: bool=True

    @field_validator("timestamp")
    @classmethod
    def utc_aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

class QueueCounts(BaseModel):
    total: int
    by_state: dict[str,int]
    active_by_priority: dict[str,int]

class NetworkStatusOut(BaseModel):
    initialized: bool
    telemetry: NetworkTelemetryOut|None
    queue: QueueCounts

class TelemetryPage(BaseModel):
    items: list[NetworkTelemetryOut]
    offset: int
    limit: int

class MessageOut(BaseModel):
    message_id: str
    priority: Priority
    state: MessageState
    created_at: datetime
    updated_at: datetime
    attempts: int
    next_attempt_at: datetime|None
    last_attempt_at: datetime|None
    delivered_at: datetime|None
    expires_at: datetime
    last_error_category: str|None
    original_size: int
    stored_size: int
    compression_algorithm: str|None
    compression_ratio: float|None

    @field_validator("created_at","updated_at","next_attempt_at","last_attempt_at","delivered_at","expires_at")
    @classmethod
    def message_times_utc(cls,value):
        if value is None: return None
        if value.tzinfo is None or value.utcoffset() is None: return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

class AttemptOut(BaseModel):
    attempt_number: int
    attempted_at: datetime
    outcome: str
    error_category: str|None
    simulated_latency_ms: float|None

    @field_validator("attempted_at")
    @classmethod
    def attempt_time_utc(cls,value):
        if value.tzinfo is None or value.utcoffset() is None: return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

class DeliveryOut(AttemptOut):
    message_id: str

class MessagePage(BaseModel):
    items: list[MessageOut]
    offset: int
    limit: int
    total: int

class MessageDetailOut(BaseModel):
    message: MessageOut
    attempt_history: list[AttemptOut]

class DeliveryHistoryPage(BaseModel):
    items: list[DeliveryOut]
    offset: int
    limit: int

class NetworkMetricsOut(BaseModel):
    delivery_success_rate_pct: float|None
    success_rate_denominator: str
    delivery_attempts: int
    attempt_outcomes: dict[str,int]
    average_simulated_delivery_latency_ms: float|None
    total_original_bytes: int
    total_stored_bytes: int
    compression_ratio: float|None
    size_reduction_percent: float|None
    queue: QueueCounts

class DeliveryBatchResult(BaseModel):
    delivered: int
    retry_wait: int
    failed: int
    expired: int
    deferred: int
    decisions: list[CommunicationDecision]

class SimulationResult(BaseModel):
    telemetry: NetworkTelemetryOut
    delivery: DeliveryBatchResult

class MessageCreatedOut(BaseModel):
    message: MessageOut
    created: bool
    handling_decision: CommunicationDecision
