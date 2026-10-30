"""Bounded CSV/JSON import into normalized, provenance-aware observations."""
import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.models import DatasetRegistry, NetworkObservationRecord

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
UNAVAILABLE_OPERATIONAL = ["round_trip_latency", "jitter", "packet_loss", "throughput", "application_delivery_success", "application_ack_latency", "endpoint_availability"]
_SAFE_METRIC = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.:-]{0,79}$")
_MAPPING_KEYS = {"measurement_type","value_column","timestamp_column","unit"}
_EXPECTED_UNITS = {
    "round_trip_latency":"ms", "jitter":"ms", "application_ack_latency":"ms",
    "packet_loss":"percent", "endpoint_availability":"percent", "application_delivery_success":"percent",
    "throughput":"Mbps", "measurement_count":"probes", "measurement_coverage":"percent",
}


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp_utc must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp_utc must be a valid ISO 8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_utc must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _row_values(row, mapping):
    if not isinstance(row, dict):
        raise ValueError("row must be an object")
    if mapping:
        measurement_type = mapping.get("measurement_type")
        value_column = mapping.get("value_column")
        timestamp_column = mapping.get("timestamp_column")
        value = row.get(value_column) if value_column else None
        timestamp = row.get(timestamp_column) if timestamp_column else row.get("timestamp_utc")
        unit = mapping.get("unit") or row.get("unit")
    else:
        measurement_type, value, timestamp, unit = (row.get("measurement_type"), row.get("value"), row.get("timestamp_utc"), row.get("unit"))
    if not isinstance(measurement_type, str) or not _SAFE_METRIC.fullmatch(measurement_type):
        raise ValueError("measurement_type is missing or invalid")
    if not isinstance(unit, str) or not unit.strip() or len(unit) > 32:
        raise ValueError("unit is missing or invalid")
    if value is None or str(value).strip() == "":
        numeric = None
    else:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError("value must be numeric or null") from None
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("value must be finite and non-negative")
    metric = measurement_type.lower()
    expected_unit=_EXPECTED_UNITS.get(metric)
    if expected_unit and unit.strip().lower()!=expected_unit.lower():
        raise ValueError(f"{measurement_type} requires unit {expected_unit}")
    if metric in {"packet_loss", "endpoint_availability", "application_delivery_success", "delivery_success"} and numeric is not None and numeric > 100:
        raise ValueError(f"{measurement_type} must be between 0 and 100")
    return {
        "timestamp_utc": _timestamp(timestamp),
        "measurement_type": measurement_type,
        "value": numeric,
        "unit": unit.strip(),
        "target_label": row.get("target_label"),
        "measurement_method": row.get("measurement_method") or "user supplied dataset mapping",
        "collection_interval_seconds": row.get("collection_interval_seconds"),
        "experiment_id": row.get("experiment_id"),
        "metadata": {key: value for key,value in (row.get("metadata") or {}).items() if key in {"measurement_count","measurement_coverage","elapsed_seconds","bytes_transferred","probe_count"} and isinstance(value,(int,float)) and math.isfinite(value)} if isinstance(row.get("metadata"),dict) else {},
    }


