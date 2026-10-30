import asyncio
import httpx
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sys

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.auth.security import issue_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.api.v1 import network as network_api
from app.models import AuditLog, DatasetRegistry, NetworkMeasurementRun, NetworkObservationRecord, PublicMeasurementIngestionRun, Role, User
from app.modules.network_resilience import measurements
from app.modules.network_resilience import ripe_atlas
from app.modules.network_resilience.dataset_import import import_dataset
from app.modules.network_resilience.simulator import apply_scenario
from app.schemas.network import NetworkScenario

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def net_env():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
    with sessions() as db:
        operator=User(username="network-provenance-test",email="network-provenance@example.test",password_hash="not-used",role=Role.NETWORK_OPERATOR,is_active=True)
        viewer=User(username="network-provenance-viewer",email="network-provenance-viewer@example.test",password_hash="not-used",role=Role.VIEWER,is_active=True)
        db.add_all([operator,viewer]);db.commit();actor_id=operator.id;viewer_id=viewer.id
    def override_db():
        with sessions() as db: yield db
    app.dependency_overrides[get_db]=override_db
    yield {"users":{"NETWORK_OPERATOR":(actor_id,issue_token(actor_id)),"VIEWER":(viewer_id,issue_token(viewer_id))},"sessions":sessions}
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine);engine.dispose()


def test_simulator_observations_are_explicitly_simulated(net_env):
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        apply_scenario(db,NetworkScenario.HEALTHY,actor)
        records=db.scalars(select(NetworkObservationRecord)).all()
        assert records
        assert {record.source_type for record in records}=={"SIMULATED"}
        assert {record.measurement_type for record in records}>={"round_trip_latency","packet_loss","throughput"}


def test_dataset_csv_import_preserves_provenance_and_rejects_bad_rows(net_env):
    csv_data=(Path(__file__).parent/"fixtures"/"network_telemetry_fixture.csv").read_bytes()
    with net_env["sessions"]() as db:
        result=import_dataset(db,csv_data,"network_telemetry_fixture.csv","synthetic-fixture-only",None,"test fixture only","fixture-1",None)
        assert result["accepted_rows"]==1 and result["rejected_rows"]==1
        item=db.scalar(select(NetworkObservationRecord).where(NetworkObservationRecord.dataset_id==result["dataset_id"]))
        assert item.source_type=="PUBLIC_DATASET" and item.value==250 and item.unit=="microseconds"
        dataset=db.get(DatasetRegistry,result["dataset_id"])
        assert dataset.checksum==result["sha256"] and dataset.provenance["rejected"][0]["row"]==3
        assert "packet_loss" in dataset.provenance["unsupported_operational_metrics"]


def test_cic_style_mapping_imports_only_selected_flow_feature(net_env):
    csv_data=b"Timestamp,Flow Duration,Label\n2026-10-26T10:00:00+00:00,250,BENIGN\n"
    with net_env["sessions"]() as db:
        result=import_dataset(db,csv_data,"mapping-fixture.csv","synthetic-mapping-fixture",None,"test fixture only","fixture-1",{"measurement_type":"flow_duration","value_column":"Flow Duration","timestamp_column":"Timestamp","unit":"microseconds"})
        row=db.scalar(select(NetworkObservationRecord).where(NetworkObservationRecord.dataset_id==result["dataset_id"]))
        assert row.value==250 and row.measurement_type=="flow_duration"
        assert row.observation_metadata=={}  # labels and addresses are not copied into provenance metadata


def test_import_rejects_unit_mismatch_and_unsafe_provenance_url(net_env):
    data=b"timestamp_utc,measurement_type,value,unit\n2026-10-26T10:00:00Z,round_trip_latency,20,seconds\n"
    with net_env["sessions"]() as db:
        result=import_dataset(db,data,"bad.csv","unit-check",None,None,None,None)
        assert result["accepted_rows"]==0 and "requires unit ms" in result["rejected"][0]["error"]
        with pytest.raises(ValueError,match="credentials or query"):
            import_dataset(db,data,"bad.csv","unsafe-url","https://example.test/data?token=secret",None,None,None)


def test_json_import_preserves_unavailable_null_and_source_support(net_env):
    content=b'{"observations":[{"timestamp_utc":"2026-10-26T10:00:00+00:00","measurement_type":"round_trip_latency","value":null,"unit":"ms"}]}'
    with net_env["sessions"]() as db:
        result=import_dataset(db,content,"telemetry.json","direct-measurement-fixture",None,"test fixture only",None,None)
        row=db.scalar(select(NetworkObservationRecord).where(NetworkObservationRecord.dataset_id==result["dataset_id"]))
        assert result["accepted_rows"]==1 and row.value is None
        assert "round_trip_latency" not in result["unsupported_operational_metrics"]


