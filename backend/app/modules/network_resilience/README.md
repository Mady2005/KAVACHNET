# Network Resilience module

Implement network health monitoring and resilience observations in this package. The service must satisfy `NetworkMonitoringService.snapshot() -> dict` from `app.services.interfaces`; events emitted by this module must use `source_module=NETWORK`.

Keep queueing, forwarding, and recovery actions behind explicit authorization and policy checks. Coordinate shared API, schema, authentication, and persistence changes before editing shared files.
