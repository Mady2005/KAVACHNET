"""REST API for the synthetic Network Resilience simulator and queue."""
from datetime import datetime, timezone
import csv
import io
import json as json_module
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.auth.dependencies import current_user, roles
from app.db.session import get_db
from app.models import AuditLog, DatasetRegistry, NetworkDeliveryAttempt, NetworkMeasurementRun, NetworkMessageRecord, NetworkObservationRecord, NetworkTelemetryRecord, PublicMeasurementIngestionRun, Role
from app.schemas.events import Priority
from app.schemas.network import DatasetImportOut, DeliveryHistoryPage, MeasurementRunOut, MeasurementRunRequest, MessageCreate, MessageCreatedOut, MessageDetailOut, MessageOut, MessagePage, MessageState, NetworkMetricsOut, NetworkScenario, NetworkState, NetworkStatusOut, NetworkTelemetryOut, ObservationOut, ObservationPage, QueueCounts, ScenarioRequest, SimulationResult, TelemetryInput, TelemetryPage
from app.modules.network_resilience.queue_service import IdempotencyConflict, PayloadTooLarge, communication_metrics, enqueue_message, expire_messages, process_eligible, queue_counts, request_retry
from app.modules.network_resilience.simulator import apply_scenario, apply_telemetry, latest_telemetry
from app.modules.network_resilience.communication_policy import decide_delivery
from app.modules.network_resilience.measurements import measurement_configuration, start_run, stop_run
from app.modules.network_resilience.dataset_import import MAX_IMPORT_BYTES, import_dataset
from app.modules.network_resilience import ripe_atlas
import json

router=APIRouter(prefix="/network",tags=["network resilience"])
network_operator=roles(Role.ADMIN,Role.NETWORK_OPERATOR)
MAX_IMPORT_BODY_BYTES=MAX_IMPORT_BYTES

def _run_output(record:NetworkMeasurementRun):
    return {"run_id":record.run_id,"status":record.status,"target_label":record.target_label,"requested_samples":record.requested_samples,"interval_seconds":record.interval_seconds,"started_at":record.started_at,"ended_at":record.ended_at,"successful_probes":record.successful_probes,"failed_probes":record.failed_probes,"error_categories":record.error_categories}

def _utc_datetime(value):
    if value is None:return None
    if value.tzinfo is None or value.utcoffset() is None:return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

@router.get("/observations",response_model=ObservationPage)
def observations(source_type:str|None=None,measurement_type:str|None=None,experiment_id:str|None=None,dataset_name:str|None=None,measurement_id:str|None=None,provider:str|None=None,start:datetime|None=None,end:datetime|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),user=Depends(current_user),db:Session=Depends(get_db)):
    if start and end and start>end: raise HTTPException(422,"start must be at or before end")
    query=select(NetworkObservationRecord)
    if source_type:
        if source_type not in {"REAL_MEASUREMENT","PUBLIC_DATASET","SIMULATED","RIPE_ATLAS_PUBLIC"}: raise HTTPException(422,"Unsupported source_type")
        query=query.where(NetworkObservationRecord.source_type==source_type)
    if measurement_type: query=query.where(NetworkObservationRecord.measurement_type==measurement_type)
    if experiment_id: query=query.where(NetworkObservationRecord.experiment_id==experiment_id)
    if start: query=query.where(NetworkObservationRecord.timestamp_utc>=start)
    if end: query=query.where(NetworkObservationRecord.timestamp_utc<=end)
    if dataset_name: query=query.where(NetworkObservationRecord.source_name==dataset_name)
    if provider: query=query.where(NetworkObservationRecord.source_name==provider)
    if measurement_id: query=query.where(NetworkObservationRecord.observation_metadata["measurement_id"].as_string()==measurement_id)
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows=db.scalars(query.order_by(NetworkObservationRecord.timestamp_utc.desc(),NetworkObservationRecord.id).offset(offset).limit(limit)).all()
    return {"items":[{"id":r.id,"timestamp_utc":r.timestamp_utc,"source_type":r.source_type,"source_name":r.source_name,"measurement_type":r.measurement_type,"value":r.value,"unit":r.unit,"target_label":r.target_label,"measurement_method":r.measurement_method,"collection_interval_seconds":r.collection_interval_seconds,"dataset_version":r.dataset_version,"experiment_id":r.experiment_id,"dataset_id":r.dataset_id,"metadata":r.observation_metadata} for r in rows],"offset":offset,"limit":limit,"total":total}