def test_real_collection_disabled_without_explicit_configuration(monkeypatch):
    monkeypatch.setattr(settings,"network_measurement_enabled",False)
    monkeypatch.setattr(settings,"network_measurement_target_url","")
    config=measurements.measurement_configuration()
    assert config["enabled"] is False and config["configured"] is False
    with pytest.raises(ValueError,match="disabled"):
        measurements.start_run("not-used",1)


def test_import_api_requires_operator_and_audits_successful_import(net_env):
    async def call():
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
            body=Path(__file__).parent/"fixtures"/"network_telemetry_fixture.csv"
            return await client.post("/api/v1/network/datasets/import",headers={"Authorization":f"Bearer {net_env['users']['NETWORK_OPERATOR'][1]}"},data={"name":"api-fixture-only","license_name":"test fixture"},files={"file":("fixture.csv",body.read_bytes(),"text/csv")})
    response=asyncio.run(call())
    assert response.status_code==201
    dataset_id=response.json()["dataset_id"]
    with net_env["sessions"]() as db:
        audit=db.scalar(select(AuditLog).where(AuditLog.action=="network.dataset.import",AuditLog.resource_id==dataset_id))
        assert audit is not None and audit.outcome=="success"


def test_measurement_start_requires_network_role_and_data_returns_pending(net_env,monkeypatch):
    monkeypatch.setattr(settings,"network_measurement_enabled",False)
    async def call():
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
            headers={"Authorization":f"Bearer {net_env['users']['VIEWER'][1]}"}
            denied=await client.post("/api/v1/network/measurements/runs",headers=headers,json={"samples":1})
            pending=await client.get("/api/v1/network/evaluation?source_type=REAL_MEASUREMENT",headers=headers)
            return denied,pending
    denied,pending=asyncio.run(call())
    assert denied.status_code==403
    assert pending.status_code==200 and pending.json()["status"]=="PENDING_DATA_COLLECTION"


def test_measurement_start_route_audits_the_configured_run(net_env,monkeypatch):
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        record=NetworkMeasurementRun(status="RUNNING",target_label="local-test-endpoint",requested_by=actor,requested_samples=1,interval_seconds=.5,successful_probes=0,failed_probes=0,error_categories={})
        db.add(record);db.commit();db.refresh(record)
    monkeypatch.setattr(network_api,"start_run",lambda actor_id,samples:record)
    async def call():
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
            return await client.post("/api/v1/network/measurements/runs",headers={"Authorization":f"Bearer {net_env['users']['NETWORK_OPERATOR'][1]}"},json={"samples":1})
    response=asyncio.run(call())
    assert response.status_code==202 and response.json()["target_label"]=="local-test-endpoint"
    with net_env["sessions"]() as db:
        audit=db.scalar(select(AuditLog).where(AuditLog.action=="network.measurement.start",AuditLog.resource_id==record.run_id))
        assert audit is not None and audit.outcome=="success"


def test_measurement_collector_uses_bounded_local_http_probe(net_env,monkeypatch):
    requests=[]
    class BodyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x"*4096
        async def aclose(self):
            return None
    def handler(request):
        requests.append(request)
        if request.url.path=="/ack":
            import json
            message_id=json.loads(request.content)["message_id"]
            return httpx.Response(200,json={"message_id":message_id},request=request)
        if request.url.path=="/throughput":
            return httpx.Response(200,stream=BodyStream(),request=request)
        return httpx.Response(200,content=b"ok",request=request)
    transport=httpx.MockTransport(handler)
    def client_factory(**kwargs):return httpx.AsyncClient(transport=transport,**kwargs)
    monkeypatch.setattr(measurements,"SessionLocal",net_env["sessions"])
    monkeypatch.setattr(settings,"network_measurement_enabled",True)
    monkeypatch.setattr(settings,"network_measurement_target_url","http://127.0.0.1:8765/health")
    monkeypatch.setattr(settings,"network_measurement_target_label","local-test-endpoint")
    monkeypatch.setattr(settings,"network_measurement_max_samples",1)
    monkeypatch.setattr(settings,"network_measurement_timeout_seconds",1.0)
    monkeypatch.setattr(settings,"network_measurement_interval_seconds",0.5)
    monkeypatch.setattr(settings,"network_ack_test_url","http://127.0.0.1:8765/ack")
    monkeypatch.setattr(settings,"network_throughput_test_url","http://127.0.0.1:8765/throughput")
    monkeypatch.setattr(settings,"network_throughput_max_bytes",1024)
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        run=NetworkMeasurementRun(status="RUNNING",target_label="local-test-endpoint",requested_by=actor,requested_samples=1,interval_seconds=.5,successful_probes=0,failed_probes=0,error_categories={})
        db.add(run);db.commit();run_id=run.run_id
    asyncio.run(asyncio.wait_for(measurements._collect(run_id,asyncio.Event(),client_factory=client_factory),timeout=5))
    with net_env["sessions"]() as db:
        run=db.get(NetworkMeasurementRun,run_id)
        items=db.scalars(select(NetworkObservationRecord).where(NetworkObservationRecord.experiment_id==run_id)).all()
        metrics={row.measurement_type:row for row in items}
        assert run.status=="COMPLETED" and run.successful_probes==1 and run.failed_probes==0
        assert [(request.method,request.url.path) for request in requests]==[("GET","/health"),("POST","/ack"),("GET","/throughput")]
        assert metrics["round_trip_latency"].value is not None
        assert metrics["endpoint_availability"].value==100
        assert metrics["packet_loss"].value is None
        assert metrics["application_delivery_success"].value==100
        assert metrics["application_ack_latency"].value is not None
        assert metrics["throughput"].observation_metadata.get("bytes_transferred")==1024, run.error_categories
        assert all(row.source_type=="REAL_MEASUREMENT" for row in items)


