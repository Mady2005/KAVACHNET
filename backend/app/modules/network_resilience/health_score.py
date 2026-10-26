"""Deterministic scoring for synthetic network telemetry."""
from dataclasses import dataclass
from app.core.config import settings
from app.schemas.network import NetworkState, TelemetryInput

@dataclass(frozen=True)
class HealthScore:
    state: NetworkState
    score: float
    latency_score: float
    delivery_score: float
    bandwidth_score: float

def score_telemetry(telemetry:TelemetryInput)->HealthScore:
    latency_score=max(0.0,1.0-telemetry.latency_ms/settings.network_latency_reference_ms)
    delivery_score=1.0-telemetry.packet_loss_pct/100.0
    bandwidth_score=min(1.0,telemetry.bandwidth_mbps/settings.network_bandwidth_reference_mbps)
    weighted=(settings.network_health_latency_weight*latency_score+
              settings.network_health_delivery_weight*delivery_score+
              settings.network_health_bandwidth_weight*bandwidth_score)
    score=0.0 if not telemetry.connected else round(max(0.0,min(100.0,100.0*weighted)),2)
    state=NetworkState.DISCONNECTED if not telemetry.connected else NetworkState.HEALTHY if score>=settings.network_healthy_threshold else NetworkState.DEGRADED
    return HealthScore(state,score,round(latency_score,4),round(delivery_score,4),round(bandwidth_score,4))
