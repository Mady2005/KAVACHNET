# Member 2 — Edge AI implementation and demo guide

**Scope:** Edge sensor analysis, canonical event generation, persisted observations, event correlation, incident lifecycle APIs, and Edge Intelligence UI.
**Development verification:** backend 33 passed; frontend 7 passed; production build passed (commands and environment below). PostgreSQL/shared-database readiness and production readiness are unverified.

## Implementation flow

1. `backend/app/modules/edge_ai/schemas.py` validates supported sensor readings; `sensor_validation.py`, `rules.py`, `anomaly_detector.py`, and `service.py` analyze measurements and produce a canonical `KavachEvent` with `source_module=EDGE_AI`, severity/action, and evidence.
2. `backend/app/modules/edge_ai/router.py` exposes authenticated analysis, simulation, history and model endpoints. `POST /api/v1/edge-ai/analyze` stores a deterministic observation record and associates its canonical event. Exact observation replays return the saved result.
3. `backend/app/api/v1/routes.py` persists canonical events at `POST /api/v1/events`, checks role/source permissions, handles exact `event_id` replays, rejects payload reuse conflicts, and invokes correlation after persistence.
4. `backend/app/modules/edge_ai/correlation_policy.py` selects eligible EDGE_AI `sensor_anomaly` events and groups by sensor and exact rule category. `correlation_service.py` uses database locks and event-link identity to create/update one incident, associates linked observations, escalates to the highest event severity, and keeps resolved incidents terminal. The configurable default window is 900 seconds.
5. `backend/app/modules/edge_ai/incident_router.py` serves authenticated incident list/detail/status endpoints. Detail returns linked canonical event evidence, observation measurements, and status history.
6. `backend/app/models/entities.py`, `backend/migrations/versions/0002_sensor_observations.py`, and `0003_incident_management.py` define persistent observations, incidents, event links, status history and correlation locks. Migration `0001_initial.py` excludes later tables so sequential migrations remain valid.
7. `frontend/src/app/App.tsx` routes authorized operators to `/edge-ai`; `frontend/src/features/edge-ai/EdgeAIPage.tsx` calls the real `/api/v1` endpoints and renders analysis history, incidents, linked evidence, and lifecycle actions. `frontend/vite.config.ts` proxies local `/api` requests to the backend.

## API routes

- `POST /api/v1/edge-ai/analyze` — analyze and persist one observation/event.
- `POST /api/v1/edge-ai/analyze/batch` and `/simulate` — batch/simulated analysis.
- `GET /api/v1/edge-ai/history` — persisted observation and analysis history.
- `POST /api/v1/events` — canonical event persistence and idempotent replay.
- `GET /api/v1/incidents` — paginated/filterable incidents.
- `GET /api/v1/incidents/{incident_id}` — incident, linked event and observation evidence, and status history.
- `PATCH /api/v1/incidents/{incident_id}/status` — permitted lifecycle transition.

All relevant operations use existing authentication and role dependencies. Edge AI analysis/incident operations are restricted to `ADMIN` and `EDGE_OPERATOR`; the shared event route additionally enforces module-role/source permissions.

## Windows PowerShell setup and demo

Use an authorized local development `EDGE_OPERATOR`; registration is disabled. Do not put passwords in commands or logs. The documented multi-role provisioning script creates multiple roles, so use an approved least-privilege local account setup when only an operator is needed.

From the repository root, follow `README.md`:

```powershell
Copy-Item .env.example .env
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location backend
alembic upgrade head
Set-Location ..
```

For the optional predictive-maintenance model, install `backend\requirements-edge-ai-ml.txt` and run the documented trainer in `backend\app\modules\edge_ai\train_ai4i.py` with an authorized dataset. The generated `.joblib` model is intentionally not transferred in this feature change; without a locally provisioned artifact, prediction reports unavailable. Core sensor analysis does not need that model.

Backend terminal (from repository root):

```powershell
.\.venv\Scripts\Activate.ps1
Set-Location backend
uvicorn app.main:app --reload
```

