"""Demonstration correlation policy for persisted Edge AI canonical events.

Only `EDGE_AI` `sensor_anomaly` events with a sensor ID and a rule field (or an
explicit Isolation Forest method) are eligible. Rule categories are the sorted
set of reported sensor fields; model-only findings use `model_anomaly`. Events
correlate only for the same sensor and exact category, and only when the full
incident timestamp span remains within the configured window. This fixed-span
rule prevents a stream of events from extending an incident forever. Late
arrivals may join when their source timestamp keeps that same full span within
the window; older/out-of-window events start a separate incident. Missing or
invalid timestamps are rejected by the canonical event schema before storage.

Incident severity is the highest existing canonical event severity attached
(INFO < LOW < MEDIUM < HIGH < CRITICAL). Correlation never infers CRITICAL from
ML output. OPEN and ACKNOWLEDGED incidents may receive events; RESOLVED
incidents are terminal and never receive new events. This policy is for a
development demonstration, not validated equipment operations.
"""
from dataclasses import dataclass

from app.core.config import settings
from app.schemas.events import KavachEvent, Severity, SourceModule

SEVERITY_RANK = {severity: index for index, severity in enumerate(Severity)}
SUPPORTED_RULE_FIELDS = {
    "temperature_c", "humidity_pct", "vibration_mm_s", "voltage_v", "battery_pct",
}


@dataclass(frozen=True)
class CorrelationPolicy:
    window_seconds: int = settings.incident_correlation_window_seconds


DEFAULT_POLICY = CorrelationPolicy()


def correlation_group(event: KavachEvent) -> tuple[str, str] | None:
    """Return (sensor ID, compatible category) for an eligible event."""
    if event.source_module is not SourceModule.EDGE_AI or event.event_type != "sensor_anomaly":
        return None
    if event.severity not in {Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL}:
        return None
    evidence = event.evidence if isinstance(event.evidence, dict) else {}
    sensor_id = evidence.get("sensor_id")
    if not isinstance(sensor_id, str) or not sensor_id.strip():
        return None
    rules = evidence.get("triggered_rules", [])
    fields = sorted({
        item.get("field") for item in rules
        if isinstance(item, dict) and item.get("field") in SUPPORTED_RULE_FIELDS
    }) if isinstance(rules, list) else []
    if fields:
        category = "sensor_anomaly:" + ",".join(fields)
    else:
        methods = evidence.get("detection_methods", [])
        if not isinstance(methods, list) or "isolation_forest" not in methods:
            return None
        category = "sensor_anomaly:model_anomaly"
    return sensor_id.strip(), category


def highest_severity(current: str, candidate: str) -> str:
    return max((current, candidate), key=lambda value: SEVERITY_RANK[Severity(value)])
