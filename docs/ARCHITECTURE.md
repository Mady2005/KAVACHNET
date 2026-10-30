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

The Network Resilience module lives in `app/modules/network_resilience` and is mounted under `/api/v1/network`. It preserves the simulator/store-and-forward system while recording normalized observations with distinct `SIMULATED`, `REAL_MEASUREMENT`, `PUBLIC_DATASET`, and `RIPE_ATLAS_PUBLIC` sources. Local real collection is opt-in and limited to server-configured HTTP test endpoints; imported datasets are local uploads; RIPE Atlas ingestion is an explicitly enabled, bounded import of an existing public ping measurement. The simulator's local receiver is not a remote delivery guarantee. The frontend polls REST; a backend WebSocket endpoint remains future work. See `backend/app/modules/network_resilience/README.md` for endpoint authorization boundaries, provenance, import limits, and metric definitions.