def _ripe_client_factory(handler):
    transport=httpx.MockTransport(handler)
    return lambda **kwargs:httpx.AsyncClient(transport=transport,**kwargs)


def test_ripe_ping_mapping_keeps_unsupported_metrics_unavailable():
    now=datetime.now(timezone.utc)
    latency_only=list(ripe_atlas.map_ping_result("9",{"msm_id":9,"prb_id":1,"timestamp":int(now.timestamp()),"avg":3.25},"example.net","run",now))
    assert len(latency_only)==1 and latency_only[0].measurement_type=="round_trip_latency" and latency_only[0].unit=="ms"
    no_average=list(ripe_atlas.map_ping_result("9",{"msm_id":9,"prb_id":1,"timestamp":int(now.timestamp()),"sent":2,"rcvd":0},"example.net","run",now))
    missing_latency=next(row for row in no_average if row.measurement_type=="round_trip_latency")
    assert missing_latency.value is None and missing_latency.observation_metadata["unavailable_reason"]
    assert next(row for row in no_average if row.measurement_type=="packet_loss").value==100


@pytest.mark.asyncio
async def test_ripe_atlas_ingestion_maps_public_ping_provenance_and_is_idempotent(net_env,monkeypatch):
    now=datetime.now(timezone.utc);measurement_id="123456"
    source={"msm_id":int(measurement_id),"prb_id":42,"timestamp":int(now.timestamp()),"avg":12.5,"sent":4,"rcvd":3,"fw":5000}
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/results/"):return httpx.Response(200,json=[source],request=request)
        return httpx.Response(200,json={"id":int(measurement_id),"type":"ping","target":"198.51.100.1"},request=request)
    monkeypatch.setattr(settings,"ripe_atlas_enabled",True);monkeypatch.setattr(settings,"ripe_atlas_measurement_id",measurement_id)
    monkeypatch.setattr(settings,"ripe_atlas_max_results",50);monkeypatch.setattr(settings,"ripe_atlas_timeout_seconds",2)
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    async def run():
        with net_env["sessions"]() as db:
            return await ripe_atlas.ingest(db,actor,now-timedelta(minutes=2),now+timedelta(minutes=2),_ripe_client_factory(handler))
    first=await run();source["from"]="203.0.113.44";second=await run()
    assert first["status"]=="COMPLETED" and first["accepted_count"]==1 and first["observations_inserted_count"]==3
    assert second["accepted_count"]==0 and second["deduplicated_count"]==1 and second["observations_deduplicated_count"]==3
    assert len(calls)==4 and calls[1].url.params["public_only"]=="true"
    with net_env["sessions"]() as db:
        rows=db.scalars(select(NetworkObservationRecord).where(NetworkObservationRecord.source_type=="RIPE_ATLAS_PUBLIC")).all()
        assert {r.measurement_type for r in rows}=={"round_trip_latency","packet_loss","probe_reachability"}
        assert {r.value for r in rows if r.measurement_type=="packet_loss"}=={25.0}
        assert all(r.observation_metadata["measurement_id"]==measurement_id and r.observation_metadata["probe_id"]==42 for r in rows)
        assert db.scalar(select(func.count()).select_from(NetworkObservationRecord).where(NetworkObservationRecord.source_type=="REAL_MEASUREMENT"))==0


@pytest.mark.asyncio
async def test_ripe_atlas_empty_and_bad_results_are_explicit(net_env,monkeypatch):
    monkeypatch.setattr(settings,"ripe_atlas_enabled",True);monkeypatch.setattr(settings,"ripe_atlas_measurement_id","789")
    now=datetime.now(timezone.utc)
    response=[{"msm_id":789,"prb_id":3,"timestamp":int(now.timestamp()),"result":"bad"}]
    def handler(request):
        body={"id":789,"type":"ping"} if not request.url.path.endswith("/results/") else response
        return httpx.Response(200,json=body,request=request)
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    async def ingest_with(results):
        nonlocal response;response=results
        with net_env["sessions"]() as db:return await ripe_atlas.ingest(db,actor,now-timedelta(minutes=2),now+timedelta(minutes=2),_ripe_client_factory(handler))
    malformed=await ingest_with(response);empty=await ingest_with([])
    assert malformed["status"]=="COMPLETED_WITH_REJECTIONS" and malformed["rejected_count"]==1
    assert empty["status"]=="COMPLETED" and empty["retrieved_count"]==0 and empty["accepted_count"]==0


