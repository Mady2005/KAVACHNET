"""Durable SQLite store-and-forward queue with a local simulated receiver."""
import gzip
import hashlib
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models import AuditLog, KavachEventRecord, NetworkDeliveryAttempt, NetworkMessageRecord, NetworkTelemetryRecord
from app.schemas.events import Action, KavachEvent, Priority, Severity, SourceModule
from app.schemas.network import MessageState, NetworkState
from app.modules.network_resilience.communication_policy import decide_delivery
from app.services.event_store import add_event
from app.modules.network_resilience.simulator import latest_telemetry

PRIORITY_ORDER={"P0":0,"P1":1,"P2":2,"P3":3}
ACTIVE_STATES=(MessageState.PENDING.value,MessageState.RETRY_WAIT.value)

class IdempotencyConflict(Exception):
    """An idempotency key was reused with a different payload."""

class PayloadTooLarge(Exception):
    """Serialized message exceeds the configured uncompressed payload limit."""

def serialize_payload(payload:dict)->tuple[bytes,bytes,str,str|None]:
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
    if len(raw)>settings.network_max_payload_bytes:
        raise PayloadTooLarge(f"Payload exceeds {settings.network_max_payload_bytes} bytes")
    compressed=gzip.compress(raw,mtime=0)
    if len(compressed)<len(raw): return raw,compressed,"gzip","gzip"
    return raw,raw,"identity",None

def _message_event(message:NetworkMessageRecord,kind:str,health:float=0)->KavachEvent:
    severity=Severity.HIGH if kind.endswith("failed") else Severity.INFO
    action=Action.ALERT if severity==Severity.HIGH else Action.STORE
    return KavachEvent(event_id=uuid.uuid4(),timestamp=datetime.now(timezone.utc),source_module=SourceModule.NETWORK,event_type=kind,severity=severity,security_risk=0,situation_risk=0,network_health=health,priority=Priority(message.priority),recommended_action=action,evidence={"simulated":True,"message_id":message.message_id,"attempts":message.attempts,"state":message.state,"original_size":message.original_size,"stored_size":message.stored_size},confidence=1,simulation=True)

def enqueue_message(db:Session,payload:dict,priority:Priority,actor_user_id:str,idempotency_key:str|None=None)->tuple[NetworkMessageRecord,bool]:
    raw,stored,encoding,algorithm=serialize_payload(payload)
    digest=hashlib.sha256(raw).hexdigest()
    if idempotency_key:
        prior=db.scalar(select(NetworkMessageRecord).where(NetworkMessageRecord.idempotency_key==idempotency_key))
        if prior:
            if prior.payload_sha256!=digest: raise IdempotencyConflict("Idempotency key already belongs to a different payload")
            return prior,False
    now=datetime.now(timezone.utc)
    message=NetworkMessageRecord(message_id=str(uuid.uuid4()),idempotency_key=idempotency_key,payload_sha256=digest,payload_blob=stored,payload_encoding=encoding,compression_algorithm=algorithm,original_size=len(raw),stored_size=len(stored),priority=priority.value,state=MessageState.PENDING.value,attempts=0,created_at=now,updated_at=now,expires_at=now+timedelta(seconds=settings.network_message_ttl_seconds))
    db.add(message)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            prior=db.scalar(select(NetworkMessageRecord).where(NetworkMessageRecord.idempotency_key==idempotency_key))
            if prior and prior.payload_sha256==digest: return prior,False
        raise IdempotencyConflict("Idempotency key is already in use")
    network=latest_telemetry(db)
    add_event(db,_message_event(message,"network_message_queued",network.health_score if network else 0),actor_user_id)
    db.add(AuditLog(actor_user_id=actor_user_id,action="network.message.enqueue",resource_type="network_message",resource_id=message.message_id,outcome="success",details={"priority":priority.value,"original_size":len(raw),"stored_size":len(stored),"compressed":encoding=="gzip"}))
    db.commit(); db.refresh(message)
    return message,True

def _decoded_payload(message:NetworkMessageRecord)->dict:
    raw=gzip.decompress(message.payload_blob) if message.payload_encoding=="gzip" else message.payload_blob
    if hashlib.sha256(raw).hexdigest()!=message.payload_sha256: raise ValueError("stored payload checksum mismatch")
    result=json.loads(raw.decode("utf-8"))
    if not isinstance(result,dict): raise ValueError("stored payload is not a JSON object")
    return result

def _claim(db:Session,message_id:str,now:datetime)->NetworkMessageRecord|None:
    eligible=or_(NetworkMessageRecord.next_attempt_at.is_(None),NetworkMessageRecord.next_attempt_at<=now)
    changed=db.execute(update(NetworkMessageRecord).where(NetworkMessageRecord.message_id==message_id,NetworkMessageRecord.state.in_(ACTIVE_STATES),NetworkMessageRecord.expires_at>now,eligible).values(state=MessageState.IN_FLIGHT.value,attempts=NetworkMessageRecord.attempts+1,last_attempt_at=now,updated_at=now).execution_options(synchronize_session=False))
    if changed.rowcount!=1:
        db.rollback()
        return None
    db.commit()
    message=db.get(NetworkMessageRecord,message_id)
    if message is not None: db.refresh(message)
    return message

