import asyncio
import gzip
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError
from app.auth.security import issue_token
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import KavachEventRecord, NetworkDeliveryAttempt, NetworkMessageRecord, NetworkTelemetryRecord, Role, User
from app.modules.network_resilience.health_score import score_telemetry
from app.modules.network_resilience.queue_service import _claim, enqueue_message, process_eligible, request_retry, serialize_payload
from app.modules.network_resilience.simulator import apply_scenario, apply_telemetry
from app.schemas.events import Priority
from app.schemas.network import NetworkScenario, NetworkState, TelemetryInput

@pytest.fixture
def net_env():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    users={}
    with TestingSession() as db:
        for role in (Role.ADMIN,Role.NETWORK_OPERATOR,Role.VIEWER):
            user=User(username=f"net-{role.value.lower()}-{uuid4().hex[:8]}",email=f"net-{uuid4().hex[:8]}@example.test",password_hash="not-used",role=role,is_active=True)
            db.add(user);db.flush();users[role.value]=(user.id,issue_token(user.id))
        db.commit()
    def override_db():
        with TestingSession() as db: yield db
    app.dependency_overrides[get_db]=override_db
    yield {"users":users,"sessions":TestingSession}
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine);engine.dispose()

async def _request(method,path,token=None,**kwargs):
    headers=kwargs.pop("headers",{})
    if token: headers["Authorization"]=f"Bearer {token}"
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        return await client.request(method,path,headers=headers,**kwargs)

def request(method,path,token=None,**kwargs): return asyncio.run(_request(method,path,token,**kwargs))

def test_network_health_score_boundaries_and_validation():
    reference=settings.network_bandwidth_reference_mbps
    healthy=score_telemetry(TelemetryInput(latency_ms=0,packet_loss_pct=0,bandwidth_mbps=reference,connected=True))
    assert healthy.score==100 and healthy.state==NetworkState.HEALTHY
    lost=score_telemetry(TelemetryInput(latency_ms=0,packet_loss_pct=100,bandwidth_mbps=reference,connected=True))
    assert lost.delivery_score==0 and lost.score<healthy.score
    disconnected=score_telemetry(TelemetryInput(latency_ms=0,packet_loss_pct=0,bandwidth_mbps=reference,connected=False))
    assert disconnected.score==0 and disconnected.state==NetworkState.DISCONNECTED
    with pytest.raises(ValidationError): TelemetryInput(latency_ms=-1,packet_loss_pct=0,bandwidth_mbps=reference,connected=True)
    with pytest.raises(ValidationError): TelemetryInput(latency_ms=0,packet_loss_pct=100.1,bandwidth_mbps=reference,connected=True)

def test_compression_round_trip_and_nonbeneficial_payload():
    raw,stored,encoding,algorithm=serialize_payload({"text":"x"*2000})
    assert encoding=="gzip" and algorithm=="gzip" and gzip.decompress(stored)==raw
    assert hashlib.sha256(raw).hexdigest()==hashlib.sha256(gzip.decompress(stored)).hexdigest()
    raw2,stored2,encoding2,_=serialize_payload({"x":"!"})
    assert encoding2=="identity" and stored2==raw2

def test_durable_service_store_forward_priority_and_retry(net_env,monkeypatch):
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        apply_scenario(db,NetworkScenario.DISCONNECTED,actor)
        lower,_=enqueue_message(db,{"id":"lower"},Priority.P3,actor)
        critical,_=enqueue_message(db,{"id":"critical"},Priority.P0,actor)
        lower_id,critical_id=lower.message_id,critical.message_id
        assert lower.state==critical.state=="PENDING"
    with net_env["sessions"]() as db:
        assert db.get(NetworkMessageRecord,lower_id).state=="PENDING"
        apply_scenario(db,NetworkScenario.RECOVERY,actor)
        result=process_eligible(db,actor)
        assert result["delivered"]==2
        attempts=list(db.scalars(select(NetworkDeliveryAttempt.message_id).order_by(NetworkDeliveryAttempt.id)))
        assert attempts==[critical_id,lower_id]
    with net_env["sessions"]() as db:
        apply_telemetry(db,TelemetryInput(latency_ms=25,packet_loss_pct=0,bandwidth_mbps=50,connected=True,delivery_failure_rate_pct=100),"custom","retry test",actor)
        monkeypatch.setattr(settings,"network_retry_limit",1)
        failed,_=enqueue_message(db,{"id":"retry"},Priority.P1,actor)
        failed_id=failed.message_id
        result=process_eligible(db,actor)
        assert result["failed"]==1 and db.get(NetworkMessageRecord,failed_id).state=="FAILED"
        request_retry(db,db.get(NetworkMessageRecord,failed_id),actor)
        result=process_eligible(db,actor)
        assert result["failed"]==1 and db.get(NetworkMessageRecord,failed_id).state=="FAILED"
        attempts=db.scalars(select(NetworkDeliveryAttempt).where(NetworkDeliveryAttempt.message_id==failed_id)).all()
        assert len(attempts)==2

