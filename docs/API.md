# API reference

All routes use `/api/v1`. FastAPI OpenAPI is generated at `/openapi.json`; Swagger UI is `/docs`.

| Method | Route | Access | Purpose |
|---|---|---|---|
| POST | `/auth/login` | Public | Validate credentials; issue short-lived bearer and HttpOnly session cookie |
| POST | `/auth/logout` | Authenticated | Revoke token identifier and clear cookie |
| GET | `/auth/me` | Authenticated | Current principal |
| POST | `/auth/register` | Public; disabled by default | Create a VIEWER when explicitly enabled |
| GET | `/health` | Public | API liveness |
| GET | `/health/ready` | Public | Database readiness |
| GET | `/system/status` | Authenticated | API/database and configured-module state |
| GET | `/dashboard/summary` | Authenticated | Database-derived event counts, distributions and recent events |
| GET | `/events?offset=0&limit=50` | Authenticated | Paginated event history (limit max 200) |
| GET | `/events/{event_id}` | Authenticated | Event detail |
| POST | `/events` | Module operator or ADMIN | Persist validated shared event |
| GET | `/decisions` | Authenticated | Recent decision records |
| GET | `/audit-logs` | ADMIN | Recent audit records |
| GET | `/users` | ADMIN | User listing |
| PATCH | `/users/{user_id}/role` | ADMIN | Change role; audited |
| GET | `/datasets` | Authenticated | Registry list |
| POST/PATCH | `/datasets[/{dataset_id}]` | ADMIN | Create/update registry metadata; audited |
| GET | `/network/status` | Authenticated | Current synthetic telemetry and queue counts |
| GET | `/network/history` | Authenticated | Persisted telemetry history |
| POST | `/network/simulator/scenario` | NETWORK_OPERATOR or ADMIN | Set a controlled synthetic scenario |
| POST | `/network/simulator/telemetry` | NETWORK_OPERATOR or ADMIN | Set validated synthetic telemetry |
| POST | `/network/simulator/reset` | NETWORK_OPERATOR or ADMIN | Reset simulation to healthy defaults |
| GET | `/network/queue` | Authenticated | Counts by message state and priority |
| POST | `/network/messages` | NETWORK_OPERATOR or ADMIN | Enqueue a size-limited JSON message |
| GET | `/network/messages` | Authenticated | Paginated messages, optional state/priority filters |
| GET | `/network/messages/{message_id}` | Authenticated | Message metadata and attempts (payload excluded) |
| POST | `/network/messages/{message_id}/retry` | NETWORK_OPERATOR or ADMIN | Manually retry a failed message |
| GET | `/network/metrics` | Authenticated | Compression, delivery attempts, and simulated latency metrics |
| GET | `/network/deliveries` | Authenticated | Paginated simulated delivery attempt history |
| GET | `/network/observations?source_type=REAL_MEASUREMENT&limit=50` | Authenticated | Normalized source-filtered telemetry with bounded pagination and optional provider/measurement ID/metric/time/experiment/dataset filters |
| GET | `/network/observations/export.csv` | Authenticated | Export up to 10,000 filtered observations as CSV |
| GET | `/network/evaluation?source_type=REAL_MEASUREMENT` | Authenticated | Source-specific summaries; unavailable metrics remain null |
| GET | `/network/evaluation/export.json?source_type=...` | Authenticated | Export bounded source-specific evaluation summary |
| GET | `/network/measurements/config` | Authenticated | Sanitized collector configuration; the endpoint URL is not returned |
| POST | `/network/measurements/runs` | NETWORK_OPERATOR or ADMIN | Start a bounded measurement run against the server-configured target; JSON `{ "samples": 5 }` |
| GET | `/network/measurements/runs/{run_id}` | Authenticated | Inspect run status and successful/failed probe counts |
| POST | `/network/measurements/runs/{run_id}/stop` | NETWORK_OPERATOR or ADMIN | Stop an active run |
| POST | `/network/datasets/import` | NETWORK_OPERATOR or ADMIN | Multipart CSV/JSON import with source URL, version, license, mapping and row validation |
| GET | `/network/datasets/{dataset_id}` | Authenticated | Dataset import summary and provenance |
| GET | `/network/ripe-atlas/config` | Authenticated | Sanitized configured provider, measurement ID and bounded ingestion settings |
| POST | `/network/ripe-atlas/ingestions` | NETWORK_OPERATOR or ADMIN | Trigger bounded ingestion of an explicitly configured public RIPE Atlas ping measurement; optional start/end timestamps |
| GET | `/network/ripe-atlas/ingestions/{run_id}` | Authenticated | Inspect RIPE Atlas ingestion status, counts, errors, and time window |

Pass `Authorization: Bearer <access_token>` for protected calls. Browser clients may use the HttpOnly session cookie; state-changing cookie-authenticated requests must also send the CSRF cookie value in `X-CSRF-Token`. Responses use FastAPI validation errors and `detail` error messages. No event update/ack endpoint or public user creation is enabled in this phase.

Network simulator routes use synthetic state and a local simulated receiver; they do not modify the host network. Real measurement routes are disabled unless an authorized endpoint is explicitly configured in backend environment settings. Dataset imports are local uploads and do not trigger downloads. See `backend/app/modules/network_resilience/README.md` for payload, queue, retry, provenance, and metric semantics. The backend does not currently expose a WebSocket endpoint.
