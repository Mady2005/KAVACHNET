"""Incident integration tests use an isolated in-memory SQLite database."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.api.v1.routes import current_user
from app.models import (IncidentEventLink, IncidentRecord, IncidentStatusHistory,
                        KavachEventRecord, Role, SensorObservation)
from app.modules.edge_ai.correlation_service import correlate_persisted_event


@pytest.fixture
def isolated():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    def override_db():
        session=factory()
        try: yield session
        finally: session.close()
    actor=type("Actor",(),{"id":None,"role":Role.EDGE_OPERATOR})()
    previous_overrides=app.dependency_overrides.copy()
    app.dependency_overrides[get_db]=override_db
    app.dependency_overrides[current_user]=lambda:actor
    with TestClient(app) as client:
        yield factory,client
    app.dependency_overrides.clear();app.dependency_overrides.update(previous_overrides)
    Base.metadata.drop_all(engine);engine.dispose()


def stored_event(session,event_id=None,*,sensor="pump-1",stamp=None,severity="HIGH",field="temperature_c"):
    event_id=str(event_id or uuid4())
    evidence={"sensor_id":sensor,"triggered_rules":[{"field":field}],"detection_methods":["rules"]}
    row=KavachEventRecord(event_id=event_id,timestamp=stamp or datetime.now(timezone.utc),source_module="EDGE_AI",event_type="sensor_anomaly",severity=severity,security_risk=0.0,situation_risk=.7,network_health=80,priority="P1",recommended_action="ALERT",evidence=evidence,confidence=.9,status="NEW",correlation_id=None,simulation=False)
    session.add(row);session.commit()
    return event_id


def test_correlation_idempotency_severity_late_and_window(isolated):
    factory,_=isolated
    base=datetime(2026,1,1,tzinfo=timezone.utc)
    with factory() as db:
        a=stored_event(db,sensor="pump-1",stamp=base,severity="MEDIUM")
        one=correlate_persisted_event(db,a); assert one.status=="OPEN" and one.event_count==1
        assert correlate_persisted_event(db,a).incident_id==one.incident_id
        b=stored_event(db,sensor="pump-1",stamp=base+timedelta(minutes=3),severity="CRITICAL")
        assert correlate_persisted_event(db,b).incident_id==one.incident_id
        late=stored_event(db,sensor="pump-1",stamp=base+timedelta(minutes=1),severity="HIGH")
        incident=correlate_persisted_event(db,late)
        assert incident.first_observed==base and incident.last_observed==base+timedelta(minutes=3)
        assert incident.severity=="CRITICAL" and incident.event_count==3
        other_sensor=stored_event(db,sensor="pump-2",stamp=base+timedelta(minutes=1))
        other_category=stored_event(db,sensor="pump-1",stamp=base+timedelta(minutes=1),field="humidity_pct")
        expired=stored_event(db,sensor="pump-1",stamp=base+timedelta(minutes=20))
        ids=[correlate_persisted_event(db,x).incident_id for x in (other_sensor,other_category,expired)]
        assert len(set(ids))==3 and one.incident_id not in ids
        assert db.scalar(select(func.count()).select_from(IncidentEventLink))==6
    with factory() as fresh:
        assert fresh.scalar(select(func.count()).select_from(IncidentRecord))==4


def test_shared_event_api_and_incident_lifecycle_authorization_filters(isolated):
    factory,client=isolated
    event_id=str(uuid4()); stamp=datetime.now(timezone.utc).isoformat()
    body={"event_id":event_id,"timestamp":stamp,"source_module":"EDGE_AI","event_type":"sensor_anomaly","severity":"HIGH","security_risk":0,"situation_risk":.7,"network_health":80,"priority":"P1","recommended_action":"ALERT","evidence":{"sensor_id":"pump-api","triggered_rules":[{"field":"temperature_c"}],"detection_methods":["rules"]},"confidence":.9,"status":"NEW","correlation_id":None,"simulation":False}
    created=client.post("/api/v1/events",json=body);assert created.status_code==201,created.text
    replay=client.post("/api/v1/events",json=body);assert replay.status_code==200
    listed=client.get("/api/v1/incidents",params={"status":"OPEN","severity":"HIGH","source_id":"pump-api","limit":1})
    assert listed.status_code==200 and listed.json()["total"]==1
    incident_id=listed.json()["items"][0]["incident_id"]
    detail=client.get(f"/api/v1/incidents/{incident_id}");assert len(detail.json()["events"])==1
    ack=client.patch(f"/api/v1/incidents/{incident_id}/status",json={"status":"ACKNOWLEDGED"});assert ack.status_code==200
    resolve=client.patch(f"/api/v1/incidents/{incident_id}/status",json={"status":"RESOLVED","resolution_notes":"Reviewed"});assert resolve.status_code==200
    conflict=client.patch(f"/api/v1/incidents/{incident_id}/status",json={"status":"ACKNOWLEDGED"});assert conflict.status_code==409
    with factory() as db: assert db.scalar(select(func.count()).select_from(IncidentStatusHistory))==3
    app.dependency_overrides[current_user]=lambda:type("Viewer",(),{"id":None,"role":Role.VIEWER})()
    denied=client.get("/api/v1/incidents");assert denied.status_code==403
    app.dependency_overrides.pop(current_user,None)
    unauthenticated=client.get("/api/v1/incidents");assert unauthenticated.status_code==401


def test_invalid_timestamp_and_ineligible_event_do_not_create_incident(isolated):
    factory,_=isolated
    with factory() as db:
        event_id=stored_event(db,sensor="",stamp=datetime.now(timezone.utc))
        assert correlate_persisted_event(db,event_id) is None
        assert db.scalar(select(func.count()).select_from(IncidentRecord))==0


def test_sensor_analysis_to_observation_event_incident_api(isolated):
    factory,client=isolated
    reading={"sensor_id":"pump-e2e","timestamp":datetime.now(timezone.utc).isoformat(),"temperature_c":96,"reading_id":"phase2-e2e"}
    analyzed=client.post("/api/v1/edge-ai/analyze",json=reading)
    assert analyzed.status_code==200, analyzed.text
    event_id=analyzed.json()["event"]["event_id"]
    assert analyzed.json()["event_persisted"] is True
    with factory() as db:
        observation=db.scalar(select(SensorObservation).where(SensorObservation.event_id==event_id))
        assert observation is not None
        link=db.get(IncidentEventLink,event_id)
        assert link is not None and link.observation_id==observation.observation_id
    detail=client.get(f"/api/v1/incidents/{client.get('/api/v1/incidents').json()['items'][0]['incident_id']}")
    assert detail.status_code==200 and detail.json()["events"][0]["observation"]["sensor_id"]=="pump-e2e"


def test_concurrent_reprocessing_same_canonical_event_is_idempotent(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'concurrency.db'}",connect_args={"check_same_thread":False,"timeout":10})
    Base.metadata.create_all(engine);factory=sessionmaker(bind=engine,expire_on_commit=False)
    with factory() as db: event_id=stored_event(db,sensor="pump-race")
    barrier=Barrier(2)
    def process():
        barrier.wait()
        with factory() as db: return correlate_persisted_event(db,event_id).incident_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        incident_ids=list(pool.map(lambda _:process(),range(2)))
    with factory() as db:
        assert len(set(incident_ids))==1
        assert db.scalar(select(func.count()).select_from(IncidentRecord))==1
        assert db.scalar(select(func.count()).select_from(IncidentEventLink))==1
    Base.metadata.drop_all(engine);engine.dispose()
