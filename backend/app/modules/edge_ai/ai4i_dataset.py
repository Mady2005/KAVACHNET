"""Validation and loading of the official UCI AI4I 2020 CSV archive."""
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

DATASET_URL = "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset"
DATASET_LICENSE = "CC BY 4.0"
EXPECTED_COLUMNS = (
    "UDI", "Product ID", "Type", "Air temperature [K]", "Process temperature [K]",
    "Rotational speed [rpm]", "Torque [Nm]", "Tool wear [min]", "Machine failure",
    "TWF", "HDF", "PWF", "OSF", "RNF",
)
FEATURE_COLUMNS = (
    "Type", "Air temperature [K]", "Process temperature [K]", "Rotational speed [rpm]",
    "Torque [Nm]", "Tool wear [min]",
)
NUMERIC_FEATURES = FEATURE_COLUMNS[1:]
TARGET_COLUMN = "Machine failure"
FAILURE_MODE_COLUMNS = ("TWF", "HDF", "PWF", "OSF", "RNF")
DEFAULT_DATASET = Path(__file__).resolve().parents[4] / "data" / "raw" / "ai4i2020.zip"


@contextmanager
def open_dataset_csv(path: str | Path = DEFAULT_DATASET) -> Iterator[TextIO]:
    """Open the CSV from the official ZIP or a manually downloaded CSV file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"AI4I dataset file not found: {source}")
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            candidates = [name for name in archive.namelist() if Path(name).name.lower() == "ai4i2020.csv"]
            if len(candidates) != 1:
                raise ValueError("archive must contain exactly one ai4i2020.csv")
            with archive.open(candidates[0]) as binary:
                import io
                wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
                try:
                    yield wrapper
                finally:
                    wrapper.detach()
    else:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            yield stream


def read_records(path: str | Path = DEFAULT_DATASET) -> list[dict[str, str]]:
    with open_dataset_csv(path) as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ValueError(f"unexpected AI4I columns: {reader.fieldnames}")
        rows = list(reader)
    return rows


def validate_dataset(path: str | Path = DEFAULT_DATASET) -> dict:
    source = Path(path)
    rows = read_records(source)
    if len(rows) != 10_000:
        raise ValueError(f"expected 10000 rows, got {len(rows)}")
    if any(None in row for row in rows):
        raise ValueError("one or more rows have an unexpected number of columns")
    missing = {column: sum(not (row.get(column) or "").strip() for row in rows) for column in EXPECTED_COLUMNS}
    duplicate_rows = len(rows) - len({tuple(row[column] for column in EXPECTED_COLUMNS) for row in rows})
    duplicate_udi = len(rows) - len({row["UDI"] for row in rows})
    if any(missing.values()) or duplicate_rows or duplicate_udi:
        raise ValueError(f"dataset failed missing/duplicate checks: missing={missing}, duplicate_rows={duplicate_rows}, duplicate_udi={duplicate_udi}")

    values: dict[str, list[float]] = {name: [] for name in NUMERIC_FEATURES}
    for row in rows:
        if row["Type"] not in {"L", "M", "H"}:
            raise ValueError(f"unexpected product type {row['Type']!r}")
        for column in (*NUMERIC_FEATURES, TARGET_COLUMN, *FAILURE_MODE_COLUMNS):
            try:
                number = float(row[column])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"non-numeric value in {column}") from exc
            if not number.is_integer() and column in (TARGET_COLUMN, *FAILURE_MODE_COLUMNS):
                raise ValueError(f"non-binary label value in {column}: {row[column]}")
            if column in (TARGET_COLUMN, *FAILURE_MODE_COLUMNS) and int(number) not in (0, 1):
                raise ValueError(f"non-binary label value in {column}: {row[column]}")
            if column in values:
                values[column].append(number)
    if not rows or {int(row[TARGET_COLUMN]) for row in rows} != {0, 1}:
        raise ValueError("machine failure target must contain both binary classes")

    labels = {column: dict(sorted(Counter(row[column] for row in rows).items())) for column in (TARGET_COLUMN, *FAILURE_MODE_COLUMNS)}
    ranges = {name: {"min": min(column_values), "max": max(column_values)} for name, column_values in values.items()}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "valid": True, "path": str(source.resolve()), "archive_sha256": digest,
        "rows": len(rows), "columns": len(EXPECTED_COLUMNS), "column_names": list(EXPECTED_COLUMNS),
        "types": {"Type": ["H", "L", "M"], "numeric_features": list(NUMERIC_FEATURES), "targets": list((TARGET_COLUMN, *FAILURE_MODE_COLUMNS))},
        "missing_values": missing, "duplicate_rows": duplicate_rows, "duplicate_udi": duplicate_udi,
        "label_distribution": labels, "numeric_ranges": ranges,
        "target": TARGET_COLUMN, "excluded_identifier_columns": ["UDI", "Product ID"],
        "excluded_leakage_columns": list(FAILURE_MODE_COLUMNS),
        "source": DATASET_URL, "license": DATASET_LICENSE,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    print(json.dumps(validate_dataset(args.dataset), indent=2))


if __name__ == "__main__":
    main()
