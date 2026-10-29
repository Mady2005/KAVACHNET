# Edge AI module

Member 2 sensor analysis validates supported readings and constructs canonical `KavachEvent` records with `source_module=EDGE_AI`, rule findings, severity, recommended action, and evidence.

## Persisted analysis and incidents

Authenticated `POST /api/v1/edge-ai/analyze` analyzes and persists a sensor observation and its canonical event, then correlates an eligible event into a persisted incident. `GET /api/v1/edge-ai/history` reads stored observations and results. Exact observation replays return the saved result.

The shared authenticated `POST /api/v1/events` endpoint stores canonical event records and supports event-ID replay. Incident routes are `GET /api/v1/incidents`, `GET /api/v1/incidents/{incident_id}`, and `PATCH /api/v1/incidents/{incident_id}/status`. Incident detail includes linked event evidence, observation measurements, and status history. Analysis and incident routes require `ADMIN` or `EDGE_OPERATOR`; the event API also enforces source-module role permissions.

Correlation is a development demonstration policy: it groups eligible `EDGE_AI` `sensor_anomaly` events by sensor and exact rule category within a configurable 900-second default span. Only active incidents accept additional events; resolved incidents are terminal. These thresholds and categories are not validated for operational equipment.

See `docs/member2-implementation-and-demo-guide.md` for the implementation map, PowerShell setup, browser demo steps, verified commands and results, and limitations.

## Optional predictive model

AI4I model inference is optional. The application loads a locally configured trained model artifact when available; it does not train or download model files at startup. With no local artifact, the model-info route reports it unavailable and prediction returns 503, while sensor rule analysis remains available. Install `backend/requirements-edge-ai-ml.txt` and use `train_ai4i.py` with an authorized dataset to prepare the optional model locally.

## Readiness limits

Only local SQLite development behavior has been verified. PostgreSQL/shared-database concurrency, deployment configuration, operational thresholds, and production readiness remain unverified. Member 1 and Member 3 feeds are not included in the current correlation policy.