@pytest.mark.asyncio
async def test_ripe_atlas_rejects_non_ping_and_http_error(net_env,monkeypatch):
    monkeypatch.setattr(settings,"ripe_atlas_enabled",True);monkeypatch.setattr(settings,"ripe_atlas_measurement_id","7")
    now=datetime.now(timezone.utc);actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    def handler(request):return httpx.Response(404,json={"detail":"not found"},request=request)
    async def run():
        with net_env["sessions"]() as db:return await ripe_atlas.ingest(db,actor,now-timedelta(minutes=1),now+timedelta(minutes=1),_ripe_client_factory(handler))
    result=await run()
    assert result["status"]=="FAILED" and "not found or not publicly accessible" in result["errors"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected",[("rate_limit","rate limit"),("timeout","timed out")])
async def test_ripe_atlas_rate_limit_and_timeout_are_reported(net_env,monkeypatch,failure,expected):
    monkeypatch.setattr(settings,"ripe_atlas_enabled",True);monkeypatch.setattr(settings,"ripe_atlas_measurement_id","8")
    now=datetime.now(timezone.utc);actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    def handler(request):
        if request.url.path.endswith("/results/"):
            if failure=="rate_limit":return httpx.Response(429,request=request)
            raise httpx.ReadTimeout("mock timeout",request=request)
        return httpx.Response(200,json={"id":8,"type":"ping","is_public":True},request=request)
    with net_env["sessions"]() as db:
        result=await ripe_atlas.ingest(db,actor,now-timedelta(minutes=1),now+timedelta(minutes=1),_ripe_client_factory(handler))
    assert result["status"]=="FAILED" and expected in result["errors"][0]


@pytest.mark.asyncio
async def test_ripe_atlas_route_is_operator_only_and_disabled_by_default(net_env,monkeypatch):
    monkeypatch.setattr(settings,"ripe_atlas_enabled",False)
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        denied=await client.post("/api/v1/network/ripe-atlas/ingestions",headers={"Authorization":f"Bearer {net_env['users']['VIEWER'][1]}"},json={})
    assert denied.status_code==403


@pytest.mark.asyncio
async def test_public_observation_filters_and_exports_are_authenticated(net_env):
    actor=net_env["users"][Role.NETWORK_OPERATOR.value][0]
    with net_env["sessions"]() as db:
        run=PublicMeasurementIngestionRun(measurement_id="555",status="COMPLETED",requested_by=actor,window_start=datetime.now(timezone.utc)-timedelta(minutes=1),window_end=datetime.now(timezone.utc),retrieved_count=1,accepted_count=1,rejected_count=0,deduplicated_count=0,observations_inserted_count=1,observations_deduplicated_count=0,errors=[])
        db.add(run);db.flush()
        db.add(NetworkObservationRecord(timestamp_utc=datetime.now(timezone.utc),source_type="RIPE_ATLAS_PUBLIC",source_name="RIPE Atlas",measurement_type="round_trip_latency",value=8.5,unit="ms",target_label="example.net",measurement_method="test source field",experiment_id=run.run_id,observation_metadata={"measurement_id":"555","probe_id":4}))
        db.commit()
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        headers={"Authorization":f"Bearer {net_env['users'][Role.NETWORK_OPERATOR.value][1]}"}
        filtered=await client.get("/api/v1/network/observations?source_type=RIPE_ATLAS_PUBLIC&provider=RIPE%20Atlas&measurement_id=555",headers=headers)
        csv_response=await client.get("/api/v1/network/observations/export.csv?source_type=RIPE_ATLAS_PUBLIC&measurement_id=555",headers=headers)
        evaluation=await client.get(f"/api/v1/network/evaluation?source_type=RIPE_ATLAS_PUBLIC&experiment_id={run.run_id}",headers=headers)
        unauthenticated=await client.get("/api/v1/network/observations?source_type=RIPE_ATLAS_PUBLIC")
    assert filtered.status_code==200 and filtered.json()["total"]==1
    assert "round_trip_latency" in csv_response.text and csv_response.headers["content-type"].startswith("text/csv")
    assert evaluation.status_code==200 and evaluation.json()["status"]=="MEASURED" and evaluation.json()["public_runs"][0]["measurement_id"]=="555"
    assert unauthenticated.status_code==401