def _retry_delay(message_id:str,attempt:int)->float:
    raw=min(settings.network_retry_base_seconds*(2**max(attempt-1,0)),settings.network_retry_max_seconds)
    # Deterministic bounded jitter keeps retry tests reproducible.
    factor=0.9+random.Random(f"{message_id}:{attempt}").random()*0.2
    return min(settings.network_retry_max_seconds,raw*factor)

def expire_messages(db:Session,now:datetime|None=None)->int:
    now=now or datetime.now(timezone.utc)
    changed=db.execute(update(NetworkMessageRecord).where(NetworkMessageRecord.state.in_(ACTIVE_STATES),NetworkMessageRecord.expires_at<=now).values(state=MessageState.EXPIRED.value,updated_at=now,next_attempt_at=None).execution_options(synchronize_session=False))
    db.commit()
    return changed.rowcount or 0

def process_eligible(db:Session,actor_user_id:str|None=None,batch_size:int=20)->dict:
    now=datetime.now(timezone.utc)
    result_expired=expire_messages(db,now)
    telemetry=latest_telemetry(db)
    result={"delivered":0,"retry_wait":0,"failed":0,"expired":result_expired,"deferred":0,"decisions":[]}
    if telemetry is None or not telemetry.connected:
        result["deferred"]=db.scalar(select(func.count()).select_from(NetworkMessageRecord).where(NetworkMessageRecord.state.in_(ACTIVE_STATES))) or 0
        network_state=NetworkState.DISCONNECTED
        depth=result["deferred"]
        pending=db.scalars(select(NetworkMessageRecord).where(NetworkMessageRecord.state.in_(ACTIVE_STATES)).order_by(case((NetworkMessageRecord.priority=="P0",0),(NetworkMessageRecord.priority=="P1",1),(NetworkMessageRecord.priority=="P2",2),else_=3),NetworkMessageRecord.created_at,NetworkMessageRecord.message_id).limit(min(max(batch_size,1),100))).all()
        result["decisions"]=[decide_delivery(network_state,message.priority,message.payload_encoding=="gzip",depth).model_dump(mode="json") for message in pending]
        return result
    rank=case((NetworkMessageRecord.priority=="P0",0),(NetworkMessageRecord.priority=="P1",1),(NetworkMessageRecord.priority=="P2",2),else_=3)
    eligible=or_(NetworkMessageRecord.next_attempt_at.is_(None),NetworkMessageRecord.next_attempt_at<=now)
    ids=db.scalars(select(NetworkMessageRecord.message_id).where(NetworkMessageRecord.state.in_(ACTIVE_STATES),NetworkMessageRecord.expires_at>now,eligible).order_by(rank,NetworkMessageRecord.created_at,NetworkMessageRecord.message_id).limit(min(max(batch_size,1),100))).all()
    for message_id in ids:
        message=_claim(db,message_id,datetime.now(timezone.utc))
        if message is None: continue
        depth=db.scalar(select(func.count()).select_from(NetworkMessageRecord).where(NetworkMessageRecord.state.in_(ACTIVE_STATES))) or 0
        policy=decide_delivery(NetworkState(telemetry.state),message.priority,message.payload_encoding=="gzip",depth)
        result["decisions"].append(policy.model_dump(mode="json"))
        if policy.delivery_deferred:
            message.state=MessageState.PENDING.value; message.attempts=max(0,message.attempts-1); message.last_attempt_at=None; message.updated_at=datetime.now(timezone.utc); db.commit(); result["deferred"]+=1; continue
        latest_attempt=db.scalar(select(func.max(NetworkDeliveryAttempt.attempt_number)).where(NetworkDeliveryAttempt.message_id==message_id)) or 0
        attempt_number=latest_attempt+1
        try:
            _decoded_payload(message)  # The simulated receiver validates/decompresses before acknowledging.
            failure=random.Random(f"delivery:{message_id}:{message.attempts}").random()<telemetry.delivery_failure_rate_pct/100
            if failure: raise TimeoutError("simulated transient delivery failure")
            state=MessageState.DELIVERED.value; outcome="delivered"; error=None; delivered_at=datetime.now(timezone.utc)
            message.state=state; message.delivered_at=delivered_at; message.next_attempt_at=None; message.last_error_category=None
            latency=max(0,telemetry.latency_ms+(telemetry.jitter_ms or 0)*random.Random(f"latency:{message_id}:{message.attempts}").random())
            result["delivered"]+=1
        except TimeoutError:
            error="simulated_transient_failure"
            if message.attempts>=settings.network_retry_limit:
                message.state=MessageState.FAILED.value; message.next_attempt_at=None; outcome="failed"; result["failed"]+=1
            else:
                message.state=MessageState.RETRY_WAIT.value; message.next_attempt_at=datetime.now(timezone.utc)+timedelta(seconds=_retry_delay(message_id,message.attempts)); outcome="retry_scheduled"; result["retry_wait"]+=1
            message.last_error_category=error; latency=None
        except (ValueError, OSError, json.JSONDecodeError):
            error="invalid_stored_payload"; message.state=MessageState.FAILED.value; message.next_attempt_at=None; message.last_error_category=error; outcome="failed"; latency=None; result["failed"]+=1
        message.updated_at=datetime.now(timezone.utc)
        db.add(NetworkDeliveryAttempt(message_id=message_id,attempt_number=attempt_number,attempted_at=message.last_attempt_at or now,outcome=outcome,error_category=error,latency_ms=latency))
        if outcome!="delivered":
            add_event(db,_message_event(message,"network_message_failed" if outcome=="failed" else "network_delivery_retry_scheduled",telemetry.health_score),actor_user_id)
        if actor_user_id:
            db.add(AuditLog(actor_user_id=actor_user_id,action="network.message.delivery",resource_type="network_message",resource_id=message_id,outcome=outcome,details={"attempt":attempt_number,"error_category":error,"policy":policy.recommended_handling.value,"policy_reason":policy.reason}))
        db.commit()
    if result["delivered"]:
        remaining=db.scalar(select(func.count()).select_from(NetworkMessageRecord).where(NetworkMessageRecord.state.in_(ACTIVE_STATES))) or 0
        event=KavachEvent(event_id=uuid.uuid4(),timestamp=datetime.now(timezone.utc),source_module=SourceModule.NETWORK,event_type="network_queue_partially_drained" if remaining else "network_queue_drained",severity=Severity.INFO,security_risk=0,situation_risk=0,network_health=telemetry.health_score,priority=Priority.P2,recommended_action=Action.STORE,evidence={"simulated":True,"delivered_count":result["delivered"],"remaining_eligible_or_retrying":remaining},confidence=1,simulation=True)
        add_event(db,event,actor_user_id);db.commit()
    return result

