# Network Resilience module

The module keeps **simulated**, **real-measurement**, and **public-dataset** observations in distinct source classes. The original simulator and durable store-and-forward behavior remain available. Scenario controls do not inspect or change the host's interfaces, routing, or firewall. The simulator's local delivery adapter decompresses and validates payloads, then returns a simulated acknowledgment; it is not remote or guaranteed delivery.

## Implementation map

- `health_score.py`: pure, deterministic 0–100 score.
- `simulator.py`: healthy, degraded, disconnected, recovery, and validated custom telemetry; persists history and emits shared events on meaningful state transitions.
- `communication_policy.py`: explicit structured handling recommendation.
- `queue_service.py`: durable SQLite store-and-forward, idempotency, gzip-if-smaller, retry/expiry, and simulated receiver.
- `monitor.py`: `NetworkMonitoringService.snapshot()` adapter.
- `backend/app/api/v1/network.py`: authenticated REST API mounted by the existing FastAPI application.
- `measurements.py`: opt-in bounded HTTP latency/availability collection, optional test-endpoint acknowledgment and response-throughput measurements.
- `dataset_import.py`: local CSV/JSON validation, row rejection reporting, SHA-256 and provenance preservation.
- `backend/migrations/versions/0003_network_provenance.py`: normalized per-observation provenance and measurement-run records.

## Health scoring

`health = 100 × (0.35 × latency_score + 0.40 × delivery_score + 0.25 × bandwidth_score)`, clamped to `[0, 100]`. `latency_score = clamp(1 - latency_ms / reference_ms, 0, 1)`, `delivery_score = 1 - packet_loss_pct / 100`, and `bandwidth_score = clamp(bandwidth_mbps / reference_mbps, 0, 1)`. A disconnected simulator always scores zero. Connected scores below the configurable threshold (70 by default) are `degraded`; scores at or above it are `healthy`. Weights must sum to 1. These are demonstration metrics, not a networking standard.

## Queue and delivery behavior

Messages transition `PENDING → IN_FLIGHT → DELIVERED`, or `IN_FLIGHT → RETRY_WAIT → IN_FLIGHT`, and terminal `FAILED`/`EXPIRED`. Manual retry permits `FAILED → PENDING` and starts a new retry cycle while retaining attempt history. Messages expire for delivery after the configured TTL; rows are retained for inspection and are not automatically purged. A request-triggered processor and one lifecycle-managed worker claim rows with conditional database updates, sorting strictly `P0`, `P1`, `P2`, `P3`, then creation time and ID. Sustained high-priority traffic can starve `P3`.

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

The backend has no WebSocket endpoint; the frontend polls REST every five seconds. Real collection is disabled unless explicitly configured; this task verified the adapter with mocked/local test behavior and did not collect data from an external operational endpoint. Local simulated acknowledgment is not a remote delivery guarantee.

## Telemetry sources and provenance

`network_observations` stores one metric observation per row. Every row has `source_type`, timestamp, source name, metric and unit, method, optional target label, collection interval, dataset/version, experiment ID, dataset foreign key, and restricted metadata. A missing metric is stored with `value=null`; it is never treated as zero. The older composite `network_telemetry` table remains for compatibility and is marked `source_type=SIMULATED`; every simulator update also writes normalized `SIMULATED` observations.

Source values are `SIMULATED`, `REAL_MEASUREMENT`, and `PUBLIC_DATASET`. Source filters are available on `GET /api/v1/network/observations`. `GET /api/v1/network/evaluation?source_type=...` summarizes only the selected source, with median over non-null observations and nearest-rank p95 when at least 20 values are available. It returns `PENDING_DATA_COLLECTION` when that source has no observations. The current health score remains the documented synthetic formula; it is not applied to real or dataset data because a normal HTTP probe does not measure packet-level loss and may not provide every score input.

### Real collection configuration

Real collection is disabled by default. Configure only a controlled endpoint for which the operator has permission to run tests. The request cannot choose an arbitrary URL; it can only start/stop a run against server configuration. The collector uses one HTTP request at a time, disables redirects and environment proxies, bounds samples to 1–100 (20 default), has a 3 second timeout (maximum 10), and polls at least every 0.5 second. It does not scan, use ICMP, capture host traffic, or change OS networking. The endpoint URL is not returned by the configuration API or included in audit records.

```dotenv
NETWORK_MEASUREMENT_ENABLED=true
NETWORK_MEASUREMENT_TARGET_URL=http://127.0.0.1:8787/health
NETWORK_MEASUREMENT_TARGET_LABEL=local-authorized-test
NETWORK_MEASUREMENT_TIMEOUT_SECONDS=3
NETWORK_MEASUREMENT_INTERVAL_SECONDS=1
NETWORK_MEASUREMENT_MAX_SAMPLES=20
NETWORK_ACK_TEST_URL=http://127.0.0.1:8787/ack
NETWORK_THROUGHPUT_TEST_URL=http://127.0.0.1:8787/throughput
NETWORK_THROUGHPUT_MAX_BYTES=1048576
NETWORK_THROUGHPUT_MAX_SECONDS=5
```