@router.get("/observations/export.csv")
def export_observations(source_type:str|None=None,measurement_type:str|None=None,measurement_id:str|None=None,provider:str|None=None,start:datetime|None=None,end:datetime|None=None,user=Depends(current_user),db:Session=Depends(get_db)):
    if start and end and start>end: raise HTTPException(422,"start must be at or before end")
    query=select(NetworkObservationRecord)
    if source_type: query=query.where(NetworkObservationRecord.source_type==source_type)
    if measurement_type: query=query.where(NetworkObservationRecord.measurement_type==measurement_type)
    if measurement_id: query=query.where(NetworkObservationRecord.observation_metadata["measurement_id"].as_string()==measurement_id)
    if provider: query=query.where(NetworkObservationRecord.source_name==provider)
    if start: query=query.where(NetworkObservationRecord.timestamp_utc>=start)
    if end: query=query.where(NetworkObservationRecord.timestamp_utc<=end)
    rows=db.scalars(query.order_by(NetworkObservationRecord.timestamp_utc).limit(10000)).all()
    output=io.StringIO(newline="")
    writer=csv.writer(output);writer.writerow(["timestamp_utc","source_type","source_name","measurement_type","value","unit","target_label","experiment_id","dataset_id","metadata"])
    for row in rows: writer.writerow([row.timestamp_utc.isoformat(),row.source_type,row.source_name,row.measurement_type,row.value,row.unit,row.target_label,row.experiment_id,row.dataset_id,json_module.dumps(row.observation_metadata,sort_keys=True)])
    return Response(content=output.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=kavachnet-observations.csv"})

@router.get("/measurements/config")
def measurement_config(user=Depends(current_user)):
    return measurement_configuration()

@router.post("/measurements/runs",response_model=MeasurementRunOut,status_code=202)
async def create_measurement_run(body:MeasurementRunRequest,user=Depends(network_operator),db:Session=Depends(get_db)):
    try: run=start_run(user.id,body.samples)
    except RuntimeError as exc: raise HTTPException(409,str(exc))
    except ValueError as exc: raise HTTPException(409,str(exc))
    db.add(AuditLog(actor_user_id=user.id,action="network.measurement.start",resource_type="network_measurement_run",resource_id=run.run_id,outcome="success",details={"target_label":run.target_label,"requested_samples":body.samples}));db.commit()
    return _run_output(run)

@router.get("/measurements/runs/{run_id}",response_model=MeasurementRunOut)
def measurement_run(run_id:str,user=Depends(current_user),db:Session=Depends(get_db)):
    run=db.get(NetworkMeasurementRun,run_id)
    if not run: raise HTTPException(404,"Measurement run not found")
    return _run_output(run)

@router.post("/measurements/runs/{run_id}/stop",response_model=MeasurementRunOut)
async def stop_measurement_run(run_id:str,user=Depends(network_operator),db:Session=Depends(get_db)):
    run=db.get(NetworkMeasurementRun,run_id)
    if not run: raise HTTPException(404,"Measurement run not found")
    if run.status not in {"RUNNING","PENDING","STOPPING"}: raise HTTPException(409,"Measurement run is already complete")
    if not stop_run(run_id): raise HTTPException(409,"Measurement run is not active on this server process")
    run.status="STOPPING"
    db.add(AuditLog(actor_user_id=user.id,action="network.measurement.stop",resource_type="network_measurement_run",resource_id=run_id,outcome="success",details={}));db.commit();db.refresh(run)
    return _run_output(run)

@router.post("/datasets/import",response_model=DatasetImportOut,status_code=201)
async def dataset_import(file:UploadFile=File(...),name:str=Form(...,min_length=1,max_length=200),source_url:str|None=Form(default=None,max_length=500),license_name:str|None=Form(default=None,max_length=200),dataset_version:str|None=Form(default=None,max_length=100),mapping:str|None=Form(default=None),user=Depends(network_operator),db:Session=Depends(get_db)):
    content=await file.read(MAX_IMPORT_BODY_BYTES+1)
    if len(content)>MAX_IMPORT_BODY_BYTES: raise HTTPException(413,f"Import exceeds the {MAX_IMPORT_BODY_BYTES} byte limit")
    try:
        if mapping and len(mapping)>4096: raise ValueError("mapping JSON exceeds the 4096 character limit")
        parsed_mapping=json.loads(mapping) if mapping else None
        if parsed_mapping is not None and not isinstance(parsed_mapping,dict): raise ValueError("mapping must be a JSON object")
        result=import_dataset(db,content,file.filename or "upload",name,source_url,license_name,dataset_version,parsed_mapping)
    except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc))
    db.add(AuditLog(actor_user_id=user.id,action="network.dataset.import",resource_type="dataset",resource_id=result["dataset_id"],outcome="success",details={"name":name,"accepted_rows":result["accepted_rows"],"rejected_rows":result["rejected_rows"],"sha256":result["sha256"]}));db.commit()
    return result

