# Phase 1 project plan

## Goal
Deliver shared identity, persistence, event contracts, operations UI, health and registry foundations so three contributors can develop independently.

## Ownership boundaries
- Member 1: Security detection service and security-specific routes under the shared API.
- Member 2: Sensor/anomaly service and edge-specific routes.
- Member 3: Network monitor, delivery/history and simulation routes.
- Shared maintainers: auth, `KavachEvent`, common persistence, audit, decision engine interface and integration policy.

## Phase 1 includes
FastAPI and React applications, role framework, users, audit, events, decisions, settings and dataset registry tables, health endpoints, initial dashboard, OpenAPI, migrations and tests.

## Deferred
Detection models, anomaly models, live telemetry, message queue, completed central decision policy, WebSocket transport, production password recovery, email delivery, deployment hardening and downloaded datasets.

## Acceptance
Local install/run works, migrations initialize schema, API auth/RBAC works, shared schema rejects invalid scores/timestamps, dashboard queries stored records and local tests pass.
