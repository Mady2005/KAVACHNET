"""REST API for the synthetic Network Resilience simulator and queue."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth.dependencies import current_user, roles
from app.db.session import get_db
from app.models import NetworkDeliveryAttempt, NetworkMessageRecord, NetworkTelemetryRecord, Role
from app.schemas.events import Priority
from app.schemas.network import DeliveryHistoryPage, MessageCreate, MessageCreatedOut, MessageDetailOut, MessageOut, MessagePage, MessageState, NetworkMetricsOut, NetworkScenario, NetworkState, NetworkStatusOut, NetworkTelemetryOut, QueueCounts, ScenarioRequest, SimulationResult, TelemetryInput, TelemetryPage
from app.modules.network_resilience.queue_service import IdempotencyConflict, PayloadTooLarge, communication_metrics, enqueue_message, expire_messages, process_eligible, queue_counts, request_retry
from app.modules.network_resilience.simulator import apply_scenario, apply_telemetry, latest_telemetry
from app.modules.network_resilience.communication_policy import decide_delivery

router=APIRouter(prefix="/network",tags=["network resilience"])
network_operator=roles(Role.ADMIN,Role.NETWORK_OPERATOR)

def _telemetry(record:NetworkTelemetryRecord|None):
    return NetworkTelemetryOut.model_validate(record) if record else None

def _message(record:NetworkMessageRecord)->dict:
    return {"message_id":record.message_id,"priority":record.priority,"state":record.state,"created_at":record.created_at,"updated_at":record.updated_at,"attempts":record.attempts,"next_attempt_at":record.next_attempt_at,"last_attempt_at":record.last_attempt_at,"delivered_at":record.delivered_at,"expires_at":record.expires_at,"last_error_category":record.last_error_category,"original_size":record.original_size,"stored_size":record.stored_size,"compression_algorithm":record.compression_algorithm,"compression_ratio":round(record.stored_size/record.original_size,4) if record.original_size else None}

def _resume_after_recovery(db:Session,previous:NetworkTelemetryRecord|None,current:NetworkTelemetryRecord,actor_id:str):
    recovering=(previous is not None and previous.state!="healthy" and current.state=="healthy") or current.scenario=="recovery"
    if current.connected and recovering:
        db.query(NetworkMessageRecord).filter(NetworkMessageRecord.state==MessageState.RETRY_WAIT.value).update({NetworkMessageRecord.next_attempt_at:datetime.now(timezone.utc),NetworkMessageRecord.updated_at:datetime.now(timezone.utc)},synchronize_session=False)
        db.commit()
    return process_eligible(db,actor_id)

@router.get("/status",response_model=NetworkStatusOut)
def network_status(user=Depends(current_user),db:Session=Depends(get_db)):
    record=latest_telemetry(db)
    return {"initialized":record is not None,"telemetry":_telemetry(record),"queue":queue_counts(db)}

@router.get("/history",response_model=TelemetryPage)
def network_history(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),user=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(NetworkTelemetryRecord).order_by(NetworkTelemetryRecord.timestamp.desc(),NetworkTelemetryRecord.id.desc()).offset(offset).limit(limit)).all()
    return {"items":[_telemetry(row).model_dump(mode="json") for row in rows],"offset":offset,"limit":limit}

@router.post("/simulator/scenario",response_model=SimulationResult)
def set_scenario(body:ScenarioRequest,user=Depends(network_operator),db:Session=Depends(get_db)):
    previous=latest_telemetry(db)
    record=apply_scenario(db,body.scenario,user.id,body.reason)
    delivery=_resume_after_recovery(db,previous,record,user.id)
    return {"telemetry":_telemetry(record),"delivery":delivery}

@router.post("/simulator/telemetry",response_model=SimulationResult)
def set_simulated_telemetry(body:TelemetryInput,user=Depends(network_operator),db:Session=Depends(get_db)):
    previous=latest_telemetry(db)
    record=apply_telemetry(db,body,"custom",body.reason,user.id)
    delivery=_resume_after_recovery(db,previous,record,user.id)
    return {"telemetry":_telemetry(record),"delivery":delivery}

@router.post("/simulator/reset",response_model=SimulationResult)
def reset_simulator(user=Depends(network_operator),db:Session=Depends(get_db)):
    previous=latest_telemetry(db)
    record=apply_scenario(db,NetworkScenario.HEALTHY,user.id,"Simulator reset to healthy synthetic defaults")
    delivery=_resume_after_recovery(db,previous,record,user.id)
    return {"telemetry":_telemetry(record),"delivery":delivery}

@router.get("/queue",response_model=QueueCounts)
def queue_overview(user=Depends(current_user),db:Session=Depends(get_db)):
    return queue_counts(db)

@router.post("/messages",status_code=201,response_model=MessageCreatedOut)
def create_message(body:MessageCreate,response:Response,user=Depends(network_operator),db:Session=Depends(get_db)):
    try:
        record,created=enqueue_message(db,body.payload,body.priority,user.id,body.idempotency_key)
    except PayloadTooLarge as error:
        raise HTTPException(413,str(error))
    except (ValueError,TypeError) as error:
        raise HTTPException(422,f"Invalid JSON payload: {error}")
    except IdempotencyConflict as error:
        raise HTTPException(409,str(error))
    telemetry=latest_telemetry(db)
    network_state=NetworkState(telemetry.state) if telemetry else NetworkState.DISCONNECTED
    active=sum(queue_counts(db)["active_by_priority"].values())
    decision=decide_delivery(network_state,body.priority.value,record.payload_encoding=="gzip",active)
    if created:
        process_eligible(db,user.id)
        db.refresh(record)
    else:
        response.status_code=200
    return {"message":_message(record),"created":created,"handling_decision":decision.model_dump(mode="json")}

@router.get("/messages",response_model=MessagePage)
def list_messages(state:MessageState|None=None,priority:Priority|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),user=Depends(current_user),db:Session=Depends(get_db)):
    from sqlalchemy import case, func
    expire_messages(db)
    query=select(NetworkMessageRecord)
    if state: query=query.where(NetworkMessageRecord.state==state.value)
    if priority: query=query.where(NetworkMessageRecord.priority==priority.value)
    rank=case((NetworkMessageRecord.priority=="P0",0),(NetworkMessageRecord.priority=="P1",1),(NetworkMessageRecord.priority=="P2",2),else_=3)
    rows=db.scalars(query.order_by(rank,NetworkMessageRecord.created_at,NetworkMessageRecord.message_id).offset(offset).limit(limit)).all()
    count=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    return {"items":[_message(row) for row in rows],"offset":offset,"limit":limit,"total":count}

@router.get("/messages/{message_id}",response_model=MessageDetailOut)
def message_detail(message_id:str,user=Depends(current_user),db:Session=Depends(get_db)):
    expire_messages(db)
    record=db.get(NetworkMessageRecord,message_id)
    if not record: raise HTTPException(404,"Message not found")
    attempts=db.scalars(select(NetworkDeliveryAttempt).where(NetworkDeliveryAttempt.message_id==message_id).order_by(NetworkDeliveryAttempt.attempt_number)).all()
    return {"message":_message(record),"attempt_history":[{"attempt_number":a.attempt_number,"attempted_at":a.attempted_at,"outcome":a.outcome,"error_category":a.error_category,"simulated_latency_ms":a.latency_ms} for a in attempts]}

@router.post("/messages/{message_id}/retry",response_model=MessageOut)
def retry_message(message_id:str,user=Depends(network_operator),db:Session=Depends(get_db)):
    record=db.get(NetworkMessageRecord,message_id)
    if not record: raise HTTPException(404,"Message not found")
    try: request_retry(db,record,user.id)
    except ValueError as error: raise HTTPException(409,str(error))
    process_eligible(db,user.id)
    db.refresh(record)
    return _message(record)

@router.get("/metrics",response_model=NetworkMetricsOut)
def metrics(user=Depends(current_user),db:Session=Depends(get_db)):
    return {**communication_metrics(db),"queue":queue_counts(db)}

@router.get("/deliveries",response_model=DeliveryHistoryPage)
def delivery_history(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),user=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.scalars(select(NetworkDeliveryAttempt).order_by(NetworkDeliveryAttempt.attempted_at.desc(),NetworkDeliveryAttempt.id.desc()).offset(offset).limit(limit)).all()
    return {"items":[{"message_id":a.message_id,"attempt_number":a.attempt_number,"attempted_at":a.attempted_at,"outcome":a.outcome,"error_category":a.error_category,"simulated_latency_ms":a.latency_ms} for a in rows],"offset":offset,"limit":limit}