def request_retry(db:Session,message:NetworkMessageRecord,actor_user_id:str)->None:
    if message.state!=MessageState.FAILED.value:
        raise ValueError("Only failed messages can be manually retried")
    message.state=MessageState.PENDING.value
    message.attempts=0
    message.next_attempt_at=datetime.now(timezone.utc)
    message.updated_at=datetime.now(timezone.utc)
    message.last_error_category=None
    db.add(AuditLog(actor_user_id=actor_user_id,action="network.message.retry",resource_type="network_message",resource_id=message.message_id,outcome="success",details={"manual_retry":True}))
    db.commit(); db.refresh(message)

def queue_counts(db:Session)->dict:
    expire_messages(db)
    states={state.value:0 for state in MessageState}
    for state,count in db.execute(select(NetworkMessageRecord.state,func.count()).group_by(NetworkMessageRecord.state)):
        states[state]=count
    priorities={priority.value:0 for priority in Priority}
    for priority,count in db.execute(select(NetworkMessageRecord.priority,func.count()).where(NetworkMessageRecord.state.in_(ACTIVE_STATES)).group_by(NetworkMessageRecord.priority)):
        priorities[priority]=count
    return {"total":sum(states.values()),"by_state":states,"active_by_priority":priorities}

def communication_metrics(db:Session)->dict:
    expire_messages(db)
    totals=db.execute(select(NetworkMessageRecord.state,func.count()).group_by(NetworkMessageRecord.state)).all()
    states={state:count for state,count in totals}
    terminal=(states.get("DELIVERED",0)+states.get("FAILED",0))
    delivered=states.get("DELIVERED",0)
    success_rate=round(100*delivered/terminal,2) if terminal else None
    attempts=db.execute(select(NetworkDeliveryAttempt.outcome,func.count()).group_by(NetworkDeliveryAttempt.outcome)).all()
    attempt_counts={outcome:count for outcome,count in attempts}
    average_latency=db.scalar(select(func.avg(NetworkDeliveryAttempt.latency_ms)).where(NetworkDeliveryAttempt.latency_ms.is_not(None)))
    sizes=db.execute(select(func.coalesce(func.sum(NetworkMessageRecord.original_size),0),func.coalesce(func.sum(NetworkMessageRecord.stored_size),0))).one()
    original,stored=sizes
    return {"delivery_success_rate_pct":success_rate,"success_rate_denominator":"delivered / (delivered + permanently failed messages); pending and retrying messages excluded","delivery_attempts":sum(attempt_counts.values()),"attempt_outcomes":attempt_counts,"average_simulated_delivery_latency_ms":round(average_latency,2) if average_latency is not None else None,"total_original_bytes":original,"total_stored_bytes":stored,"compression_ratio":round(stored/original,4) if original else None,"size_reduction_percent":round(100*(1-stored/original),2) if original else None}
