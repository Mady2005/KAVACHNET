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

Pass `Authorization: Bearer <access_token>` for protected calls. Browser clients may use the HttpOnly session cookie; state-changing cookie-authenticated requests must also send the CSRF cookie value in `X-CSRF-Token`. Responses use FastAPI validation errors and `detail` error messages. No event update/ack endpoint or public user creation is enabled in this phase.

Network routes use synthetic state and a local simulated receiver; they do not modify or measure the host network. See `backend/app/modules/network_resilience/README.md` for payload, queue, retry, and metric semantics. The backend does not currently expose a WebSocket endpoint.
