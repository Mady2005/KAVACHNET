"""NetworkMonitoringService adapter backed by persisted simulator telemetry."""
from app.db.session import SessionLocal
from app.modules.network_resilience.simulator import latest_telemetry

class NetworkMonitor:
    def __init__(self,session_factory=SessionLocal):
        self._session_factory=session_factory

    def snapshot(self)->dict:
        with self._session_factory() as db:
            record=latest_telemetry(db)
            if record is None:
                return {"initialized":False,"telemetry":None}
            return {"initialized":True,"telemetry":{"scenario":record.scenario,"state":record.state,"timestamp":record.timestamp.isoformat(),"latency_ms":record.latency_ms,"packet_loss_pct":record.packet_loss_pct,"bandwidth_mbps":record.bandwidth_mbps,"connected":record.connected,"jitter_ms":record.jitter_ms,"delivery_failure_rate_pct":record.delivery_failure_rate_pct,"health_score":record.health_score,"reason":record.reason,"simulated":True}}
