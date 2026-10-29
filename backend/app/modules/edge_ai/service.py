"""Local Edge AI analysis and canonical event construction."""
from collections import OrderedDict, deque
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from app.schemas.events import Action, KavachEvent, Priority, Severity, SourceModule
from app.services.interfaces import SensorAnomalyService

from .anomaly_detector import IsolationForestDetector, extract_features
from .rules import RuleConfig, check_thresholds
from .sensor_validation import validate_reading

STALE_AFTER_SECONDS = 600
FUTURE_CLOCK_SKEW_SECONDS = 300


class EdgeAIService(SensorAnomalyService):
    """Bounded stateful service; malformed readings raise ValueError."""

    def __init__(self, rule_config: RuleConfig | None = None, history_size: int = 256, detector: IsolationForestDetector | None = None):
        self.rule_config = rule_config or RuleConfig()
        self.history_size = history_size
        self.detector = detector or IsolationForestDetector()
        self.history: OrderedDict[str, deque] = OrderedDict()
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.lock = RLock()
        self.processed = 0
        self.invalid = 0

    def analyze_detailed(self, raw: dict) -> tuple[KavachEvent, dict]:
        try:
            reading, warnings = validate_reading(raw)
        except ValueError:
            self.invalid += 1
            raise
        sensor_id = reading["sensor_id"]
        stamp = reading["timestamp"]
        fingerprint = str(reading.get("reading_id") or "")
        with self.lock:
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            if not reading.get("simulation") and age > STALE_AFTER_SECONDS:
                warnings.append("stale_reading_over_600_seconds")
            elif age < -FUTURE_CLOCK_SKEW_SECONDS:
                warnings.append("reading_timestamp_more_than_300_seconds_in_future")
            if fingerprint:
                key = f"{sensor_id}:{fingerprint}"
                if key in self.seen:
                    warnings.append("duplicate_reading_id")
                self.seen[key] = None
                self.seen.move_to_end(key)
                while len(self.seen) > self.history_size * 4:
                    self.seen.popitem(last=False)
            series = self.history.setdefault(sensor_id, deque(maxlen=self.history_size))
            prior = series[-1] if series else None
            out_of_order = prior is not None and stamp < prior["timestamp"]
            if out_of_order:
                warnings.append("out_of_order_timestamp_not_added_to_temporal_history")
            rules = check_thresholds(reading, self.rule_config)
            features = extract_features(reading)
            if rules:
                active_ids = {rule["rule_id"] for rule in rules}
                previous_matches = sum(
                    bool(active_ids.intersection(rule["rule_id"] for rule in check_thresholds(item, self.rule_config)))
                    for item in list(series)[-4:]
                )
                if previous_matches:
                    rules.append({"rule_id": "REPEATED_ABNORMAL_READING", "previous_abnormal_observations": previous_matches})
            delta_rules = []
            if prior and not out_of_order:
                for field, value in features.items():
                    old = prior.get(field)
                    if old is not None and abs(value - old) > max(abs(old) * 0.5, 10.0):
                        delta_rules.append({"rule_id": f"{field.upper()}_SUDDEN_CHANGE", "field": field, "previous": old, "observed": value})
            rules.extend(delta_rules)
            if not out_of_order:
                series.append(reading)
            self.history.move_to_end(sensor_id)
            while len(self.history) > 512:
                self.history.popitem(last=False)
            model_score, model_flag, model_raw_score = self.detector.score(features)
            rule_flag = bool(rules)
            # Model-only findings are informational evidence; rule or model agreement elevates operational risk.
            anomaly = rule_flag or model_flag is True
            methods = (["rules"] if rule_flag else []) + (["isolation_forest"] if model_flag is not None else [])
            if model_flag is not None and model_flag != rule_flag:
                warnings.append("rule_model_disagreement")
            score = 0.0 if not anomaly else (0.85 if rules else (model_score or 0.5))
            score = min(1.0, max(0.0, score))
            if score >= 0.85:
                severity, priority, action = Severity.HIGH, Priority.P1, Action.ALERT
            elif anomaly:
                severity, priority, action = Severity.MEDIUM, Priority.P2, Action.WARN
            else:
                severity, priority, action = Severity.INFO, Priority.P3, Action.STORE
            explanation = self._explain(reading, rules, warnings, anomaly)
            confidence = None  # No calibration or labelled evaluation is available.
            evidence = {
                "sensor_id": sensor_id, "sensor_type": reading.get("sensor_type"),
                "observed": {key: reading.get(key) for key in features},
                "original_reading": {key: value for key, value in reading.items() if key != "metadata"},
                "received_timestamp": raw.get("timestamp"),
                "metadata": reading.get("metadata", {}), "triggered_rules": rules,
                "detection_methods": methods, "anomaly_score": score,
                "anomaly_score_semantics": "bounded operational finding score; not a probability",
                "model": self.detector.name, "model_version": self.detector.version,
                "model_display_score": model_score, "model_decision_function_score": model_raw_score,
                "data_quality_warnings": warnings,
                "explanation": explanation, "decision_support_only": True,
            }
            event = KavachEvent(
                event_id=uuid4(), timestamp=datetime.now(timezone.utc), source_module=SourceModule.EDGE_AI,
                event_type="sensor_anomaly" if anomaly else "sensor_observation", severity=severity,
                security_risk=0.0, situation_risk=score, network_health=100.0,
                priority=priority, recommended_action=action, evidence=evidence, confidence=confidence,
                simulation=bool(reading.get("simulation", False)),
            )
            self.processed += 1
            summary = {"sensor_id": sensor_id, "timestamp": stamp, "anomaly": anomaly,
                "anomaly_score": score, "score_semantics": "bounded operational finding score; not a probability",
                "detection_methods": methods, "triggered_rules": [x["rule_id"] for x in rules],
                "features": features, "explanation": explanation, "model_name": self.detector.name,
                "model_version": self.detector.version, "data_quality_warnings": warnings}
            return event, summary

    def analyze(self, reading: dict) -> KavachEvent:
        return self.analyze_detailed(reading)[0]

    @staticmethod
    def _explain(reading: dict, rules: list[dict], warnings: list[str], anomaly: bool) -> str:
        if rules:
            first = rules[0]
            if "threshold" in first:
                msg = f"{first['field']} was {first['observed']}, beyond configured threshold {first['threshold']} ({first['operator']})."
            else:
                msg = f"{first['field']} changed suddenly from {first['previous']} to {first['observed']}."
            if len(rules) > 1:
                msg += f" {len(rules)} rules triggered in total."
        elif anomaly:
            msg = "The configured Isolation Forest marked this observation as unusual relative to its supplied baseline."
        else:
            msg = "No configured rule or available local detector flagged this observation."
        if warnings:
            msg += " Quality context: " + ", ".join(warnings) + "."
        return msg + " Verify the sensor and local operating context before taking action."

    def model_info(self) -> dict:
        return {"name": self.detector.name, "version": self.detector.version,
                "available": self.detector.available, "reason": self.detector.unavailable_reason,
                "rule_fallback": True, "labeled_accuracy_established": False}

    def metrics(self) -> dict:
        return {"processed": self.processed, "invalid": self.invalid,
                "tracked_sensors": len(self.history), "history_capacity_per_sensor": self.history_size}
