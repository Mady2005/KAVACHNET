"""Validation and normalization for supported sensor payloads."""
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from pydantic import ValidationError

from .schemas import SensorReading

MEASUREMENT_RANGES = {
    "temperature_c": (-80.0, 150.0),  # broad ingestion sanity limits, not operating thresholds
    "humidity_pct": (0.0, 100.0),
    "vibration_mm_s": (0.0, 1000.0),  # illustrative sensor-unit bound
    "voltage_v": (0.0, 100000.0),  # broad sanity bound; configure per installation
    "battery_pct": (0.0, 100.0),
}


class ReadingValidationError(ValueError):
    """Raised when a sensor payload is malformed or physically non-finite."""


def validate_reading(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        raise ReadingValidationError("reading must be an object")
    for name in MEASUREMENT_RANGES:
        value = raw.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ReadingValidationError(f"{name} must be a number")
    try:
        reading = SensorReading.model_validate(raw)
    except ValidationError as exc:
        raise ReadingValidationError(str(exc)) from exc
    values = reading.model_dump()
    if not any(values.get(name) is not None for name in MEASUREMENT_RANGES):
        raise ReadingValidationError("at least one supported measurement is required")
    for name, (low, high) in MEASUREMENT_RANGES.items():
        value = values.get(name)
        if value is not None:
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ReadingValidationError(f"{name} must be a finite number")
            if not low <= value <= high:
                raise ReadingValidationError(f"{name} must be within the ingestion sanity range [{low}, {high}]")
    stamp = reading.timestamp
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ReadingValidationError("timestamp must include a timezone")
    values["timestamp"] = stamp.astimezone(timezone.utc)
    warnings = [f"missing_{name}" for name in MEASUREMENT_RANGES if values.get(name) is None]
    return values, warnings