def test_pending_message_survives_sqlite_engine_restart(tmp_path):
    database=tmp_path/"network-restart.db"
    first_engine=create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(first_engine)
    FirstSession=sessionmaker(bind=first_engine,autoflush=False,expire_on_commit=False)
    with FirstSession() as db:
        actor=User(username="restart-operator",email="restart@example.test",password_hash="not-used",role=Role.NETWORK_OPERATOR,is_active=True)
        db.add(actor);db.commit();actor_id=actor.id
        apply_scenario(db,NetworkScenario.DISCONNECTED,actor_id)
        message,_=enqueue_message(db,{"restart":"survives"},Priority.P1,actor_id)
        message_id=message.message_id
    first_engine.dispose()
    second_engine=create_engine(f"sqlite:///{database}")
    SecondSession=sessionmaker(bind=second_engine,autoflush=False,expire_on_commit=False)
    with SecondSession() as db:
        restored=db.get(NetworkMessageRecord,message_id)
        assert restored is not None and restored.state=="PENDING"
        assert restored.payload_encoding in {"identity","gzip"}
    second_engine.dispose()

def test_conditional_claim_allows_only_one_worker(net_env):
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        message,_=enqueue_message(db,{"claim":"once"},Priority.P0,actor)
        message_id=message.message_id
    with net_env["sessions"]() as first:
        claimed=_claim(first,message_id,datetime.now(timezone.utc))
        assert claimed is not None and claimed.state=="IN_FLIGHT"
    with net_env["sessions"]() as second:
        assert _claim(second,message_id,datetime.now(timezone.utc)) is None

def test_disconnected_store_forward_recovery_and_priority(net_env):
    token=net_env["users"][Role.NETWORK_OPERATOR.value][1]
    disconnected=request("POST","/api/v1/network/simulator/scenario",token,json={"scenario":"disconnected"})
    assert disconnected.status_code==200 and disconnected.json()["telemetry"]["connected"] is False
    low=request("POST","/api/v1/network/messages",token,json={"payload":{"message":"low"},"priority":"P3"})
    high=request("POST","/api/v1/network/messages",token,json={"payload":{"message":"high"},"priority":"P0"})
    assert low.status_code==201 and high.status_code==201
    low_id=low.json()["message"]["message_id"];high_id=high.json()["message"]["message_id"]
    assert low.json()["message"]["state"]=="PENDING" and high.json()["message"]["state"]=="PENDING"
    with net_env["sessions"]() as db:
        assert db.get(NetworkMessageRecord,low_id) is not None  # survives session close/reopen
    recovery=request("POST","/api/v1/network/simulator/scenario",token,json={"scenario":"recovery"})
    assert recovery.status_code==200 and recovery.json()["delivery"]["delivered"]==2
    with net_env["sessions"]() as db:
        assert db.get(NetworkMessageRecord,low_id).state=="DELIVERED"
        assert db.get(NetworkMessageRecord,high_id).state=="DELIVERED"
        order=list(db.scalars(select(NetworkDeliveryAttempt.message_id).order_by(NetworkDeliveryAttempt.id)))
        assert order==[high_id,low_id]
        assert db.scalar(select(KavachEventRecord).where(KavachEventRecord.source_module=="NETWORK")) is not None

def test_role_boundary_idempotency_and_failed_retry(net_env,monkeypatch):
    viewer=net_env["users"][Role.VIEWER.value][1]
    operator=net_env["users"][Role.NETWORK_OPERATOR.value][1]
    denied=request("POST","/api/v1/network/simulator/scenario",viewer,json={"scenario":"healthy"})
    assert denied.status_code==403
    request("POST","/api/v1/network/simulator/telemetry",operator,json={"latency_ms":10,"packet_loss_pct":0,"bandwidth_mbps":50,"connected":True,"delivery_failure_rate_pct":100})
    monkeypatch.setattr(settings,"network_retry_limit",1)
    body={"payload":{"sample":"failure-case"},"priority":"P1","idempotency_key":"failure-idempotency-key"}
    created=request("POST","/api/v1/network/messages",operator,json=body)
    assert created.status_code==201 and created.json()["message"]["state"]=="FAILED"
    duplicate=request("POST","/api/v1/network/messages",operator,json=body)
    assert duplicate.status_code==200 and duplicate.json()["created"] is False
    conflict=request("POST","/api/v1/network/messages",operator,json=body|{"payload":{"sample":"different"}})
    assert conflict.status_code==409
    retried=request("POST",f"/api/v1/network/messages/{created.json()['message']['message_id']}/retry",operator)
    assert retried.status_code==200 and retried.json()["state"]=="FAILED"
    history=request("GET",f"/api/v1/network/messages/{created.json()['message']['message_id']}",operator)
    assert len(history.json()["attempt_history"])==2

def test_network_status_and_unknown_event_fields(net_env):
    token=net_env["users"][Role.NETWORK_OPERATOR.value][1]
    response=request("GET","/api/v1/network/status",token)
    assert response.status_code==200 and response.json()["initialized"] is False
    event={"event_id":str(uuid4()),"timestamp":datetime.now(timezone.utc).isoformat(),"source_module":"NETWORK","event_type":"test","severity":"INFO","security_risk":0,"situation_risk":0,"network_health":100,"priority":"P3","recommended_action":"ALLOW","extra":"must be rejected"}
    from app.schemas.events import KavachEvent
    with pytest.raises(ValidationError): KavachEvent.model_validate(event)
