"""Bounded ingestion for existing, publicly accessible RIPE Atlas ping measurements."""
import asyncio
import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import NetworkObservationRecord, PublicMeasurementIngestionRun

API = "https://atlas.ripe.net/api/v2"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_ingestion_lock = asyncio.Lock()


def configuration():
    measurement_id=settings.ripe_atlas_measurement_id.strip()
    return {"enabled":settings.ripe_atlas_enabled,"configured":measurement_id.isdigit(),
            "provider":"RIPE Atlas","measurement_id":measurement_id if measurement_id.isdigit() else None,
            "supported_type":"ping","timeout_seconds":settings.ripe_atlas_timeout_seconds,
            "max_results":settings.ripe_atlas_max_results,"lookback_hours":settings.ripe_atlas_lookback_hours,
            "maximum_window_days":30,"maximum_response_bytes":MAX_RESPONSE_BYTES}


class RipeAtlasError(ValueError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RipeAtlasError("Ingestion window timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _timestamp(raw) -> datetime:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        raise ValueError("timestamp must be a finite Unix timestamp")
    return datetime.fromtimestamp(raw, timezone.utc)


def _finite_nonnegative(raw, field: str):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(raw)


def _stable_observation_id(measurement_id: str, record: dict, metric: str) -> str:
    # RIPE has no row ID. The measurement/probe/Unix timestamp tuple is stable
    # across retrievals even if backend-enriched metadata changes afterward.
    probe_id = record.get("prb_id")
    if probe_id is None:
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        identity = f"ripe-atlas:{measurement_id}:no-probe:{record.get('timestamp')}:{metric}:{hashlib.sha256(canonical.encode()).hexdigest()}"
    else:
        identity = f"ripe-atlas:{measurement_id}:{probe_id}:{record.get('timestamp')}:{metric}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def map_ping_result(measurement_id: str, record: dict, target: str | None, run_id: str, retrieved_at: datetime):
    if not isinstance(record, dict):
        raise ValueError("result must be an object")
    if str(record.get("msm_id", measurement_id)) != measurement_id:
        raise ValueError("result measurement ID does not match requested measurement")
    observed_at = _timestamp(record.get("timestamp"))
    probe_id = record.get("prb_id")
    if probe_id is not None and (isinstance(probe_id, bool) or not isinstance(probe_id, (int, str))):
        raise ValueError("prb_id must be an integer or string")
    metadata = {"provider": "RIPE Atlas", "measurement_id": measurement_id, "probe_id": probe_id,
                "retrieved_at_utc": retrieved_at.isoformat(), "import_run_id": run_id,
                "source_url": f"{API}/measurements/{measurement_id}/results/", "target": str(target)[:255] if target else None,
                "destination_address": record.get("dst_addr"), "destination_name": record.get("dst_name"),
                "firmware_version": record.get("fw"), "measurement_version": record.get("mver"),
                "sent": record.get("sent"), "received": record.get("rcvd"), "average_latency_ms": record.get("avg")}
    output = []
    average = record.get("avg")
    if average is not None:
        output.append(("round_trip_latency", _finite_nonnegative(average, "avg"), "ms", "RIPE Atlas ping result avg (milliseconds)"))
    sent, received = record.get("sent"), record.get("rcvd")
    if sent is not None and received is not None:
        if isinstance(sent, bool) or isinstance(received, bool) or not isinstance(sent, int) or not isinstance(received, int) or sent <= 0 or received < 0 or received > sent:
            raise ValueError("sent/rcvd must be integers with sent > 0 and 0 <= rcvd <= sent")
        output.append(("packet_loss", (sent - received) * 100.0 / sent, "percent", "RIPE Atlas ping sent/rcvd response counts"))
        output.append(("probe_reachability", 1.0 if received > 0 else 0.0, "boolean", "RIPE Atlas ping response count; 1 means at least one reply"))
    elif average is None:
        # A ping result with a timestamp and probe but no RTT or packet counters
        # cannot support a latency/loss value. The rejected-record report explains it.
        raise ValueError("ping result contains neither avg latency nor sent/rcvd packet counts")
    if average is None and sent is not None and received is not None:
        output.append(("round_trip_latency", None, "ms", "RIPE Atlas ping result did not provide avg latency"))
    for metric, value, unit, method in output:
        output_row = NetworkObservationRecord(
            id=_stable_observation_id(measurement_id, record, metric), timestamp_utc=observed_at,
            source_type="RIPE_ATLAS_PUBLIC", source_name="RIPE Atlas", measurement_type=metric,
            value=value, unit=unit, target_label=(str(target)[:120] if target else None),
            measurement_method=method, experiment_id=run_id,
            observation_metadata={**metadata, **({"unavailable_reason":"avg latency field was not present"} if metric=="round_trip_latency" and value is None else {})})
        yield output_row


async def _get_json(client, url, params=None):
    try:
        async with client.stream("GET", url, params=params, headers={"Accept": "application/json"}) as response:
            if response.status_code == 404:
                raise RipeAtlasError("RIPE Atlas measurement/results not found or not publicly accessible (404)")
            if response.status_code == 429:
                raise RipeAtlasError("RIPE Atlas rate limit reached (429); retry later")
            if response.status_code in (401, 403):
                raise RipeAtlasError(f"RIPE Atlas denied access (HTTP {response.status_code}); the measurement may not be public")
            if response.status_code >= 400:
                raise RipeAtlasError(f"RIPE Atlas returned HTTP {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RipeAtlasError("RIPE Atlas response exceeded the 10 MiB safety limit; narrow the time window")
        return json.loads(body)
    except httpx.TimeoutException:
        raise RipeAtlasError("RIPE Atlas request timed out") from None
    except httpx.RequestError:
        raise RipeAtlasError("Could not connect to RIPE Atlas") from None
    except json.JSONDecodeError:
        raise RipeAtlasError("RIPE Atlas returned invalid JSON") from None


def _run_out(run: PublicMeasurementIngestionRun):
    return {"run_id": run.run_id, "provider": run.provider, "measurement_id": run.measurement_id,
            "status": run.status, "started_at": run.started_at, "ended_at": run.ended_at,
            "window_start": run.window_start, "window_end": run.window_end,
            "retrieved_count": run.retrieved_count, "accepted_count": run.accepted_count,
            "rejected_count": run.rejected_count, "deduplicated_count": run.deduplicated_count,
            "observations_inserted_count": run.observations_inserted_count,
            "observations_deduplicated_count": run.observations_deduplicated_count,
            "errors": run.errors}


async def ingest(db: Session, actor_id: str, start: datetime | None = None, end: datetime | None = None, client_factory=None):
    if not settings.ripe_atlas_enabled:
        raise RipeAtlasError("RIPE Atlas ingestion is disabled; set RIPE_ATLAS_ENABLED=true")
    measurement_id = settings.ripe_atlas_measurement_id.strip()
    if not measurement_id.isdigit() or len(measurement_id) > 20:
        raise RipeAtlasError("Configure a numeric RIPE_ATLAS_MEASUREMENT_ID")
    window_end = _aware_utc(end) if end else datetime.now(timezone.utc)
    window_start = _aware_utc(start) if start else window_end - timedelta(hours=settings.ripe_atlas_lookback_hours)
    if window_start >= window_end:
        raise RipeAtlasError("Ingestion start must be earlier than end")
    if window_end - window_start > timedelta(hours=720):
        raise RipeAtlasError("Ingestion window cannot exceed 30 days")
    if _ingestion_lock.locked():
        raise RipeAtlasError("A RIPE Atlas ingestion is already running on this server")
    async with _ingestion_lock:
        run = PublicMeasurementIngestionRun(measurement_id=measurement_id, status="RUNNING", requested_by=actor_id,
            window_start=window_start, window_end=window_end, errors=[])
        db.add(run); db.commit(); db.refresh(run)
        run_id = run.run_id
        client_factory = client_factory or httpx.AsyncClient
        retrieved_at = datetime.now(timezone.utc)
        errors = []
        retrieved_count = accepted = rejected = deduplicated = 0
        observations_inserted = observations_deduplicated = 0
        try:
            async with client_factory(timeout=settings.ripe_atlas_timeout_seconds, follow_redirects=False, trust_env=False) as client:
                meta = await _get_json(client, f"{API}/measurements/{measurement_id}/")
                if not isinstance(meta, dict) or str(meta.get("id")) != measurement_id:
                    raise RipeAtlasError("RIPE Atlas returned metadata for a different or invalid measurement ID")
                if meta.get("is_public") is False:
                    raise RipeAtlasError("The configured RIPE Atlas measurement is not public")
                measurement_type = str(meta.get("type", "")).lower()
                if measurement_type != "ping":
                    raise RipeAtlasError(f"Measurement {measurement_id} is type {measurement_type or 'unknown'}; this adapter currently supports ping measurements only")
                target = meta.get("target_ip") or meta.get("target") or meta.get("dst_name")
                params = {"start": int(window_start.timestamp()), "stop": int(window_end.timestamp()), "format": "json", "public_only": "true"}
                results = await _get_json(client, f"{API}/measurements/{measurement_id}/results/", params)
            if not isinstance(results, list):
                raise RipeAtlasError("RIPE Atlas results response must be a JSON array")
            retrieved_count = len(results)
            if retrieved_count > settings.ripe_atlas_max_results:
                results = results[-settings.ripe_atlas_max_results:]
                errors.append(f"Result window contained {retrieved_count} records; only the newest {settings.ripe_atlas_max_results} were processed")
            for index, source in enumerate(results):
                try:
                    rows = list(map_ping_result(measurement_id, source, target, run_id, retrieved_at))
                    if rows and (rows[0].timestamp_utc < window_start or rows[0].timestamp_utc > window_end):
                        raise ValueError("result timestamp falls outside the requested time window")
                    if not rows:
                        raise ValueError("result contained no supported metrics")
                    existing_ids=set(db.scalars(select(NetworkObservationRecord.id).where(NetworkObservationRecord.id.in_([row.id for row in rows]))).all())
                    result_inserted=False
                    for row in rows:
                        if row.id in existing_ids:
                            observations_deduplicated += 1
                        else:
                            db.add(row); observations_inserted += 1;result_inserted=True
                    if result_inserted:accepted += 1
                    else:deduplicated += 1
                except (ValueError, TypeError, OverflowError) as exc:
                    rejected += 1
                    if len(errors) < 100:
                        errors.append(f"result {index}: {str(exc)[:240]}")
            db.commit()
            status = "COMPLETED" if not rejected else "COMPLETED_WITH_REJECTIONS"
        except RipeAtlasError as exc:
            db.rollback(); status = "FAILED"; errors.append(str(exc))
        except Exception as exc:
            db.rollback(); status = "FAILED"; errors.append(f"RIPE Atlas ingestion failed ({type(exc).__name__})")
        run = db.get(PublicMeasurementIngestionRun, run_id)
        run.status = status; run.ended_at = datetime.now(timezone.utc)
        run.retrieved_count = retrieved_count; run.accepted_count = accepted
        run.rejected_count = rejected; run.deduplicated_count = deduplicated
        run.observations_inserted_count=observations_inserted;run.observations_deduplicated_count=observations_deduplicated;run.errors = errors
        db.commit(); db.refresh(run)
        return _run_out(run)


def get_run(db: Session, run_id: str):
    run = db.get(PublicMeasurementIngestionRun, run_id)
    return _run_out(run) if run else None


def recover_interrupted_ingestions():
    with SessionLocal() as db:
        db.execute(update(PublicMeasurementIngestionRun).where(PublicMeasurementIngestionRun.status == "RUNNING").values(
            status="INTERRUPTED", ended_at=datetime.now(timezone.utc), errors=["Server stopped before ingestion completed"]))
        db.commit()
