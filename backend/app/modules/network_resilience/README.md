# Network Resilience module

The module uses **synthetic telemetry only**. Scenario controls do not inspect or change the host's interfaces, routing, or firewall. Its local delivery adapter decompresses and validates payloads, then returns a simulated acknowledgment; it is not remote or guaranteed delivery.

## Implementation map

- `health_score.py`: pure, deterministic 0–100 score.
- `simulator.py`: healthy, degraded, disconnected, recovery, and validated custom telemetry; persists history and emits shared events on meaningful state transitions.
- `communication_policy.py`: explicit structured handling recommendation.
- `queue_service.py`: durable SQLite store-and-forward, idempotency, gzip-if-smaller, retry/expiry, and simulated receiver.
- `monitor.py`: `NetworkMonitoringService.snapshot()` adapter.
- `backend/app/api/v1/network.py`: authenticated REST API mounted by the existing FastAPI application.

## Health scoring

`health = 100 × (0.35 × latency_score + 0.40 × delivery_score + 0.25 × bandwidth_score)`, clamped to `[0, 100]`. `latency_score = clamp(1 - latency_ms / reference_ms, 0, 1)`, `delivery_score = 1 - packet_loss_pct / 100`, and `bandwidth_score = clamp(bandwidth_mbps / reference_mbps, 0, 1)`. A disconnected simulator always scores zero. Connected scores below the configurable threshold (70 by default) are `degraded`; scores at or above it are `healthy`. Weights must sum to 1. These are demonstration metrics, not a networking standard.

## Queue and delivery behavior

Messages transition `PENDING → IN_FLIGHT → DELIVERED`, or `IN_FLIGHT → RETRY_WAIT → IN_FLIGHT`, and terminal `FAILED`/`EXPIRED`. Manual retry permits `FAILED → PENDING` and starts a new retry cycle while retaining attempt history. Messages expire for delivery after the configured TTL; rows are retained for inspection and are not automatically purged. A request-triggered processor claims rows with a conditional database update, sorting strictly `P0`, `P1`, `P2`, `P3`, then creation time and ID. Sustained high-priority traffic can starve `P3`. No uncontrolled background worker is started.

The application priority meanings follow the shared event enum: `P0` critical, `P1` high, `P2` normal, and `P3` low. Priority affects eligibility order only; it cannot guarantee delivery.

Retries use capped exponential backoff with deterministic ±10% jitter. A disconnected channel defers attempts. Recovery makes retry-wait messages eligible immediately. A single worker task polls at the configured interval, handles up to 20 messages per pass, and is started/stopped by the FastAPI lifespan. Run one Uvicorn worker for this MVP; multi-process worker deployment is not claimed. SQLite persistence survives application restarts. Conditional updates prevent duplicate claims in the supported SQLite execution model. An idempotency key prevents duplicate enqueues; a conflicting payload under the same key returns 409.

JSON payloads are capped at 64 KiB by default. gzip is stored only if it makes serialized data smaller. Metrics report stored/original ratio and size reduction. The receiver decompresses and verifies the checksum before acknowledging. Payload contents are excluded from list/detail APIs and audit details. Expiry marks messages `EXPIRED`; it does not physically delete records.

## API

Routes are under `/api/v1/network`. All reads require authentication; simulator changes, enqueue, and manual retry require `NETWORK_OPERATOR` or `ADMIN`.

| Method and route | Purpose |
|---|---|
| `GET /status` | Latest synthetic state and queue counts; telemetry is null before initialization |
| `GET /history` | Paginated persisted telemetry |
| `POST /simulator/scenario` | Apply `healthy`, `degraded`, `disconnected`, or `recovery` |
| `POST /simulator/telemetry` | Set range-validated synthetic telemetry |
| `POST /simulator/reset` | Reset to healthy defaults |
| `GET /queue` | Counts by state and active priority |
| `POST /messages` | Enqueue a JSON message with priority and optional idempotency key (201 new, 200 matching duplicate) |
| `GET /messages` | Paginated messages, optionally filtered by state and priority |
| `GET /messages/{message_id}` | Message metadata and attempt history (no payload) |
| `POST /messages/{message_id}/retry` | Manually retry a failed message |
| `GET /metrics` | Compression, attempts, simulated latency, and terminal-message success rate |
| `GET /deliveries` | Paginated attempt outcomes |

## Configuration and limitations

Settings are exposed through environment variables (see `.env.example`): payload size, TTL, retry limit/backoff, worker poll interval, latency/bandwidth references, healthy threshold, and score weights. Apply migrations from `backend/` with `alembic upgrade head`. Run the API with one worker, for example `uvicorn app.main:app --reload`. Run backend tests there with `python -m pytest -q`; run frontend tests/build from `frontend/` with `npm test` and `npm run build`.

The backend has no WebSocket endpoint; the frontend polls REST every five seconds. No actual network performance is measured, and local simulated acknowledgment is not a remote delivery guarantee.