An HTTP GET to the configured probe endpoint measures application-level round-trip time and endpoint availability. Probe failures are categorized as timeout, connection, HTTP, or generic request errors; these are not packet-loss samples. Availability is successful probes divided by all attempted probes. Jitter is the mean absolute difference between consecutive successful HTTP round-trip times and is unavailable until two samples succeed. The optional acknowledgment URL receives a small JSON test message with a unique ID; the response must echo that ID. The optional throughput URL serves a response body; throughput is bytes read up to the configured maximum times eight divided by elapsed seconds (decimal Mbps), with both byte and duration caps. These optional calls run once per probe and only against separately configured test endpoints. No credentials or tokens are accepted in endpoint URLs.

Run a loopback-only synthetic endpoint from the repository root:

```powershell
python scripts/network_test_endpoint.py
```

Configure the example endpoints in the root `.env`, then start the backend (single worker) and frontend in separate terminals:

```powershell
cd backend
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm run dev
```

Sign in as `NETWORK_OPERATOR` or `ADMIN` and use the Network page's **Real measurements** tab. `POST /api/v1/network/measurements/runs` accepts `{"samples": 5}` and returns a run ID. Poll `GET /api/v1/network/measurements/runs/{run_id}`; stop early with `POST /api/v1/network/measurements/runs/{run_id}/stop`. Reads require authentication; start/stop require a network operator or admin and are audit logged. One active run per application process is supported. Use a single Uvicorn worker for this MVP. A hard process exit marks active runs `INTERRUPTED` on next startup; already stored observations remain available, and queued network messages remain independent.

The collector persists latency only after successful probes; HTTP probe failure is not evidence of IP packet loss, so `packet_loss` is persisted as unavailable. Acknowledgment and throughput metrics are null when their separate endpoint is not configured. Real collection was verified with a deterministic mocked HTTP transport in tests; no external or live operational endpoint was contacted. Set up a controlled local endpoint to perform actual measurements.

An evaluation response has this structure; an empty real evaluation is intentionally marked pending rather than filled with example measurements:

```json
{
  "source_type": "REAL_MEASUREMENT",
  "experiment_id": null,
  "dataset_id": null,
  "observation_count": 0,
  "collection_window": {"started_at": null, "ended_at": null},
  "runs": [],
  "datasets": [],
  "metrics": {},
  "status": "PENDING_DATA_COLLECTION"
}
```

### Public dataset import

No large dataset is downloaded automatically. `POST /api/v1/network/datasets/import` accepts a local CSV or JSON file (10 MiB, 10,000 rows maximum), source name, optional official URL/license/version, and optional CSV mapping JSON. Canonical rows use `timestamp_utc` (ISO-8601 with timezone), `measurement_type`, numeric or null `value`, and `unit`; optional method, target label, interval, experiment ID, and a small numeric metadata whitelist are supported. Invalid rows are reported by row number and reason; rejected raw data is not stored. A mapping can select one numeric feature from a flow CSV, for example:

```json
{"measurement_type":"flow_duration","value_column":"Flow Duration","timestamp_column":"Timestamp","unit":"microseconds"}
```

Mapped timestamps must include an explicit timezone. Convert naive dataset timestamps only when their timezone is known and document that transformation. Each accepted row is linked to a `DatasetRegistry` record with name, version, source URL, license/attribution note, import time, mapping, file SHA-256, accepted/rejected counts, and supported fields. Canonical metrics validate units (`ms`, `percent`, `Mbps`, or `probes`); arbitrary flow features retain the user-supplied unit. Importing is restricted to `NETWORK_OPERATOR`/`ADMIN`, and each import is audit logged. The import API does not accept file system paths.

### RIPE Atlas public measurements

The opt-in adapter retrieves existing publicly accessible **ping** measurements; it never creates or changes a RIPE Atlas measurement. Configure an existing numeric measurement ID on the server. Public observations use `RIPE_ATLAS_PUBLIC`, distinct from local `REAL_MEASUREMENT` probes and uploaded `PUBLIC_DATASET` files.

```dotenv
RIPE_ATLAS_ENABLED=true
RIPE_ATLAS_MEASUREMENT_ID=YOUR_EXISTING_PUBLIC_PING_MEASUREMENT_ID
RIPE_ATLAS_TIMEOUT_SECONDS=10
RIPE_ATLAS_MAX_RESULTS=500
RIPE_ATLAS_LOOKBACK_HOURS=24
```

Find a measurement in the RIPE Atlas UI and check its visibility and type. The adapter validates metadata and currently supports `ping` results only. It calls the anonymous results endpoint with `public_only=true`; no Atlas API key is needed and credentials should not be configured here. The window is limited to 30 days, timeout to 30 seconds, response to 10 MiB, and processed results to 5,000. The endpoint has no result pagination parameter, so an oversized result list is reduced to the newest configured count after the bounded response read. Narrow the time window when possible. HTTP access errors, rate limiting, timeouts, and malformed JSON are recorded in the run status.

Apply migrations and start the backend. Then trigger a bounded import as `NETWORK_OPERATOR` or `ADMIN`:

```http
POST /api/v1/network/ripe-atlas/ingestions
Authorization: Bearer <KAVACHNET access token>
Content-Type: application/json

{}
```

Optional ISO-8601 UTC timestamps override the configured lookback: `{"start":"2026-10-25T00:00:00Z","end":"2026-10-26T00:00:00Z"}`. Inspect the returned run at `GET /api/v1/network/ripe-atlas/ingestions/{run_id}`. `retrieved_count`, `accepted_count`, `rejected_count`, and `deduplicated_count` count source result records; `observations_inserted_count` and `observations_deduplicated_count` count normalized metric rows. The Network page has a separate **RIPE Atlas public** tab with time, metric, and measurement filters, a latency trend, and CSV/JSON export buttons. Filter observations at `/api/v1/network/observations?source_type=RIPE_ATLAS_PUBLIC&measurement_type=round_trip_latency&measurement_id=...`; export filtered observations as CSV at `/api/v1/network/observations/export.csv`, and evaluation JSON at `/api/v1/network/evaluation/export.json?source_type=RIPE_ATLAS_PUBLIC&experiment_id=<run_id>`.

Mapping uses actual ping result fields: Unix `timestamp` becomes UTC; `prb_id` is retained; numeric `avg` is milliseconds; `sent` and `rcvd` yield packet loss only when valid; reachability is true only when at least one reply is reported. Missing fields stay unavailable. Malformed records are counted with limited error details. Provenance includes measurement/probe IDs, firmware and measurement version, target, source URL, retrieval time, and import run ID. Stable IDs derived from the original result make repeated imports idempotent. Atlas ping data cannot supply local application acknowledgment, throughput, or queue-recovery metrics, so those are not inferred.

An empty response is a completed retrieval with zero observations, not a successful evaluation. Evaluation remains `PENDING_DATA_COLLECTION` until actual observations are persisted. Runs are manually triggered and audited; scheduled polling is not implemented. No public measurement is bundled or assumed by default.

### CIC-IDS2017 assessment

The official [Canadian Institute for Cybersecurity CIC-IDS2017 page](https://www.unb.ca/cic/datasets/ids-2017.html) describes PCAP traffic, labeled flows, CSV files, timestamps, endpoint/port/protocol fields, labels, and more than 80 flow features. It covers five days in July 2017. The page lists daily capture sizes from 7.8 GB to 13 GB; acquisition is through the provider's download flow, and this project does not download or bundle it. The provider states that researchers may use the dataset and asks users to cite the dataset paper. The provider's FAQ also says datasets may be redistributed, republished, and mirrored if cited; this is provider usage guidance, not an SPDX license identifier, so preserve the citation and verify terms before redistribution.

CIC-IDS2017 is suitable for labeled traffic-flow/intrusion analysis. Its published fields do not establish endpoint RTT, jitter, packet delivery outcomes, application acknowledgments, or controlled endpoint throughput. Flow-derived byte rates or durations must remain flow features, not be relabeled as measured link throughput or delivery metrics. Those operational metrics require direct, bounded measurements from an authorized test endpoint. Use the mapping importer only for the specific flow feature being analyzed; do not infer unsupported metrics.

### APIs added

| Method and route | Purpose | Authorization |
|---|---|---|
| `GET /api/v1/network/observations` | Source, metric, experiment, dataset-name, time-range filters and pagination | Authenticated |
| `GET /api/v1/network/evaluation?source_type=...` | Separate source-specific summaries and missing-data status | Authenticated |
| `GET /api/v1/network/measurements/config` | Sanitized target label and collector capabilities; omits target URL | Authenticated |
| `POST /api/v1/network/measurements/runs` | Start bounded configured measurement run | Network operator/admin |
| `GET /api/v1/network/measurements/runs/{run_id}` | Inspect run counts, state and errors | Authenticated |
| `POST /api/v1/network/measurements/runs/{run_id}/stop` | Stop a running measurement job | Network operator/admin |
| `POST /api/v1/network/datasets/import` | Validate and import uploaded CSV/JSON with provenance | Network operator/admin |
| `GET /api/v1/network/datasets/{dataset_id}` | Import provenance and rejected-row summary | Authenticated |
| `GET /api/v1/network/ripe-atlas/config` | Sanitized configured measurement ID and bounded settings | Authenticated |
| `POST /api/v1/network/ripe-atlas/ingestions` | Trigger configured public ping result ingestion | Network operator/admin |
| `GET /api/v1/network/ripe-atlas/ingestions/{run_id}` | Inspect counts, status, errors, and window | Authenticated |
| `GET /api/v1/network/observations/export.csv` | Export up to 10,000 filtered observations | Authenticated |
| `GET /api/v1/network/evaluation/export.json` | Export a source-specific evaluation summary | Authenticated |

All collection and import operations use the existing shared database and audit log. Frontend source tabs keep simulator controls out of the real-measurement and public-dataset views. The observation table is API-backed, has metric filtering, source labels, units, and an explicit unavailable state. It polls through REST; WebSocket support remains unavailable.
