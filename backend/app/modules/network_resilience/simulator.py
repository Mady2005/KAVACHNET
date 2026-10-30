"""Controlled synthetic network scenarios; never reads or changes host networking."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import AuditLog, NetworkObservationRecord, NetworkTelemetryRecord
from app.schemas.events import Action, EventStatus, KavachEvent, Priority, Severity, SourceModule
from app.schemas.network import NetworkScenario, TelemetryInput
from app.services.event_store import add_event
from app.modules.network_resilience.health_score import score_telemetry

SCENARIOS={
    NetworkScenario.HEALTHY:dict(latency_ms=25,packet_loss_pct=0.1,bandwidth_mbps=100,connected=True,jitter_ms=2,delivery_failure_rate_pct=0),
    NetworkScenario.DEGRADED:dict(latency_ms=350,packet_loss_pct=20,bandwidth_mbps=2,connected=True,jitter_ms=80,delivery_failure_rate_pct=20),
    NetworkScenario.DISCONNECTED:dict(latency_ms=5000,packet_loss_pct=100,bandwidth_mbps=0,connected=False,jitter_ms=None,delivery_failure_rate_pct=100),
    NetworkScenario.RECOVERY:dict(latency_ms=100,packet_loss_pct=1,bandwidth_mbps=25,connected=True,jitter_ms=15,delivery_failure_rate_pct=0),
}

def latest_telemetry(db:Session)->NetworkTelemetryRecord|None:
    return db.scalar(select(NetworkTelemetryRecord).order_by(NetworkTelemetryRecord.timestamp.desc(),NetworkTelemetryRecord.id.desc()).limit(1))

def _event(record:NetworkTelemetryRecord)->KavachEvent:
    state=record.state
    severity=Severity.CRITICAL if state=="disconnected" else Severity.MEDIUM if state=="degraded" else Severity.INFO
    priority=Priority.P0 if state=="disconnected" else Priority.P1 if state=="degraded" else Priority.P3
    return KavachEvent(event_id=uuid4(),timestamp=record.timestamp,source_module=SourceModule.NETWORK,event_type=f"network_{state}",severity=severity,security_risk=0,situation_risk=round(1-record.health_score/100,4),network_health=record.health_score,priority=priority,recommended_action=Action.ALERT if state!="healthy" else Action.ALLOW,evidence={"simulated":True,"scenario":record.scenario,"reason":record.reason,"latency_ms":record.latency_ms,"packet_loss_pct":record.packet_loss_pct,"bandwidth_mbps":record.bandwidth_mbps,"connected":record.connected},confidence=1,status=EventStatus.NEW,simulation=True)

def apply_telemetry(db:Session,telemetry:TelemetryInput,scenario:str,reason:str,actor_user_id:str)->NetworkTelemetryRecord:
    now=datetime.now(timezone.utc)
    score=score_telemetry(telemetry)
    previous=latest_telemetry(db)
    record=NetworkTelemetryRecord(scenario=scenario,state=score.state.value,timestamp=now,latency_ms=telemetry.latency_ms,packet_loss_pct=telemetry.packet_loss_pct,bandwidth_mbps=telemetry.bandwidth_mbps,connected=telemetry.connected,jitter_ms=telemetry.jitter_ms,delivery_failure_rate_pct=telemetry.delivery_failure_rate_pct,health_score=score.score,reason=reason or telemetry.reason)
    db.add(record); db.flush()
    for metric,value,unit in (("round_trip_latency",telemetry.latency_ms,"ms"),("packet_loss",telemetry.packet_loss_pct,"percent"),("throughput",telemetry.bandwidth_mbps,"Mbps"),("jitter",telemetry.jitter_ms,"ms"),("application_delivery_failure",telemetry.delivery_failure_rate_pct,"percent"),("network_health_score",score.score,"score")):
        db.add(NetworkObservationRecord(timestamp_utc=now,source_type="SIMULATED",source_name="controlled_simulator",measurement_type=metric,value=value,unit=unit,target_label="simulated-channel",measurement_method="controlled scenario generator",experiment_id=None,observation_metadata={"scenario":scenario,"telemetry_record_id":record.id}))
    changed=(previous is None or previous.state!=record.state or previous.connected!=record.connected or record.state=="disconnected" or (previous.state=="disconnected" and record.connected))
    if changed: add_event(db,_event(record),actor_user_id)
    db.add(AuditLog(actor_user_id=actor_user_id,action="network.simulator.change",resource_type="network_telemetry",resource_id=str(record.id),outcome="success",details={"scenario":scenario,"state":record.state,"health_score":record.health_score,"simulated":True}))
    db.commit(); db.refresh(record)
    return record

def apply_scenario(db:Session,scenario:NetworkScenario,actor_user_id:str,reason:str|None=None)->NetworkTelemetryRecord:
    profile=SCENARIOS[scenario]
    telemetry=TelemetryInput(**profile,reason=reason or f"Controlled {scenario.value} scenario")
    return apply_telemetry(db,telemetry,scenario.value,reason or telemetry.reason,actor_user_id)
