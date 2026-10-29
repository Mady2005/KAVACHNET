"""Transparent, configurable equipment observation rules."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleConfig:
    thresholds: dict[str, tuple[float | None, float | None]] = field(default_factory=lambda: {
        "temperature_c": (None, 85.0),
        "humidity_pct": (None, 95.0),
        "vibration_mm_s": (None, 25.0),
        "voltage_v": (180.0, 260.0),
        "battery_pct": (15.0, None),
    })


def check_thresholds(reading: dict, config: RuleConfig) -> list[dict]:
    triggered = []
    for field, (low, high) in config.thresholds.items():
        value = reading.get(field)
        if value is None:
            continue
        if low is not None and value < low:
            triggered.append({"rule_id": f"{field.upper()}_BELOW_CONFIGURED_MIN", "field": field, "observed": value, "threshold": low, "operator": "<"})
        if high is not None and value > high:
            triggered.append({"rule_id": f"{field.upper()}_ABOVE_CONFIGURED_MAX", "field": field, "observed": value, "threshold": high, "operator": ">"})
    return triggered
