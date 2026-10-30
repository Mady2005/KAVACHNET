from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.events import KavachEvent, SourceModule


def event_payload(source_module: SourceModule, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": uuid4(),
        "timestamp": datetime.now(timezone.utc),
        "source_module": source_module,
        "event_type": "security.inspection",
        "severity": "INFO",
        "security_risk": 0.0,
        "situation_risk": 0.2,
        "network_health": 98.0,
        "priority": "P3",
        "recommended_action": "ALLOW",
    }
    payload.update(overrides)
    return payload


def test_security_events_allow_both_unassessed_metrics_to_be_null():
    event = KavachEvent(
        **event_payload(
            SourceModule.SECURITY,
            situation_risk=None,
            network_health=None,
        )
    )

    assert event.situation_risk is None
    assert event.network_health is None


@pytest.mark.parametrize(
    "source_module",
    [SourceModule.EDGE_AI, SourceModule.NETWORK, SourceModule.SYSTEM],
)
def test_non_security_events_preserve_numeric_metrics(source_module: SourceModule):
    event = KavachEvent(**event_payload(source_module))

    assert event.situation_risk == 0.2
    assert event.network_health == 98.0


@pytest.mark.parametrize(
    "source_module",
    [SourceModule.EDGE_AI, SourceModule.NETWORK, SourceModule.SYSTEM],
)
@pytest.mark.parametrize("field", ["situation_risk", "network_health"])
def test_non_security_events_reject_null_metrics(source_module: SourceModule, field: str):
    with pytest.raises(ValidationError):
        KavachEvent(**event_payload(source_module, **{field: None}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"situation_risk": None, "network_health": 98.0},
        {"situation_risk": 0.2, "network_health": None},
    ],
)
def test_security_events_reject_partially_unassessed_metrics(overrides: dict[str, object]):
    with pytest.raises(ValidationError):
        KavachEvent(**event_payload(SourceModule.SECURITY, **overrides))


@pytest.mark.parametrize("field", ["situation_risk", "network_health"])
def test_nullable_metric_fields_remain_required(field: str):
    payload = event_payload(SourceModule.SECURITY, situation_risk=None, network_health=None)
    del payload[field]

    with pytest.raises(ValidationError):
        KavachEvent(**payload)