def import_dataset(db, content: bytes, filename: str, name: str, source_url: str|None, license_name: str|None, dataset_version: str|None, mapping: dict|None):
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError(f"File exceeds the {MAX_IMPORT_BYTES} byte import limit")
    checksum = hashlib.sha256(content).hexdigest()
    if source_url:
        parsed_url=urlsplit(source_url)
        if parsed_url.scheme not in {"http","https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.query:
            raise ValueError("source_url must be an http(s) provenance URL without credentials or query parameters")
        try: parsed_url.port
        except ValueError: raise ValueError("source_url contains an invalid port") from None
    if mapping is not None:
        if not isinstance(mapping,dict) or set(mapping)-_MAPPING_KEYS:
            raise ValueError("mapping may contain only measurement_type, value_column, timestamp_column, and unit")
        if any(not isinstance(value,str) or len(value)>120 for value in mapping.values()):
            raise ValueError("mapping values must be strings of at most 120 characters")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if suffix == "csv":
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
            if not reader.fieldnames:
                raise ValueError("CSV must include a header row")
            raw_rows = reader
        elif suffix == "json":
            decoded = json.loads(content)
            raw_rows = decoded if isinstance(decoded, list) else decoded.get("observations") if isinstance(decoded, dict) else None
            if not isinstance(raw_rows, list):
                raise ValueError("JSON must be a list of observations or an object with an observations list")
        else:
            raise ValueError("Only CSV and JSON files are supported")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to parse import file: {exc}") from None
    records=[]; rejected=[]; supported=set(); count=0
    for index, raw in enumerate(raw_rows, start=2 if suffix == "csv" else 1):
        count += 1
        if count > MAX_IMPORT_ROWS:
            raise ValueError(f"Import exceeds the {MAX_IMPORT_ROWS} row limit")
        try:
            values = _row_values(raw, mapping)
            if values["target_label"] is not None and (not isinstance(values["target_label"], str) or len(values["target_label"]) > 120):
                raise ValueError("target_label must be at most 120 characters")
            if values["collection_interval_seconds"] is not None:
                try: values["collection_interval_seconds"] = float(values["collection_interval_seconds"])
                except (TypeError,ValueError): raise ValueError("collection_interval_seconds must be numeric") from None
                if not math.isfinite(values["collection_interval_seconds"]) or not 0 <= values["collection_interval_seconds"] <= 86400:
                    raise ValueError("collection_interval_seconds must be between 0 and 86400")
            if values["target_label"] is not None and any(ord(char)<32 for char in values["target_label"]):
                raise ValueError("target_label cannot contain control characters")
            if len(values["measurement_method"])>200:
                raise ValueError("measurement_method must be at most 200 characters")
            records.append(values); supported.add(values["measurement_type"])
        except (ValueError, TypeError, OverflowError) as exc:
            rejected.append({"row": index, "error": str(exc)})
    unsupported=[metric for metric in UNAVAILABLE_OPERATIONAL if metric not in supported]
    dataset = DatasetRegistry(name=name[:200], owning_member="Member 3", source_url=source_url, license=license_name, description="User-supplied telemetry dataset; not downloaded by KAVACHNET", provenance={"filename": filename.rsplit("/",1)[-1].rsplit("\\",1)[-1][:255], "imported_at": datetime.now(timezone.utc).isoformat(), "sha256": checksum, "mapping": mapping or {}, "imported_rows": count, "accepted_rows": len(records), "rejected_rows": len(rejected), "rejected": rejected[:500], "unsupported_operational_metrics": unsupported}, import_status="IMPORTED_WITH_REJECTIONS" if rejected else "IMPORTED", schema_metadata={"dataset_version":dataset_version,"fields":sorted(supported)}, checksum=checksum)
    db.add(dataset); db.flush()
    for row in records:
        db.add(NetworkObservationRecord(timestamp_utc=row["timestamp_utc"],source_type="PUBLIC_DATASET",source_name=name[:200],measurement_type=row["measurement_type"],value=row["value"],unit=row["unit"],target_label=row["target_label"],measurement_method=row["measurement_method"][:200],collection_interval_seconds=row["collection_interval_seconds"],dataset_version=dataset_version,dataset_id=dataset.dataset_id,experiment_id=row["experiment_id"],observation_metadata=row["metadata"]))
    db.commit(); db.refresh(dataset)
    return {"dataset_id":dataset.dataset_id,"name":dataset.name,"source_url":source_url,"license":license_name,"import_status":dataset.import_status,"imported_rows":count,"accepted_rows":len(records),"rejected_rows":len(rejected),"rejected":rejected[:500],"supported_fields":sorted(supported),"unsupported_operational_metrics":unsupported,"sha256":checksum}
