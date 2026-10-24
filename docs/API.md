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

Pass `Authorization: Bearer <access_token>` for protected calls. Browser clients may use the HttpOnly session cookie; state-changing cookie-authenticated requests must also send the CSRF cookie value in `X-CSRF-Token`. Responses use FastAPI validation errors and `detail` error messages. No event update/ack endpoint or public user creation is enabled in this phase.
