# Architecture

```text
React SPA (Vite) -- bearer session --> FastAPI /api/v1
                                         | auth dependencies / RBAC
                                         | module services (future)
                                         | shared event + audit services
                                         v
                                   SQLAlchemy -> SQLite
                                   Alembic schema migrations
```

## Boundaries
`app/auth` handles password and token primitives; `app/api/v1` is the HTTP adapter; `app/schemas/events.py` is the cross-module contract; `app/models` owns shared persistence; `app/services/interfaces.py` defines module and transport ports. The initial decision record is persistence only: no decision algorithm is active. Module health is `not_configured` until a real service reports status.

## Extension interfaces
- `SecurityDetectionService.inspect(payload) -> KavachEvent`
- `SensorAnomalyService.analyze(reading) -> KavachEvent`
- `NetworkMonitoringService.snapshot() -> dict`
- `DecisionEngine.decide(event) -> DecisionResult` (typed policy recommendation; no active policy implementation)
- `EventPublisher.publish(event)` and `WebSocketService.broadcast(event)` are async transport contracts.

Modules should implement these interfaces, validate output as `KavachEvent`, persist via shared event persistence, and add scoped routes with backend permission dependencies and audit actions. Event score meanings and enum mappings must be agreed before module integration.

The Network Resilience simulator lives in `app/modules/network_resilience` and is mounted under `/api/v1/network`. It persists synthetic telemetry and store-and-forward messages in SQLite and writes network state events using the shared `KavachEvent` persistence adapter. The frontend polls REST; a backend WebSocket endpoint remains future work. Its receiver is local and simulated, not a remote delivery guarantee.
