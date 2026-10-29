"""Typed inputs and module-local outputs for sensor analysis."""
from datetime import datetime
from typing import Any, Literal
import json
from pydantic import field_validator, model_validator

from pydantic import BaseModel, ConfigDict, Field


class SensorReading(BaseModel):
    """A sensor observation. At least one supported measurement is required."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sensor_id: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    sensor_type: str | None = Field(default=None, max_length=64)
    temperature_c: float | None = None
    humidity_pct: float | None = None
    vibration_mm_s: float | None = None
    voltage_v: float | None = None
    battery_pct: float | None = None
    location: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reading_id: str | None = Field(default=None, max_length=128)
    simulation: bool = False

    @model_validator(mode="before")
    @classmethod
    def strict_measurement_types(cls, value):
        if isinstance(value, dict):
            for key in ("temperature_c", "humidity_pct", "vibration_mm_s", "voltage_v", "battery_pct"):
                item = value.get(key)
                if item is not None and (isinstance(item, bool) or not isinstance(item, (int, float))):
                    raise ValueError(f"{key} must be a number")
        return value

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value):
        try:
            encoded = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain JSON-compatible finite values") from exc
        if len(encoded) > 4096:
            raise ValueError("metadata must be at most 4096 characters when encoded")
        return value


class BatchReadings(BaseModel):
    readings: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class SimulationRequest(BaseModel):
    count: int = Field(default=6, ge=2, le=100)
    seed: int = 42


class AI4IPredictionRequest(BaseModel):
    """Operational features in the same units/schema as the UCI AI4I data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    product_type: Literal["L", "M", "H"]
    air_temperature_k: float
    process_temperature_k: float
    rotational_speed_rpm: float
    torque_nm: float
    tool_wear_min: float


class AI4IPredictionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model_status: Literal["available"]
    model_name: str
    model_version: str
    target: str
    prediction: Literal[0, 1]
    class_probabilities: dict[Literal["0", "1"], float]
    decision_threshold: float = Field(ge=0, le=1)
    probabilities_calibrated: bool
    features_schema_version: str
    prediction_source: Literal["ml"]


class AnalysisSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensor_id: str
    timestamp: datetime
    anomaly: bool
    anomaly_score: float = Field(ge=0, le=1)
    score_semantics: str
    detection_methods: list[str]
    triggered_rules: list[str]
    features: dict[str, float]
    explanation: str
    model_name: str
    model_version: str
    data_quality_warnings: list[str]
