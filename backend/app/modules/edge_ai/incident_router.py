"""Authenticated read and lifecycle endpoints for persisted incidents."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.v1.routes import event_dict, roles
from app.db.session import get_db
from app.models import (IncidentEventLink, IncidentRecord, IncidentStatusHistory,
                        KavachEventRecord, Role, SensorObservation)
from .incident_schemas import IncidentOut, IncidentStatusUpdate

router = APIRouter(prefix="/incidents", tags=["incidents"])
access = Depends(roles(Role.ADMIN, Role.EDGE_OPERATOR))

def _utc(value):
    if value is None: return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def _incident_json(row):
    values=IncidentOut.model_validate(row).model_dump()
    for key in ("first_observed","last_observed","created_at","updated_at","resolved_at"):
        values[key]=_utc(values[key])
    return IncidentOut.model_validate(values).model_dump(mode="json")

@router.get("")
def list_incidents(status: str | None = Query(None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$"),
                   severity: str | None = Query(None, pattern="^(INFO|LOW|MEDIUM|HIGH|CRITICAL)$"),
                   source_id: str | None = Query(None, min_length=1, max_length=128),
                   start: datetime | None = None, end: datetime | None = None,
                   limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                   user=access, db: Session = Depends(get_db)):
    if start and start.tzinfo is None or end and end.tzinfo is None:
        raise HTTPException(422, "start and end must include a timezone")
    if start and end and start > end: raise HTTPException(422, "start must be earlier than or equal to end")
    filters=[]
    if status: filters.append(IncidentRecord.status == status)
    if severity: filters.append(IncidentRecord.severity == severity)
    if source_id: filters.append(IncidentRecord.source_id == source_id)
    if start: filters.append(IncidentRecord.last_observed >= start)
    if end: filters.append(IncidentRecord.first_observed <= end)
    total=db.scalar(select(func.count()).select_from(IncidentRecord).where(*filters)) or 0
    rows=db.scalars(select(IncidentRecord).where(*filters).order_by(IncidentRecord.last_observed.desc(), IncidentRecord.incident_id).offset(offset).limit(limit)).all()
    return {"items":[_incident_json(row) for row in rows],"total":total,"limit":limit,"offset":offset}

@router.get("/{incident_id}")
def incident_detail(incident_id: str, user=access, db: Session=Depends(get_db)):
    incident=db.get(IncidentRecord, incident_id)
    if not incident: raise HTTPException(404, "Incident not found")
    items=[]
    links=db.scalars(select(IncidentEventLink).where(IncidentEventLink.incident_id==incident_id).order_by(IncidentEventLink.linked_at)).all()
    for link in links:
        event=db.get(KavachEventRecord, link.event_id)
        if not event: continue
        observation=db.get(SensorObservation, link.observation_id) if link.observation_id else None
        items.append({"event":event_dict(event),"observation":({"observation_id":observation.observation_id,"sensor_id":observation.sensor_id,"timestamp":_utc(observation.timestamp),"simulation":observation.simulation,"measurements":{k:v for k,v in observation.payload.items() if k in {"temperature_c","humidity_pct","vibration_mm_s","voltage_v","battery_pct"}}} if observation else None)})
    history=db.scalars(select(IncidentStatusHistory).where(IncidentStatusHistory.incident_id==incident_id).order_by(IncidentStatusHistory.changed_at)).all()
    return {"incident":_incident_json(incident),"events":items,"status_history":[{"from_status":x.from_status,"to_status":x.to_status,"note":x.note,"changed_at":_utc(x.changed_at),"actor_user_id":x.actor_user_id} for x in history]}

@router.patch("/{incident_id}/status")
def update_incident_status(incident_id: str, body: IncidentStatusUpdate, user=access, db: Session=Depends(get_db)):
    incident=db.get(IncidentRecord, incident_id)
    if not incident: raise HTTPException(404, "Incident not found")
    old=incident.status
    allowed={"OPEN":{"ACKNOWLEDGED","RESOLVED"},"ACKNOWLEDGED":{"RESOLVED"},"RESOLVED":set()}
    if body.status not in allowed[old]: raise HTTPException(409, f"Invalid incident transition: {old} to {body.status}")
    now=datetime.now(timezone.utc)
    values={"status":body.status,"updated_at":now}
    if body.status == "RESOLVED": values.update(resolved_at=now,resolution_notes=body.resolution_notes)
    result=db.execute(update(IncidentRecord).where(IncidentRecord.incident_id==incident_id,IncidentRecord.status==old).values(**values))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409,"Incident status changed concurrently; reload before retrying")
    db.add(IncidentStatusHistory(incident_id=incident_id,actor_user_id=getattr(user,"id",None),from_status=old,to_status=body.status,note=body.resolution_notes))
    db.commit(); db.refresh(incident)
    return _incident_json(incident)
