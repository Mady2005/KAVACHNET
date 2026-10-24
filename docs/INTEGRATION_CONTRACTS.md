# KAVACHNET module integration contracts

This document describes the shared backend boundaries for parallel module work. The Security, Edge AI, and Network Resilience algorithms and their ingest APIs are not implemented by this contract.

## Shared event

`backend/app/schemas/events.py::KavachEvent` is the canonical event payload. It requires a UUID `event_id`, timezone-aware `timestamp` normalized to UTC, `source_module`, `event_type` (1–100 characters), `severity`, `security_risk` and `situation_risk` in `[0, 1]`, `network_health` in `[0, 100]`, `priority`, and `recommended_action`. `evidence` defaults to `{}`, `confidence` is optional in `[0, 1]`, `status` defaults to `NEW`, `correlation_id` is optional (max 100), and `simulation` defaults to false. Unknown fields are rejected and non-finite numeric values are invalid.

Enums:

- Source: `SECURITY`, `EDGE_AI`, `NETWORK`, `SYSTEM`
- Severity: `INFO`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
- Priority: `P0`, `P1`, `P2`, `P3`
- Action: `ALLOW`, `WARN`, `SANITIZE`, `BLOCK`, `QUARANTINE`, `ALERT`, `SUMMARIZE`, `COMPRESS`, `PRIORITIZE`, `STORE`, `FORWARD`
- Status: `NEW`, `ACKNOWLEDGED`, `QUEUED`, `DELIVERED`, `BLOCKED`, `RESOLVED`

The API validates event payloads, but the database currently stores enum fields as strings and has no equivalent check constraints. Priority assignment, score semantics, and status-transition rules remain shared-contract decisions and must be agreed before modules rely on them.

## Current service protocols

Implementations belong in module-owned services and must satisfy `backend/app/services/interfaces.py`:

| Protocol | Required interface |
|---|---|
| Security detection | `inspect(payload: dict) -> KavachEvent` |
| Edge sensor anomaly | `analyze(reading: dict) -> KavachEvent` |
| Network monitoring | `snapshot() -> dict` |
| Decision engine | `decide(event: KavachEvent) -> DecisionResult` |
| Event publisher | `async publish(event: KavachEvent) -> None` |
| WebSocket service | `async broadcast(event: KavachEvent) -> None` |

`DecisionResult` carries `selected_action`, `policy_rule_id`, `explanation`, `input_scores`, and a UTC `timestamp`. It records a recommendation; it does not authorize or execute external actions. Policy implementations must not bypass the responsible module's authorization and safety checks.

## Ownership and future API work

| Owner | Owns | Current seam | API still needed (not implemented) |
|---|---|---|---|
| Security | detection, sanitization/blocking decisions, security evidence | `SecurityDetectionService.inspect` | authenticated security inspection/ingest and security event history |
| Edge AI | sensor validation, anomaly detection, model metadata | `SensorAnomalyService.analyze` | sensor ingest, anomaly history, model/version reporting |
| Network Resilience | network health, queue/delivery observations, resilience simulation | `NetworkMonitoringService.snapshot` | health snapshots, queue/delivery state, simulation controls |
| Platform | shared schemas, auth, event persistence, policy interface, audit | `DecisionEngine`, `EventPublisher`, `WebSocketService` | event status transition/acknowledgment, decision creation/preview, authenticated WebSocket endpoint |

The current `POST /api/v1/events` is the shared event write endpoint. It persists the event and then audits it. Module integrations should use this common event shape and must not write event rows directly. Publishing is not wired to persistence: any future publisher should run after commit, and durable delivery requires an outbox or equivalent. The frontend WebSocket helper currently has no matching backend endpoint or reconnect/delivery contract.

## API access boundary

The shared event write route now binds `SECURITY_ANALYST` to `SECURITY`, `EDGE_OPERATOR` to `EDGE_AI`, and `NETWORK_OPERATOR` to `NETWORK`; `ADMIN` may submit any source, including `SYSTEM`. A module must not impersonate another module by selecting a different `source_module`. Existing event/decision/dataset read routes remain globally readable to authenticated users for compatibility; module-scoped read access is a separate authorization change.

## Integration sequence

1. Agree on score meanings, priority mapping, allowed status transitions, and module role mapping.
2. Keep module code in owner-specific service/package areas; shared schema, auth, database, and API changes require coordinated review.
3. Add explicit migrations and contract tests before changing stored event fields or enum constraints.
4. Centralize event persistence, publish only after commit, and define delivery/retry behavior before enabling live frontend updates.
5. Add module-specific routes only with typed request/response schemas and role checks; keep module algorithms behind the protocols above.
