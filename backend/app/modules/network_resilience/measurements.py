"""Opt-in, bounded application-level measurement of one operator-configured endpoint."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import NetworkMeasurementRun, NetworkObservationRecord

_tasks: dict[str, asyncio.Task] = {}
_stop: dict[str, asyncio.Event] = {}


def measurement_configuration() -> dict:
    url = settings.network_measurement_target_url.strip()
    parsed = urlsplit(url) if url else None
    try:
        if url:_endpoint(url)
        valid=bool(url)
    except ValueError:
        valid=False
    label = settings.network_measurement_target_label.strip() or (parsed.hostname if valid else None)
    valid = valid and label is not None and len(label)<=120 and not any(ord(char)<32 for char in label)
    optional_ack = settings.network_ack_test_url.strip()
    optional_throughput = settings.network_throughput_test_url.strip()
    try:
        if optional_ack: _endpoint(optional_ack)
        ack_valid = bool(optional_ack)
    except ValueError:
        ack_valid = False
    try:
        if optional_throughput: _endpoint(optional_throughput)
        throughput_valid = bool(optional_throughput)
    except ValueError:
        throughput_valid = False
    return {
        "enabled": settings.network_measurement_enabled and valid,
        "configured": valid,
        "target_label": label,
        "probe_method": "bounded HTTP GET, one request at a time, redirects disabled",
        "timeout_seconds": settings.network_measurement_timeout_seconds,
        "interval_seconds": settings.network_measurement_interval_seconds,
        "max_samples": settings.network_measurement_max_samples,
        "ack_test_configured": ack_valid,
        "throughput_test_configured": throughput_valid,
        "throughput_max_bytes": settings.network_throughput_max_bytes if throughput_valid else None,
        "throughput_max_seconds": settings.network_throughput_max_seconds if throughput_valid else None,
    }


def _endpoint(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configured measurement endpoint must be an http(s) URL without credentials, query, or fragment")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Configured measurement endpoint has an invalid port") from None
    return url.strip()


def start_run(actor_id: str, samples: int) -> NetworkMeasurementRun:
    config = measurement_configuration()
    if not config["configured"] or not settings.network_measurement_enabled:
        raise ValueError("Real measurement collection is disabled or no valid endpoint is configured")
    if settings.network_ack_test_url and not config["ack_test_configured"]:
        raise ValueError("Configured acknowledgment test endpoint is invalid")
    if settings.network_throughput_test_url and not config["throughput_test_configured"]:
        raise ValueError("Configured throughput test endpoint is invalid")
    if samples < 1 or samples > settings.network_measurement_max_samples:
        raise ValueError(f"samples must be between 1 and {settings.network_measurement_max_samples}")
    with SessionLocal() as db:
        active = db.scalar(select(NetworkMeasurementRun).where(NetworkMeasurementRun.status.in_(["PENDING", "RUNNING", "STOPPING"])))
        if active:
            raise RuntimeError("A real measurement run is already active")
        run = NetworkMeasurementRun(status="RUNNING", target_label=config["target_label"], requested_by=actor_id,
            requested_samples=samples, interval_seconds=settings.network_measurement_interval_seconds,
            successful_probes=0, failed_probes=0, error_categories={})
        db.add(run); db.commit(); db.refresh(run)
        run_id = run.run_id
    stop_event = asyncio.Event()
    _stop[run_id] = stop_event
    _tasks[run_id] = asyncio.create_task(_collect(run_id, stop_event), name=f"kavachnet-measurement-{run_id}")
    return run


async def _collect(run_id: str, stop_event: asyncio.Event, client_factory=None) -> None:
    target = _endpoint(settings.network_measurement_target_url)
    client_factory = client_factory or httpx.AsyncClient
    errors: dict[str, int] = {}
    latencies: list[float] = []
    successes = failures = 0
    try:
        async with client_factory(timeout=settings.network_measurement_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            for index in range(settings.network_measurement_max_samples):
                with SessionLocal() as db:
                    run = db.get(NetworkMeasurementRun, run_id)
                    if not run or index >= run.requested_samples or stop_event.is_set():
                        break
                started = time.perf_counter()
                observed_at = datetime.now(timezone.utc)
                error_category = None
                try:
                    async with client.stream("GET", target, headers={"X-Kavachnet-Probe": str(uuid.uuid4())}) as response:
                        status = response.status_code
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                    if status < 200 or status >= 300:
                        error_category = f"http_error_{status}"
                    else:
                        successes += 1
                        latencies.append(elapsed_ms)
                        _save_observation(run_id, observed_at, "round_trip_latency", elapsed_ms, "ms", {"http_status": status})
                except httpx.TimeoutException:
                    error_category = "timeout"
                except httpx.ConnectError:
                    error_category = "connection_error"
                except httpx.RequestError:
                    error_category = "request_error"
                except Exception:
                    error_category = "probe_error"
                if error_category:
                    failures += 1
                    errors[error_category] = errors.get(error_category, 0) + 1
                _save_observation(run_id, observed_at, "probe_success", 0.0 if error_category else 1.0, "boolean", {"error_category": error_category})
                _save_observation(run_id, observed_at, "endpoint_availability_sample", 0.0 if error_category else 100.0, "percent", {"probe_denominator": 1})
                await _optional_ack(client, run_id, observed_at, errors)
                await _optional_throughput(client, run_id, observed_at, errors)
                with SessionLocal() as db:
                    run=db.get(NetworkMeasurementRun,run_id)
                    if run:
                        run.successful_probes=successes;run.failed_probes=failures;run.error_categories=dict(errors);db.commit()
                if index + 1 < settings.network_measurement_max_samples and not stop_event.is_set():
                    with SessionLocal() as db:
                        run = db.get(NetworkMeasurementRun, run_id)
                        is_last = not run or index + 1 >= run.requested_samples
                    if not is_last:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=settings.network_measurement_interval_seconds)
                        except asyncio.TimeoutError:
                            pass
    except asyncio.CancelledError:
        raise
    finally:
        ended = datetime.now(timezone.utc)
        with SessionLocal() as db:
            run = db.get(NetworkMeasurementRun, run_id)
            if run:
                run.status = "STOPPED" if stop_event.is_set() else "COMPLETED"
                run.ended_at = ended
                run.successful_probes = successes
                run.failed_probes = failures
                run.error_categories = errors
                db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="endpoint_availability", value=round(100 * successes / (successes + failures), 3) if successes + failures else None, unit="percent", target_label=run.target_label, measurement_method="successful HTTP probes / all attempted HTTP probes", experiment_id=run_id, observation_metadata={"successful_probes": successes, "failed_probes": failures, "denominator": successes + failures, "errors": errors}))
                db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="packet_loss", value=None, unit="percent", target_label=run.target_label, measurement_method="not measured by application-level HTTP probes", experiment_id=run_id, observation_metadata={"unavailable_reason": "HTTP probe failures are not packet-level loss measurements"}))
                if not latencies:
                    db.add(NetworkObservationRecord(timestamp_utc=ended,source_type="REAL_MEASUREMENT",source_name="configured_http_endpoint",measurement_type="round_trip_latency",value=None,unit="ms",target_label=run.target_label,measurement_method="no successful probe in this run",experiment_id=run_id,observation_metadata={"unavailable_reason":"no successful response"}))
                db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="measurement_count", value=float(successes+failures), unit="probes", target_label=run.target_label, measurement_method="count of attempted application-level probes", experiment_id=run_id, observation_metadata={"requested": run.requested_samples}))
                db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="measurement_coverage", value=round(100*(successes+failures)/run.requested_samples,3), unit="percent", target_label=run.target_label, measurement_method="attempted probes / requested probes", experiment_id=run_id, observation_metadata={"attempted": successes+failures,"requested":run.requested_samples}))
                if len(latencies) >= 2:
                    jitter = sum(abs(b-a) for a, b in zip(latencies, latencies[1:])) / (len(latencies)-1)
                    db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="jitter", value=round(jitter, 3), unit="ms", target_label=run.target_label, measurement_method="mean absolute difference between consecutive successful HTTP RTT samples", experiment_id=run_id, observation_metadata={"sample_count": len(latencies)}))
                else:
                    db.add(NetworkObservationRecord(timestamp_utc=ended, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type="jitter", value=None, unit="ms", target_label=run.target_label, measurement_method="requires at least two successful RTT samples", experiment_id=run_id, observation_metadata={"sample_count": len(latencies)}))
                missing_metrics=[("application_delivery_success","percent","no acknowledgment samples were collected"),("application_ack_latency","ms","no successful acknowledgment was collected"),("throughput","Mbps","no bounded throughput sample was collected")]
                for metric,unit,reason in missing_metrics:
                    available=db.scalar(select(func.count()).select_from(NetworkObservationRecord).where(NetworkObservationRecord.experiment_id==run_id,NetworkObservationRecord.measurement_type==metric,NetworkObservationRecord.value.is_not(None))) or 0
                    if not available:
                        configured=(settings.network_ack_test_url if metric.startswith("application_") else settings.network_throughput_test_url)
                        detail=reason if configured else "optional test endpoint is not configured"
                        db.add(NetworkObservationRecord(timestamp_utc=ended,source_type="REAL_MEASUREMENT",source_name="configured_http_endpoint",measurement_type=metric,value=None,unit=unit,target_label=run.target_label,measurement_method="optional measurement unavailable for this run",experiment_id=run_id,observation_metadata={"unavailable_reason":detail}))
                db.commit()
        _tasks.pop(run_id, None); _stop.pop(run_id, None)


def _save_observation(run_id, timestamp, metric, value, unit, metadata):
    with SessionLocal() as db:
        run = db.get(NetworkMeasurementRun, run_id)
        if not run:
            return
        db.add(NetworkObservationRecord(timestamp_utc=timestamp, source_type="REAL_MEASUREMENT", source_name="configured_http_endpoint", measurement_type=metric, value=value, unit=unit, target_label=run.target_label, measurement_method="bounded application-level HTTP probe", collection_interval_seconds=run.interval_seconds, experiment_id=run_id, observation_metadata=metadata))
        db.commit()


async def _optional_ack(client, run_id, timestamp, errors):
    if not settings.network_ack_test_url:
        return
    message_id = str(uuid.uuid4())
    started = time.perf_counter()
    value, category = 0.0, None
    try:
        url = _endpoint(settings.network_ack_test_url)
        response_body=bytearray()
        async with asyncio.timeout(settings.network_measurement_timeout_seconds):
            async with client.stream("POST",url,json={"message_id":message_id,"sent_at_utc":timestamp.isoformat()}) as response:
                status=response.status_code
                if response.is_success:
                    async for chunk in response.aiter_bytes(256):
                        if len(response_body)+len(chunk)>1024:
                            category="ack_response_too_large"
                            break
                        response_body.extend(chunk)
        try:
            body=json.loads(response_body) if response_body else {}
        except (json.JSONDecodeError,UnicodeDecodeError):
            body={}
        if category is None and 200<=status<300 and body.get("message_id") == message_id:
            value = 1.0
            _save_observation(run_id, timestamp, "application_ack_latency", round((time.perf_counter()-started)*1000, 3), "ms", {"message_id": message_id})
        elif category is None:
            category = "invalid_acknowledgment"
    except (httpx.TimeoutException, TimeoutError):
        category = "ack_timeout"
    except Exception:
        category = "ack_error"
    if category:
        errors[category] = errors.get(category, 0) + 1
    _save_observation(run_id, timestamp, "application_delivery_success", 100.0*value, "percent", {"message_id": message_id, "error_category": category})


async def _optional_throughput(client, run_id, timestamp, errors):
    if not settings.network_throughput_test_url:
        return
    started = time.perf_counter()
    transferred = 0
    try:
        url = _endpoint(settings.network_throughput_test_url)
        async with asyncio.timeout(settings.network_throughput_max_seconds):
            async with client.stream("GET", url,headers={"Accept-Encoding":"identity"}) as response:
                if not response.is_success:
                    raise httpx.HTTPStatusError("throughput endpoint returned an error", request=response.request, response=response)
                async for chunk in response.aiter_raw(8192):
                    transferred += min(len(chunk), settings.network_throughput_max_bytes-transferred)
                    if transferred >= settings.network_throughput_max_bytes:
                        break
        elapsed = time.perf_counter() - started
        if transferred and elapsed > 0:
            _save_observation(run_id, timestamp, "throughput", round(transferred * 8 / elapsed / 1_000_000, 4), "Mbps", {"bytes_transferred": transferred, "elapsed_seconds": round(elapsed, 6), "max_bytes": settings.network_throughput_max_bytes, "method": "bounded HTTP response body transfer"})
    except (httpx.TimeoutException,TimeoutError):
        errors["throughput_timeout"] = errors.get("throughput_timeout", 0) + 1
    except httpx.RequestError:
        errors["throughput_request_error"] = errors.get("throughput_request_error", 0) + 1
    except Exception as error:
        category=f"throughput_{type(error).__name__.lower()}"
        errors[category] = errors.get(category, 0) + 1


def stop_run(run_id: str) -> bool:
    event = _stop.get(run_id)
    if not event:
        return False
    event.set()
    return True


async def shutdown_measurement_runs() -> None:
    tasks = list(_tasks.values())
    for event in _stop.values():
        event.set()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def recover_interrupted_runs() -> None:
    """Mark jobs left active by a hard process exit as interrupted on startup."""
    with SessionLocal() as db:
        rows=db.scalars(select(NetworkMeasurementRun).where(NetworkMeasurementRun.status.in_(["PENDING","RUNNING","STOPPING"]))).all()
        for row in rows:
            row.status="INTERRUPTED"
            row.ended_at=datetime.now(timezone.utc)
            row.error_categories={**(row.error_categories or {}),"process_interruption":1}
        if rows: db.commit()
