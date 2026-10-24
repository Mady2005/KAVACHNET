# Edge AI module

Implement sensor reading analysis and anomaly detection in this package. The service must satisfy `SensorAnomalyService.analyze(reading) -> KavachEvent` from `app.services.interfaces` and emit events with `source_module=EDGE_AI`.

Keep model-specific inputs and outputs inside this package unless they become a shared API contract. Coordinate changes to shared schemas, authentication, and persistence before editing shared files.
