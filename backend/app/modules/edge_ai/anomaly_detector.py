"""Optional local Isolation Forest adapter; no artifact downloads occur."""
from __future__ import annotations

from math import isfinite
from typing import Any

FEATURES = ("temperature_c", "humidity_pct", "vibration_mm_s", "voltage_v", "battery_pct")


def extract_features(reading: dict[str, Any]) -> dict[str, float]:
    """Return available finite features; missing features stay missing."""
    return {key: float(reading[key]) for key in FEATURES if reading.get(key) is not None}


class IsolationForestDetector:
    """Model is opt-in and must be fit with caller supplied representative data."""

    name = "isolation_forest"
    version = "sklearn-local-v1"

    def __init__(self, contamination: float = 0.05, random_state: int = 42):
        if not 0.0 < contamination < 0.5:
            raise ValueError("contamination must be between 0 and 0.5")
        self.contamination = contamination
        self.random_state = random_state
        self.model = None
        self.columns: tuple[str, ...] = ()
        self.unavailable_reason: str | None = "model not fitted; rule-based analysis remains available"

    @property
    def available(self) -> bool:
        return self.model is not None

    def fit(self, readings: list[dict[str, Any]]) -> bool:
        if len(readings) < 10:
            self.unavailable_reason = "at least 10 supplied baseline readings are required"
            return False
        for row in readings:
            for key in FEATURES:
                value = row.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)):
                    self.unavailable_reason = f"baseline contains an invalid {key} feature"
                    return False
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            self.unavailable_reason = "scikit-learn is not installed; rule-based analysis remains available"
            return False
        columns = tuple(key for key in FEATURES if sum(row.get(key) is not None for row in readings) == len(readings))
        if not columns:
            self.unavailable_reason = "baseline needs at least one measurement present in every row"
            return False
        matrix = [[float(row[key]) for key in columns] for row in readings]
        model = IsolationForest(contamination=self.contamination, random_state=self.random_state)
        model.fit(matrix)
        self.model, self.columns, self.unavailable_reason = model, columns, None
        return True

    def score(self, features: dict[str, float]) -> tuple[float | None, bool | None, float | None]:
        if not self.available:
            return None, None, None
        if not all(key in features for key in self.columns):
            return None, None, None
        row = [[features[key] for key in self.columns]]
        raw_score = float(self.model.decision_function(row)[0])
        # Monotonic bounded display score; not a probability or confidence.
        normalized = 1.0 / (1.0 + max(-50.0, min(50.0, raw_score)))
        return max(0.0, min(1.0, normalized)), bool(self.model.predict(row)[0] == -1), raw_score
