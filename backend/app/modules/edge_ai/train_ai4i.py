"""Train and evaluate a reproducible AI4I machine-failure baseline."""
from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, average_precision_score, classification_report, confusion_matrix,
    f1_score, precision_score, recall_score, precision_recall_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .ai4i_dataset import (
    DATASET_LICENSE, DATASET_URL, DEFAULT_DATASET, FEATURE_COLUMNS, NUMERIC_FEATURES,
    TARGET_COLUMN, read_records, validate_dataset,
)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "data" / "models" / "ai4i_random_forest.joblib"
RANDOM_SEED = 42


def train(dataset_path: Path = DEFAULT_DATASET, model_path: Path = DEFAULT_MODEL_PATH) -> dict:
    validation = validate_dataset(dataset_path)
    rows = read_records(dataset_path)
    features = np.asarray([
        [row[column] if column == "Type" else float(row[column]) for column in FEATURE_COLUMNS]
        for row in rows
    ], dtype=object)
    labels = np.asarray([int(row[TARGET_COLUMN]) for row in rows], dtype=np.int8)
    all_indices = np.arange(len(rows))
    train_validation, test_indices = train_test_split(
        all_indices, test_size=0.15, stratify=labels, random_state=RANDOM_SEED,
    )
    train_indices, validation_indices = train_test_split(
        train_validation, test_size=(0.15 / 0.85),
        stratify=labels[train_validation], random_state=RANDOM_SEED,
    )
    X_train = features[train_indices]
    y_train = labels[train_indices]
    X_test = features[test_indices]
    y_test = labels[test_indices]
    X_validation = features[validation_indices]
    y_validation = labels[validation_indices]

    preprocessing = ColumnTransformer(
        transformers=[
            ("type", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0]),
            ("numeric", "passthrough", list(range(1, len(FEATURE_COLUMNS)))),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
    model = Pipeline([
        ("preprocess", preprocessing),
        ("classifier", RandomForestClassifier(
            n_estimators=200, class_weight="balanced_subsample", random_state=RANDOM_SEED,
            n_jobs=1, min_samples_leaf=1,
        )),
    ])
    # The test set is not used in fitting, preprocessing, or model selection.
    model.fit(X_train, y_train)
    positive_index = list(model.classes_).index(1)
    validation_probabilities = model.predict_proba(X_validation)[:, positive_index]
    validation_precision, validation_recall, thresholds = precision_recall_curve(y_validation, validation_probabilities)
    validation_f1 = 2 * validation_precision[:-1] * validation_recall[:-1] / np.maximum(validation_precision[:-1] + validation_recall[:-1], 1e-12)
    selected_index = int(np.argmax(validation_f1))
    decision_threshold = float(thresholds[selected_index])
    test_probabilities = model.predict_proba(X_test)[:, positive_index]

    def evaluate(threshold: float) -> dict:
        # sklearn's classifier predicts the first class on an exact 0.5 tie.
        predictions = (test_probabilities > threshold if threshold == 0.5 else test_probabilities >= threshold).astype(np.int8)
        return {
            "decision_threshold": float(threshold),
            "precision_positive": float(precision_score(y_test, predictions, pos_label=1, zero_division=0)),
            "recall_positive": float(recall_score(y_test, predictions, pos_label=1, zero_division=0)),
            "f1_positive": float(f1_score(y_test, predictions, pos_label=1, zero_division=0)),
            "average_precision_positive": float(average_precision_score(y_test, test_probabilities)),
            "accuracy_secondary": float(accuracy_score(y_test, predictions)),
            "confusion_matrix_labels_0_1": confusion_matrix(y_test, predictions, labels=[0, 1]).tolist(),
            "class_report": classification_report(y_test, predictions, labels=[0, 1], target_names=["no_failure", "failure"], output_dict=True, zero_division=0),
            "support": {"no_failure": int(np.sum(y_test == 0)), "failure": int(np.sum(y_test == 1))},
        }

    baseline_metrics = evaluate(0.5)
    metrics = evaluate(decision_threshold)
    metadata = {
        "artifact_schema_version": 1,
        "model_name": "AI4I RandomForestClassifier",
        "target": TARGET_COLUMN,
        "target_definition": "UCI Machine failure binary label; individual subtype labels are excluded from features",
        "features": list(FEATURE_COLUMNS),
        "excluded_identifiers": ["UDI", "Product ID"],
        "excluded_leakage_columns": ["TWF", "HDF", "PWF", "OSF", "RNF"],
        "dataset_source": DATASET_URL,
        "dataset_license": DATASET_LICENSE,
        "dataset_sha256": validation["archive_sha256"],
        "dataset_rows": len(rows),
        "split": {"train": len(train_indices), "validation": len(validation_indices), "test": len(test_indices), "strategy": "stratified 70/15/15; fixed seed 42"},
        "validation_split_used_for_tuning": True,
        "threshold_selection": "maximize positive-class F1 on validation split; test set not used for threshold selection",
        "decision_threshold": decision_threshold,
        "configuration": {"n_estimators": 200, "class_weight": "balanced_subsample", "random_state": RANDOM_SEED, "n_jobs": 1},
        "metrics": metrics,
        "baseline_metrics_threshold_0_5": baseline_metrics,
        "validation_metrics_at_selected_threshold": {
            "precision_positive": float(validation_precision[selected_index]),
            "recall_positive": float(validation_recall[selected_index]),
            "f1_positive": float(validation_f1[selected_index]),
            "average_precision_positive": float(average_precision_score(y_validation, validation_probabilities)),
            "support": {"no_failure": int(np.sum(y_validation == 0)), "failure": int(np.sum(y_validation == 1))},
        },
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "scikit_learn_version": sklearn.__version__,
        "numpy_version": np.__version__,
    }
    artifact = {"metadata": metadata, "pipeline": model}
    output = Path(model_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output, compress=3)
    return {"artifact_path": str(output), "metadata": metadata}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    args = parser.parse_args()
    print(json.dumps(train(args.dataset, args.model_path), indent=2))


if __name__ == "__main__":
    main()
