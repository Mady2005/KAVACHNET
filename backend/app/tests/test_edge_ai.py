from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.edge_ai.anomaly_detector import IsolationForestDetector
from app.modules.edge_ai.schemas import BatchReadings
from app.modules.edge_ai.sensor_validation import ReadingValidationError, validate_reading
from app.modules.edge_ai.service import EdgeAIService
from app.modules.edge_ai.simulator import generate_readings
from app.modules.edge_ai.model_manager import AI4IModelManager
from app.api.v1.routes import current_user
from app.db.session import get_db
from app.db.base import Base
from app.main import app
from app.models import Role
from app.db.session import SessionLocal
from app.models import AuditLog, KavachEventRecord
from app.schemas.events import KavachEvent, SourceModule


def reading(**updates):
    value = {"sensor_id": "temp-1", "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc), "temperature_c": 22.0, "reading_id": "r1"}
    value.update(updates)
    return value


def test_valid_reading_normalizes_timezone_and_allows_missing_optional_fields():
    row, warnings = validate_reading(reading(timestamp=datetime(2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5)))))
    assert row["timestamp"].utcoffset() == timedelta(0)
    assert "missing_humidity_pct" in warnings
    assert row["humidity_pct"] is None


@pytest.mark.parametrize("field,value", [("temperature_c", float("nan")), ("temperature_c", float("inf")), ("humidity_pct", 101), ("battery_pct", -1), ("vibration_mm_s", -0.1)])
def test_rejects_nonfinite_and_invalid_measurements(field, value):
    with pytest.raises((ReadingValidationError, ValidationError)):
        validate_reading(reading(**{field: value}))


def test_rejects_missing_sensor_id_timestamp_measurements_and_naive_timestamp():
    for data in ({"timestamp": datetime.now(timezone.utc), "temperature_c": 20}, reading(timestamp=datetime(2026, 1, 1)), {"sensor_id": "x", "timestamp": datetime.now(timezone.utc)}):
        with pytest.raises((ReadingValidationError, ValidationError)):
            validate_reading(data)


def test_threshold_detection_and_canonical_event_fields():
    service = EdgeAIService()
    event = service.analyze(reading(temperature_c=91, humidity_pct=45))
    assert isinstance(event, KavachEvent)
    assert event.source_module is SourceModule.EDGE_AI
    assert event.severity.value == "HIGH"
    assert event.security_risk == 0
    assert event.situation_risk == 0.85
    assert event.evidence["triggered_rules"][0]["rule_id"] == "TEMPERATURE_C_ABOVE_CONFIGURED_MAX"
    assert event.timestamp.utcoffset() == timedelta(0)


def test_normal_event_simulation_and_score_semantics():
    event = EdgeAIService().analyze(reading(simulation=True))
    assert event.event_type == "sensor_observation"
    assert event.simulation is True
    assert event.situation_risk == 0
    assert "not a probability" in event.evidence["anomaly_score_semantics"]


def test_duplicate_and_out_of_order_are_warnings_and_do_not_break_analysis():
    service = EdgeAIService()
    service.analyze(reading(reading_id="same"))
    duplicate = service.analyze(reading(reading_id="same"))
    assert "duplicate_reading_id" in duplicate.evidence["data_quality_warnings"]
    later = reading(timestamp=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc), reading_id="later")
    service.analyze(later)
    old = service.analyze(reading(timestamp=datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc), reading_id="old"))
    assert any("out_of_order" in w for w in old.evidence["data_quality_warnings"])


def test_temporal_spike_is_explained():
    service = EdgeAIService()
    service.analyze(reading(reading_id="first", temperature_c=20))
    event = service.analyze(reading(reading_id="second", timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), temperature_c=40))
    assert "TEMPERATURE_C_SUDDEN_CHANGE" in [r["rule_id"] for r in event.evidence["triggered_rules"]]


def test_repeated_abnormal_observations_are_identified():
    service = EdgeAIService()
    service.analyze(reading(reading_id="hot-1", temperature_c=90))
    event = service.analyze(reading(reading_id="hot-2", temperature_c=91, timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)))
    assert "REPEATED_ABNORMAL_READING" in [r["rule_id"] for r in event.evidence["triggered_rules"]]


def test_stale_observation_is_warned_without_becoming_anomaly():
    event = EdgeAIService().analyze(reading(reading_id="stale", simulation=False))
    assert "stale_reading_over_600_seconds" in event.evidence["data_quality_warnings"]
    assert event.event_type == "sensor_observation"


def test_detector_gracefully_handles_insufficient_and_missing_sklearn():
    detector = IsolationForestDetector()
    assert detector.fit([{"temperature_c": 20}] * 3) is False
    assert "10" in detector.unavailable_reason
    assert detector.score({"temperature_c": 20}) == (None, None, None)
    assert detector.fit([{"temperature_c": float("nan")}] * 10) is False
    assert "invalid" in detector.unavailable_reason


def test_simulator_reproducible_and_labeled():
    first, second = generate_readings(4, 19), generate_readings(4, 19)
    assert first == second
    assert all(row["simulation"] for row in first)
    assert first[-1]["temperature_c"] == 96


def test_metrics_count_results():
    service = EdgeAIService()
    service.analyze(reading())
    assert service.metrics()["processed"] == 1


def test_ai4i_model_manager_reports_missing_artifact_without_retraining(tmp_path):
    manager = AI4IModelManager(tmp_path / "missing.joblib")
    assert manager.load() is False
    assert manager.info()["available"] is False
    assert "missing" in manager.info()["reason"]


