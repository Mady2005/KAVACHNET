"""Trusted local AI4I artifact loading and supervised prediction."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .ai4i_dataset import FEATURE_COLUMNS, TARGET_COLUMN

logger = logging.getLogger(__name__)
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[3] / "data" / "models" / "ai4i_random_forest.joblib"


class AI4IModelManager:
    """Loads once from an operator-configured local path; never trains at startup."""

    def __init__(self, artifact_path: str | Path | None = None):
        configured = artifact_path or os.getenv("KAVACHNET_AI4I_MODEL_PATH") or DEFAULT_ARTIFACT
        self.artifact_path = Path(configured).expanduser().resolve()
        self.pipeline = None
        self.metadata: dict | None = None
        self.reason = "model artifact not loaded"

    @property
    def available(self) -> bool:
        return self.pipeline is not None and self.metadata is not None

    def load(self) -> bool:
        if not self.artifact_path.is_file():
            self.pipeline = None
            self.metadata = None
            self.reason = "AI4I model artifact is missing; run the documented training command"
            return False
        try:
            import joblib
            artifact = joblib.load(self.artifact_path)
            metadata = artifact["metadata"]
            pipeline = artifact["pipeline"]
            if metadata.get("artifact_schema_version") != 1:
                raise ValueError("unsupported artifact schema")
            if metadata.get("target") != TARGET_COLUMN or tuple(metadata.get("features", ())) != FEATURE_COLUMNS:
                raise ValueError("artifact feature or target schema mismatch")
            if not callable(getattr(pipeline, "predict", None)) or not callable(getattr(pipeline, "predict_proba", None)):
                raise ValueError("artifact does not contain a probability-capable classifier")
            self.pipeline, self.metadata, self.reason = pipeline, metadata, ""
            return True
        except Exception:
            logger.exception("Configured AI4I artifact could not be loaded or validated")
            self.pipeline = None
            self.metadata = None
            self.reason = "AI4I model artifact is invalid or incompatible"
            return False

    def info(self) -> dict:
        return {
            "name": "AI4I RandomForestClassifier", "version": self._version(),
            "available": self.available, "reason": self.reason or None,
            "target": TARGET_COLUMN, "features_schema_version": "ai4i-v1",
            "dataset_sha256": self.metadata.get("dataset_sha256") if self.metadata else None,
            "trained_at_utc": self.metadata.get("trained_at_utc") if self.metadata else None,
            "metrics": self.metadata.get("metrics") if self.metadata else None,
            "decision_threshold": float(self.metadata.get("decision_threshold", 0.5)) if self.metadata else None,
            "probabilities_calibrated": False,
        }

    def predict(self, request) -> dict:
        import numpy as np
        if not self.available:
            raise RuntimeError("AI4I predictive maintenance model is unavailable")
        features = np.asarray([[
            request.product_type, request.air_temperature_k, request.process_temperature_k,
            request.rotational_speed_rpm, request.torque_nm, request.tool_wear_min,
        ]], dtype=object)
        class_values = list(self.pipeline.classes_)
        probabilities = self.pipeline.predict_proba(features)[0]
        probability_by_label = {int(label): float(probability) for label, probability in zip(class_values, probabilities)}
        threshold = float(self.metadata.get("decision_threshold", 0.5))
        prediction = int(probability_by_label.get(1, 0.0) >= threshold)
        return {
            "model_status": "available", "model_name": self.info()["name"],
            "model_version": self._version(), "target": TARGET_COLUMN,
            "prediction": prediction,
            "class_probabilities": {str(label): probability for label, probability in probability_by_label.items()},
            "decision_threshold": threshold,
            "probabilities_calibrated": False, "features_schema_version": "ai4i-v1",
            "prediction_source": "ml",
        }

    def _version(self) -> str:
        if not self.metadata:
            return "unavailable"
        trained = str(self.metadata.get("trained_at_utc", "unknown"))
        digest = str(self.metadata.get("dataset_sha256", ""))[:12]
        return f"{trained}:{digest}"
