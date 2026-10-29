"""Compare AI4I baselines on the train/validation split only; never reads test metrics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .ai4i_dataset import DEFAULT_DATASET, FEATURE_COLUMNS, TARGET_COLUMN, read_records, validate_dataset
from .train_ai4i import RANDOM_SEED


def _pipeline(classifier, *, scale_numeric: bool = False) -> Pipeline:
    numeric = StandardScaler() if scale_numeric else "passthrough"
    preprocessing = ColumnTransformer(
        [
            ("type", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0]),
            ("numeric", numeric, list(range(1, len(FEATURE_COLUMNS)))),
        ],
        remainder="drop",
    )
    return Pipeline([("preprocess", preprocessing), ("classifier", classifier)])


def _metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    predictions = (probabilities >= threshold).astype(np.int8)
    return {
        "threshold": float(threshold),
        "precision_positive": float(precision_score(labels, predictions, zero_division=0)),
        "recall_positive": float(recall_score(labels, predictions, zero_division=0)),
        "f1_positive": float(f1_score(labels, predictions, zero_division=0)),
        "average_precision_positive": float(average_precision_score(labels, probabilities)),
        "confusion_matrix_labels_0_1": confusion_matrix(labels, predictions, labels=[0, 1]).tolist(),
    }


def compare(dataset_path: Path = DEFAULT_DATASET) -> dict:
    validate_dataset(dataset_path)
    rows = read_records(dataset_path)
    features = np.asarray(
        [[row[column] if column == "Type" else float(row[column]) for column in FEATURE_COLUMNS] for row in rows],
        dtype=object,
    )
    labels = np.asarray([int(row[TARGET_COLUMN]) for row in rows], dtype=np.int8)
    indices = np.arange(len(labels))
    train_validation, _test_indices = train_test_split(
        indices, test_size=0.15, stratify=labels, random_state=RANDOM_SEED,
    )
    train_indices, validation_indices = train_test_split(
        train_validation, test_size=(0.15 / 0.85), stratify=labels[train_validation], random_state=RANDOM_SEED,
    )
    X_train, y_train = features[train_indices], labels[train_indices]
    X_validation, y_validation = features[validation_indices], labels[validation_indices]
    models = {
        "rf_balanced_subsample": _pipeline(RandomForestClassifier(
            n_estimators=200, class_weight="balanced_subsample", random_state=RANDOM_SEED, n_jobs=1,
        )),
        "rf_failure_weight_5": _pipeline(RandomForestClassifier(
            n_estimators=200, class_weight={0: 1, 1: 5}, random_state=RANDOM_SEED, n_jobs=1,
        )),
        "logistic_balanced": _pipeline(LogisticRegression(
            class_weight="balanced", max_iter=2000, random_state=RANDOM_SEED,
        ), scale_numeric=True),
        "hist_gradient_balanced": _pipeline(HistGradientBoostingClassifier(
            class_weight="balanced", max_iter=100, random_state=RANDOM_SEED,
        )),
    }
    results = {}
    for name, model in models.items():
        model.fit(X_train, y_train)
        probabilities = model.predict_proba(X_validation)[:, list(model.classes_).index(1)]
        results[name] = _metrics(y_validation, probabilities, 0.5)
        if name == "rf_balanced_subsample":
            precision, recall, thresholds = precision_recall_curve(y_validation, probabilities)
            f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
            best = int(np.argmax(f1))
            results["rf_validation_threshold_sweep"] = {
                "candidate_count": int(len(thresholds)),
                "selected_by": "maximum positive-class F1 on validation data",
                "selected": _metrics(y_validation, probabilities, float(thresholds[best])),
            }
    return {
        "dataset": str(dataset_path.resolve()), "seed": RANDOM_SEED,
        "split": {"train": len(train_indices), "validation": len(validation_indices), "test_reserved": len(_test_indices)},
        "validation_support": {"no_failure": int(np.sum(y_validation == 0)), "failure": int(np.sum(y_validation == 1))},
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    print(json.dumps(compare(args.dataset), indent=2))


if __name__ == "__main__":
    main()