Frontend terminal (from repository root):

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` in the browser. To start Chrome from PowerShell: `Start-Process 'chrome.exe' -ArgumentList 'http://127.0.0.1:5173'`. API docs are at `http://127.0.0.1:8000/docs`.

1. Sign in through the normal UI with the authorized local operator.
2. Use the application navigation to open **Edge Intelligence**.
3. Submit a valid reading with a unique sensor ID, a current timezone-aware timestamp, and a supported measurement. For a development demonstration, a temperature above the configured maximum produces a HIGH anomaly; clearly identify it as test data.
4. Confirm the analysis result and persisted-history row; then confirm the incident list entry and open its detail to inspect linked event and observation evidence.
5. Refresh the page and confirm the same incident remains, loaded from the API.
6. To check exact event replay, submit the exact returned canonical event payload again to the authenticated `POST /api/v1/events` endpoint. Reusing its event ID with a changed payload should return conflict. The UI analysis form generates a fresh timestamp on each submission, so repeating the form is a new observation rather than an exact replay.
7. Use the incident actions to acknowledge/resolve and confirm the updated state after refresh. Verify role restrictions with authorized accounts only.

## Verification performed

Commands were run in the `KAVACHNET-git` feature checkout. Its default database was a new ignored local SQLite file at `backend/kavachnet.db`; `alembic upgrade head` completed through `0003_incident_management`. No PostgreSQL or shared database was used.

- Backend: `..\..\KAVACHNET-main\.venv\Scripts\python.exe -m pytest app\tests -q --basetemp 'C:\Users\Ankita\.codex\visualizations\2026\10\26\01a178ec-3c2d-7b81-816e-a50d61c17738\pytest-kavachnet-member2-20261029'` from `backend/` — **33 passed, 1 Starlette/httpx deprecation warning**. For the full prediction test, `KAVACHNET_AI4I_MODEL_PATH` was set to the already existing local model in `KAVACHNET-main`; the model binary was not copied into this checkout.
- Frontend setup: `npm install --no-package-lock` from `frontend/` — completed; npm reported 8 existing vulnerabilities (5 moderate, 1 high, 2 critical).
- Frontend tests: `npm test` from `frontend/` — **2 files, 7 tests passed**, with React Router future-flag warnings.
- Production bundle: `npm run build` from `frontend/` — **passed** (TypeScript and Vite; 1629 modules transformed).

These tests are automated tests, not a repeat of the earlier live-browser workflow. The prior local authenticated acceptance recorded a persisted incident and linked evidence, refresh persistence, and duplicate correlation behavior. Those checks used the local SQLite development application, not PostgreSQL/shared infrastructure.

## User-observed screenshot and boundaries

The user reported a local dashboard screenshot showing: Recorded events 1, High/critical 1, Awaiting action 1, Module sources 1; a `sensor_anomaly` event from `EDGE_AI`, severity `HIGH`, status `NEW`, and API connected. This is recorded only as a user-observed local screenshot; it is not independent proof of all end-to-end workflows.

Member 1 security-scanner feeds and Member 3 network feeds were not integrated or validated here. Current correlation policy handles only eligible Edge AI sensor anomaly events; cross-module category/source policy remains to be agreed. SQLite was the only tested database. PostgreSQL/shared-database behavior, deployment configuration, operational safety thresholds, and production readiness remain unverified. The current npm audit reports 8 vulnerabilities; no breaking dependency upgrades were performed.

## Interview explanations

- **Why canonical events?** A shared, typed `KavachEvent` keeps module outputs interoperable and preserves event identity for retrieval and idempotent replay.
- **How is correlation bounded?** Only eligible Edge AI anomaly events with the same sensor and category join; the full timestamp span must fit the configured window. Resolved incidents are not reopened by correlation.
- **How is duplicate handling enforced?** Stable observation identity returns the saved analysis for an exact replay; canonical `event_id` plus the unique incident-event link prevents duplicate associations.
- **How is evidence traceable?** Incident detail exposes the stored event evidence and the linked observation measurement/timestamp instead of synthesizing evidence from UI state.
- **What remains before production?** Validate field thresholds, PostgreSQL locking/migrations and shared deployment, cross-member event policy, secure production secrets/cookies, operational monitoring, and dependency advisories.