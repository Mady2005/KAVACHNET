"""Authenticated HTTP adapter for the Edge AI service."""
import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.v1.routes import create_event, roles
from app.db.session import get_db
from app.models import SensorObservation
from app.models import Role
from app.schemas.events import KavachEvent

from .schemas import AI4IPredictionRequest, AI4IPredictionResponse, BatchReadings, SensorReading, SimulationRequest
from .service import EdgeAIService
from .simulator import generate_readings
from .model_manager import AI4IModelManager

router = APIRouter(prefix="/edge-ai", tags=["edge-ai"])
service = EdgeAIService()
ai4i_model = AI4IModelManager()
ai4i_model.load()
access = Depends(roles(Role.ADMIN, Role.EDGE_OPERATOR))


@router.get("/health")
def health(user=access):
    info = service.model_info()
    return {"status": "ready" if info["available"] else "degraded", "rules_available": True, "model": info}


@router.post("/analyze")
def analyze(body: SensorReading, response: Response, user=access, db: Session = Depends(get_db)):
    try:
        event, summary, replayed = _analyze_and_store(body.model_dump(mode="python", exclude_none=True), db, user, response)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"event": event, "analysis": summary, "persisted": True, "event_persisted": True, "replayed": replayed}


@router.post("/analyze/batch")
def analyze_batch(body: BatchReadings, response: Response, user=access, db: Session = Depends(get_db)):
    results = []
    for index, reading in enumerate(body.readings):
        try:
            event, summary, replayed = _analyze_and_store(reading, db, user, response)
            results.append({"index": index, "event": event, "analysis": summary, "error": None, "replayed": replayed, "event_persisted": True})
        except ValueError as exc:
            results.append({"index": index, "event": None, "analysis": None, "error": str(exc)})
    return {"items": results, "persisted": False}


@router.post("/simulate")
def simulate(body: SimulationRequest, response: Response, user=access, db: Session = Depends(get_db)):
    items = []
    for reading in generate_readings(body.count, body.seed):
        event, analysis, _ = _analyze_and_store(reading, db, user, response)
        items.append({"event": event, "analysis": analysis})
    return {"items": items, "seed": body.seed, "simulation": True, "persisted": True, "event_persisted": True}


def _analyze_and_store(raw: dict, db: Session, user, response):
    normalized = dict(raw)
    stamp = normalized.get("timestamp")
    if isinstance(stamp, datetime):
        normalized["timestamp"] = stamp.astimezone(timezone.utc).isoformat()
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    observation_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    existing = db.get(SensorObservation, observation_id)
    if existing:
        if existing.event_id:
            from .correlation_service import correlate_persisted_event
            correlate_persisted_event(db, existing.event_id, actor_user_id=getattr(user, "id", None))
        return KavachEvent.model_validate(existing.result["event"]), existing.result["analysis"], True
    event, summary = service.analyze_detailed(raw)
    create_event(event, response, user, db)
    payload = json.loads(encoded)
    result = {"event": event.model_dump(mode="json"), "analysis": summary}
    summary_json = json.loads(json.dumps(summary, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))
    result["analysis"] = summary_json
    row = SensorObservation(
        observation_id=observation_id, sensor_id=str(payload["sensor_id"]),
        timestamp=datetime.fromisoformat(payload["timestamp"]), simulation=bool(payload.get("simulation", False)),
        payload=payload, result=result, event_id=str(event.event_id),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(SensorObservation, observation_id)
        if not existing:
            raise
        return KavachEvent.model_validate(existing.result["event"]), existing.result["analysis"], True
    from .correlation_service import correlate_persisted_event
    correlate_persisted_event(db, str(event.event_id), actor_user_id=getattr(user, "id", None))
    return event, summary, False


@router.get("/history")
def history(sensor_id: str | None = Query(default=None, min_length=1, max_length=128),
            start: datetime | None = None, end: datetime | None = None,
            limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0), user=access,
            db: Session = Depends(get_db)):
    if start and end and start > end:
        raise HTTPException(422, "start must be earlier than or equal to end")
    filters = []
    if sensor_id:
        filters.append(SensorObservation.sensor_id == sensor_id)
    if start:
        filters.append(SensorObservation.timestamp >= start)
    if end:
        filters.append(SensorObservation.timestamp <= end)
    total = db.scalar(select(func.count()).select_from(SensorObservation).where(*filters)) or 0
    rows = db.scalars(select(SensorObservation).where(*filters).order_by(SensorObservation.timestamp.desc(), SensorObservation.observation_id).offset(offset).limit(limit)).all()
    return {"items": [{"observation_id": row.observation_id, "sensor_id": row.sensor_id,
                        "timestamp": row.timestamp, "simulation": row.simulation,
                        "reading": row.payload, "event": row.result["event"], "analysis": row.result["analysis"]} for row in rows],
            "total": total, "limit": limit, "offset": offset, "order": "timestamp_desc"}


@router.get("/model-info")
def model_info(user=access):
    return service.model_info() | {"ai4i_predictive_maintenance": ai4i_model.info()}


@router.post("/predict/maintenance", response_model=AI4IPredictionResponse)
def predict_maintenance(body: AI4IPredictionRequest, user=access):
    if not ai4i_model.available:
        raise HTTPException(503, "AI4I predictive maintenance model is unavailable")
    try:
        return ai4i_model.predict(body)
    except Exception as exc:
        raise HTTPException(500, "AI4I model inference failed") from exc


@router.get("/metrics")
def metrics(user=access):
    return service.metrics()