def test_edge_ai_api_requires_auth_and_accepts_edge_operator():
    async def request(method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    denied = asyncio.run(request("GET", "/api/v1/edge-ai/model-info"))
    assert denied.status_code == 401
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    def test_db():
        db = TestSession()
        try: yield db
        finally: db.close()
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role=Role.EDGE_OPERATOR, id=None)
    app.dependency_overrides[get_db] = test_db
    try:
        response = asyncio.run(request("POST", "/api/v1/edge-ai/analyze", json={
            "sensor_id": "api-test", "timestamp": "2026-01-01T00:00:00Z", "temperature_c": 96
        }))
        assert response.status_code == 200
        assert response.json()["persisted"] is True
        assert response.json()["event_persisted"] is True
        assert response.json()["event"]["source_module"] == "EDGE_AI"
        event_id = response.json()["event"]["event_id"]
        retrieved_event = asyncio.run(request("GET", f"/api/v1/events/{event_id}"))
        assert retrieved_event.status_code == 200 and retrieved_event.json()["event_id"] == event_id
        replay = asyncio.run(request("POST", "/api/v1/edge-ai/analyze", json={
            "sensor_id": "api-test", "timestamp": "2026-01-01T00:00:00Z", "temperature_c": 96
        }))
        assert replay.json()["replayed"] is True
        history = asyncio.run(request("GET", "/api/v1/edge-ai/history?sensor_id=api-test"))
        assert history.status_code == 200 and history.json()["total"] == 1
        changed = asyncio.run(request("POST", "/api/v1/edge-ai/analyze", json={
            "sensor_id": "api-test", "timestamp": "2026-01-01T00:01:00Z", "temperature_c": 25
        }))
        assert changed.status_code == 200 and changed.json()["replayed"] is False
        page = asyncio.run(request("GET", "/api/v1/edge-ai/history?sensor_id=api-test&limit=1"))
        assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
        invalid_range = asyncio.run(request("GET", "/api/v1/edge-ai/history?start=2026-02-01T00:00:00Z&end=2026-01-01T00:00:00Z"))
        assert invalid_range.status_code == 422
        prediction = asyncio.run(request("POST", "/api/v1/edge-ai/predict/maintenance", json={
            "product_type": "M", "air_temperature_k": 300, "process_temperature_k": 310,
            "rotational_speed_rpm": 1500, "torque_nm": 40, "tool_wear_min": 100
        }))
        assert prediction.status_code == 200 and prediction.json()["prediction_source"] == "ml"
        assert prediction.json()["decision_threshold"] == pytest.approx(0.36)
        assert prediction.json()["prediction"] == int(prediction.json()["class_probabilities"]["1"] >= 0.36)
        invalid_prediction = asyncio.run(request("POST", "/api/v1/edge-ai/predict/maintenance", json={
            "product_type": "M", "air_temperature_k": "hot", "process_temperature_k": 310,
            "rotational_speed_rpm": 1500, "torque_nm": 40, "tool_wear_min": 100
        }))
        assert invalid_prediction.status_code == 422
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
        test_engine.dispose()
    unauthorized_history = asyncio.run(request("GET", "/api/v1/edge-ai/history"))
    assert unauthorized_history.status_code == 401


def test_rejects_string_measurements_and_unknown_fields():
    with pytest.raises((ReadingValidationError, ValidationError)):
        validate_reading(reading(temperature_c="hot"))
    with pytest.raises((ReadingValidationError, ValidationError)):
        validate_reading(reading(temperature_c=20, random="unknown"))
    with pytest.raises(ValidationError):
        BatchReadings(readings=[{}] * 101)


def test_shared_event_endpoint_is_idempotent_and_rejects_id_reuse():
    async def request(body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/v1/events", json=body)

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role=Role.ADMIN, id=None)
    first_id = str(uuid4())
    other_id = str(uuid4())
    original = EdgeAIService().analyze(reading(timestamp=datetime.now(timezone.utc))).model_dump(mode="json")
    original["event_id"] = first_id
    second_device = original | {"event_id": other_id, "evidence": {"sensor_id": "device-b"}}
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    old_event = original | {"event_id": str(uuid4()), "timestamp": old_timestamp}
    old_id = old_event["event_id"]
    try:
        created = asyncio.run(request(original))
        replay = asyncio.run(request(original))
        collision = asyncio.run(request(original | {"evidence": {"sensor_id": "changed"}}))
        distinct = asyncio.run(request(second_device))
        old_created = asyncio.run(request(old_event))
        old_replay = asyncio.run(request(old_event))
        assert created.status_code == 201
        assert replay.status_code == 200 and replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["event_id"] == first_id
        assert collision.status_code == 409
        assert distinct.status_code == 201
        assert old_created.status_code == 201 and old_replay.status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)
        db = SessionLocal()
        ids = [first_id, other_id, old_id]
        db.query(AuditLog).filter(AuditLog.resource_id.in_(ids)).delete(synchronize_session=False)
        db.query(KavachEventRecord).filter(KavachEventRecord.event_id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_concurrent_identical_event_posts_persist_once():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await asyncio.gather(*[client.post("/api/v1/events", json=payload) for _ in range(2)])

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role=Role.ADMIN, id=None)
    payload = EdgeAIService().analyze(reading(timestamp=datetime.now(timezone.utc))).model_dump(mode="json")
    try:
        results = asyncio.run(run())
        assert sorted(result.status_code for result in results) == [200, 201]
    finally:
        app.dependency_overrides.pop(current_user, None)
        db = SessionLocal()
        db.query(AuditLog).filter(AuditLog.resource_id == str(payload["event_id"])).delete(synchronize_session=False)
        db.query(KavachEventRecord).filter(KavachEventRecord.event_id == str(payload["event_id"])).delete(synchronize_session=False)
        db.commit()
        db.close()