@router.get("/datasets/{dataset_id}")
def dataset_import_summary(dataset_id:str,user=Depends(current_user),db:Session=Depends(get_db)):
    record=db.get(DatasetRegistry,dataset_id)
    if not record: raise HTTPException(404,"Dataset import not found")
    return {"dataset_id":record.dataset_id,"name":record.name,"source_url":record.source_url,"license":record.license,"import_status":record.import_status,"checksum":record.checksum,"provenance":record.provenance,"schema_metadata":record.schema_metadata}

@router.post("/ripe-atlas/ingestions")
async def create_ripe_ingestion(body:dict|None=None,user=Depends(network_operator),db:Session=Depends(get_db)):
    body=body or {}
    if set(body)-{"start","end"}: raise HTTPException(422,"Only optional start and end timestamps are accepted")
    try:
        start=datetime.fromisoformat(body["start"].replace("Z","+00:00")) if body.get("start") else None
        end=datetime.fromisoformat(body["end"].replace("Z","+00:00")) if body.get("end") else None
        result=await ripe_atlas.ingest(db,user.id,start,end)
    except (ValueError,TypeError) as exc: raise HTTPException(409,str(exc))
    db.add(AuditLog(actor_user_id=user.id,action="network.ripe_atlas.ingest",resource_type="public_measurement_ingestion",resource_id=result["run_id"],outcome=result["status"],details={"measurement_id":result["measurement_id"],"accepted_count":result["accepted_count"],"rejected_count":result["rejected_count"],"deduplicated_count":result["deduplicated_count"],"observations_inserted_count":result["observations_inserted_count"],"observations_deduplicated_count":result["observations_deduplicated_count"]}));db.commit()
    return result

@router.get("/ripe-atlas/config")
def ripe_configuration(user=Depends(current_user)):
    return ripe_atlas.configuration()

@router.get("/ripe-atlas/ingestions/{run_id}")
def ripe_ingestion_status(run_id:str,user=Depends(current_user),db:Session=Depends(get_db)):
    result=ripe_atlas.get_run(db,run_id)
    if not result: raise HTTPException(404,"RIPE Atlas ingestion run not found")
    return result

