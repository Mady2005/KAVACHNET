"""Reproducible synthetic sensor readings for local demonstrations."""
import random
from datetime import datetime, timedelta, timezone


def generate_readings(count: int = 6, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({"sensor_id": "demo-temperature-01", "sensor_type": "temperature",
            "timestamp": start + timedelta(minutes=index), "temperature_c": round(22 + rng.uniform(-1, 1), 2),
            "humidity_pct": round(45 + rng.uniform(-3, 3), 2), "battery_pct": 88,
            "reading_id": f"demo-{seed}-{index}", "simulation": True})
    if count >= 2:
        rows[-1]["temperature_c"] = 96.0
    return rows
