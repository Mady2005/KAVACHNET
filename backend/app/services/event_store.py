"""Shared persistence adapter for validated KavachEvent values."""
from sqlalchemy.orm import Session
from app.models import AuditLog, KavachEventRecord
from app.schemas.events import KavachEvent

def add_event(db:Session,event:KavachEvent,actor_user_id:str|None=None)->KavachEventRecord:
    values=event.model_dump()
    values["event_id"]=str(event.event_id)
    values={key:(value.value if hasattr(value,"value") else value) for key,value in values.items()}
    record=KavachEventRecord(**values)
    db.add(record)
    db.add(AuditLog(actor_user_id=actor_user_id,action="event.create",resource_type="event",resource_id=str(event.event_id),outcome="success",details={"source_module":event.source_module.value,"event_type":event.event_type}))
    return record