@router.get("/evaluation")
def telemetry_evaluation(source_type:str=Query(...,pattern="^(REAL_MEASUREMENT|PUBLIC_DATASET|SIMULATED|RIPE_ATLAS_PUBLIC)$"),experiment_id:str|None=None,dataset_id:str|None=None,start:datetime|None=None,end:datetime|None=None,limit:int=Query(10000,ge=1,le=10000),user=Depends(current_user),db:Session=Depends(get_db)):
    if start and end and start>end: raise HTTPException(422,"start must be at or before end")
    query=select(NetworkObservationRecord).where(NetworkObservationRecord.source_type==source_type)
    if experiment_id: query=query.where(NetworkObservationRecord.experiment_id==experiment_id)
    if dataset_id: query=query.where(NetworkObservationRecord.dataset_id==dataset_id)
    if start: query=query.where(NetworkObservationRecord.timestamp_utc>=start)
    if end: query=query.where(NetworkObservationRecord.timestamp_utc<=end)
    observation_count=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if observation_count>limit: raise HTTPException(413,f"Evaluation includes {observation_count} observations; add a time, run, or dataset filter (limit {limit})")
    rows=db.scalars(query.order_by(NetworkObservationRecord.timestamp_utc)).all()
    groups={}
    for row in rows:
        bucket=groups.setdefault(row.measurement_type,{"unit":row.unit,"sample_count":0,"available_count":0,"values":[],"first_timestamp":row.timestamp_utc,"last_timestamp":row.timestamp_utc})
        bucket["sample_count"]+=1;bucket["last_timestamp"]=row.timestamp_utc
        if row.value is not None: bucket["available_count"]+=1;bucket["values"].append(row.value)
    metrics={}
    for key,bucket in groups.items():
        values=sorted(bucket.pop("values"));bucket["min"]=values[0] if values else None;bucket["max"]=values[-1] if values else None
        bucket["mean"]=round(sum(values)/len(values),4) if values else None
        import statistics
        bucket["median"]=round(statistics.median(values),4) if values else None;bucket["p95"]=_percentile(values,0.95) if len(values)>=20 else None;bucket["availability"]="AVAILABLE" if values else "UNAVAILABLE"
        metrics[key]=bucket
    run_ids=sorted({row.experiment_id for row in rows if row.experiment_id})
    runs=[]
    if source_type=="REAL_MEASUREMENT" and run_ids:
        for run in db.scalars(select(NetworkMeasurementRun).where(NetworkMeasurementRun.run_id.in_(run_ids)).order_by(NetworkMeasurementRun.started_at)).all():
            runs.append({"run_id":run.run_id,"status":run.status,"target_label":run.target_label,"requested_samples":run.requested_samples,"attempted_probes":run.successful_probes+run.failed_probes,"successful_probes":run.successful_probes,"failed_probes":run.failed_probes,"started_at":_utc_datetime(run.started_at),"ended_at":_utc_datetime(run.ended_at),"error_categories":run.error_categories})
    datasets=[]
    dataset_ids={row.dataset_id for row in rows if row.dataset_id}
    if source_type=="PUBLIC_DATASET" and dataset_ids:
        for item in db.scalars(select(DatasetRegistry).where(DatasetRegistry.dataset_id.in_(dataset_ids))).all():
            datasets.append({"dataset_id":item.dataset_id,"name":item.name,"version":item.schema_metadata.get("dataset_version"),"source_url":item.source_url,"license":item.license,"checksum":item.checksum,"import_status":item.import_status,"unsupported_operational_metrics":item.provenance.get("unsupported_operational_metrics",UNAVAILABLE_OPERATIONAL),"provenance":item.provenance})
    public_runs=[]
    if source_type=="RIPE_ATLAS_PUBLIC":
        run_ids={row.experiment_id for row in rows if row.experiment_id}
        if run_ids:
            for record in db.scalars(select(PublicMeasurementIngestionRun).where(PublicMeasurementIngestionRun.run_id.in_(run_ids)).order_by(PublicMeasurementIngestionRun.started_at)).all():
                public_runs.append({"run_id":record.run_id,"provider":record.provider,"measurement_id":record.measurement_id,"status":record.status,"started_at":_utc_datetime(record.started_at),"ended_at":_utc_datetime(record.ended_at),"window_start":_utc_datetime(record.window_start),"window_end":_utc_datetime(record.window_end),"retrieved_count":record.retrieved_count,"accepted_count":record.accepted_count,"rejected_count":record.rejected_count,"deduplicated_count":record.deduplicated_count,"observations_inserted_count":record.observations_inserted_count,"observations_deduplicated_count":record.observations_deduplicated_count,"errors":record.errors})
    empty_status="PENDING_DATA_COLLECTION" if source_type in {"REAL_MEASUREMENT","RIPE_ATLAS_PUBLIC"} else "PENDING_IMPORT" if source_type=="PUBLIC_DATASET" else "NO_SIMULATION_DATA"
    return {"source_type":source_type,"experiment_id":experiment_id,"dataset_id":dataset_id,"observation_count":observation_count,"collection_window":{"started_at":_utc_datetime(rows[0].timestamp_utc) if rows else None,"ended_at":_utc_datetime(rows[-1].timestamp_utc) if rows else None},"runs":runs,"public_runs":public_runs,"datasets":datasets,"metrics":metrics,"status":empty_status if not rows else "MEASURED"}

@router.get("/evaluation/export.json")
def export_evaluation(source_type:str=Query(...,pattern="^(REAL_MEASUREMENT|PUBLIC_DATASET|SIMULATED|RIPE_ATLAS_PUBLIC)$"),experiment_id:str|None=None,dataset_id:str|None=None,start:datetime|None=None,end:datetime|None=None,user=Depends(current_user),db:Session=Depends(get_db)):
    result=telemetry_evaluation(source_type,experiment_id,dataset_id,start,end,10000,user,db)
    payload=json_module.dumps(result,default=lambda value:value.isoformat() if hasattr(value,"isoformat") else str(value),indent=2)
    return Response(content=payload,media_type="application/json",headers={"Content-Disposition":"attachment; filename=kavachnet-network-evaluation.json"})

def _percentile(values,quantile):
    if not values:return None
    import math
    index=max(0,min(len(values)-1,math.ceil(len(values)*quantile)-1))
    return values[index]

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
