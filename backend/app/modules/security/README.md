# Security inspection module

The Security module provides deterministic, rules-based inspection for text. Its service adapts inspection results to the shared `KavachEvent` contract; the v1 API persists successful inspections through the shared event and audit path. Recommendations are advisory. The module does not block, quarantine, execute, or otherwise enforce actions.

## Architecture

- `schemas.py` defines strict request validation, finding/result models, severities, categories, and safe validation errors.
- `engine.py` normalizes text for detection, runs bounded local rules, calculates a deterministic risk score, and produces redacted content for direct engine callers.
- `service.py` implements `SecurityDetectionService.inspect(payload: dict) -> KavachEvent` and the non-raising `inspect_result(payload)` path used by HTTP. It creates server-side event and correlation IDs and UTC timestamps, maps safe result metadata, and does not persist events.
- `backend/app/api/v1/routes.py` owns the authenticated Security endpoints and uses the existing event persistence and audit functions. The Security module does not write to the database directly.
- The frontend Security Center calls these APIs through the shared authenticated API client.

## Detection and scoring

The current local rule set covers selected patterns for prompt injection (including instruction override and system-prompt disclosure), jailbreak and role manipulation, dangerous command/script text, credentials, and selected personal data. Detection normalizes Unicode compatibility forms, HTML entities, and selected zero-width characters. Context labels can reduce the score for educational or cautionary examples while preserving findings.

Each distinct matching rule contributes its fixed rule weight once. Additional occurrences of that rule add 0.05 each, capped at 0.10. EDUCATIONAL context contributes 10% of the rule weight; DIRECT context contributes the full weight. Contributions are summed, capped at 1.0, and rounded to three decimal places. This deterministic score is a heuristic, not a probability.

| Score | Severity |
|---|---|
| `< 0.15` | INFO |
| `0.15–<0.40` | LOW |
| `0.40–<0.65` | MEDIUM |
| `0.65–<0.85` | HIGH |
| `>= 0.85` | CRITICAL |

These thresholds and rule weights are fixed policy defaults; evaluate them against an approved representative corpus before operational reliance.

## API

Both endpoints are under `/api/v1` and require an authenticated session or bearer token. Only `ADMIN` and `SECURITY_ANALYST` may use them.

### `POST /api/v1/security/inspect`

Request JSON accepts only `content` and optional `kind`:

```json
{"content":"text to inspect","kind":"TEXT"}
```

`kind` is `TEXT` (default) or `DOCUMENT_TEXT`; the latter means already-extracted text, not a file upload or document parser. Content must be a non-blank string of at most 100,000 characters. The HTTP body is separately bounded at 1,000,000 bytes. Unknown fields and client-supplied event metadata are rejected.

A successful request returns HTTP 201:

```json
{
  "event": {
    "event_id": "server-generated UUID",
    "timestamp": "server-generated UTC timestamp",
    "source_module": "SECURITY",
    "event_type": "security.inspection",
    "severity": "INFO",
    "security_risk": 0.0,
    "situation_risk": null,
    "network_health": null,
    "priority": "P3",
    "recommended_action": "ALLOW",
    "evidence": {
      "rules_version": "...",
      "inspection_kind": "TEXT",
      "finding_count": 0,
      "findings": [],
      "redaction_counts": {}
    },
    "confidence": null,
    "status": "NEW",
    "correlation_id": "server-generated value",
    "simulation": false
  },
  "findings": []
}
```

The example illustrates the shape only. IDs, timestamp, score, severity, and evidence are generated from the actual inspection. Validation errors use sanitized details (422); malformed JSON is reported generically (400), oversized bodies are rejected (413), and unexpected inspection failures return a generic error (500). Invalid requests do not create event or audit records.

### `GET /api/v1/security/events`

Returns only `SECURITY` events, newest first, in `{items, offset, limit, total}` form. `offset` defaults to 0 and must be non-negative. `limit` defaults to 50 and must be 1–100. The API does not currently accept severity or category filters; the frontend filters the loaded page only.

## Event mapping

Every successful inspection is persisted, including INFO inspections with no findings. The adapter maps fields as follows:

| `KavachEvent` field | Security value |
|---|---|
| `event_id`, `timestamp`, `correlation_id` | Generated server-side |
| `source_module`, `event_type` | `SECURITY`, `security.inspection` |
| `security_risk`, `severity` | Deterministic inspection score and classification |
| `situation_risk`, `network_health`, `confidence` | `null` |
| `priority` | CRITICAL=P0, HIGH=P1, MEDIUM=P2, LOW/INFO=P3 |
| `recommended_action` | INFO=ALLOW, LOW/MEDIUM=WARN, HIGH/CRITICAL=ALERT |
| `status`, `simulation` | `NEW`, `false` |

Actions are recommendations only. No endpoint applies them automatically.

## Evidence and privacy

Event evidence is allowlisted to `rules_version`, `inspection_kind`, `finding_count`, `findings`, and `redaction_counts`. Each finding contains only `rule_id`, `category`, `occurrences`, `score_weight`, and `context`. Evidence excludes submitted text, redacted text, matched values, sensitive offsets, and arbitrary request metadata. API responses likewise return only the event and safe finding metadata.

The shared persistence path commits the event and then creates the existing `event.create` audit record with actor and event ID. Audit details do not include inspection text. These are separate commits, matching the existing shared behavior; they are not one atomic transaction. Validation failures create neither record. Request bodies and sensitive content must not be logged by callers or middleware.

The API does not return `redacted_content`. Direct callers of `inspect_text` do receive it and must handle it as sensitive derived data. Detection/redaction rules cover common formats only and redaction is not complete for every possible secret.

## Shared metric nullability and migration

Revision `0002_security_nullable` follows `0001_initial` and makes `situation_risk` and `network_health` nullable in the database. Both fields remain required in the event contract. For SECURITY events, both may be numeric or both may be null; a mixed pair is rejected. Non-Security events must continue to supply numeric values for both fields.

Apply with `alembic upgrade head` from `backend/`. The migration preserves existing values. Downgrade to `0001_initial` is intentionally refused while any event row contains a null in either metric; remove or otherwise resolve those records under an explicitly approved data plan before downgrade. The migration never invents replacement values.

## Tests

From `backend/`, run `python -m pytest`. Pytest discovery includes `app/tests` and `app/modules/security`. Security-owned tests cover engine rules and scoring, strict/safe validation, event contract compatibility, service mapping, API authorization/privacy/persistence, and migration upgrade/downgrade behavior. Migration tests use temporary SQLite databases; API tests use the test client and an in-memory SQLite database.

From `frontend/`, run `pnpm test` and `pnpm run build` (the build runs TypeScript project checks and Vite). Frontend tests cover role visibility, inspection flows, privacy, nullable metrics, filters, and pagination.

## Limitations

- Rules are an explainable baseline, not a full malware scanner or general prompt-injection classifier. Novel, multilingual, encoded, context-dependent, and obfuscated attacks may be missed; benign quotations can still trigger findings.
- The engine does not decode arbitrary base64, inspect images, parse PDF/office files, fetch URLs, or execute code. `DOCUMENT_TEXT` is text supplied by the caller.
- Credential and personal-data patterns recognize common formats only. Custom credentials, unfamiliar tokens, non-US identifiers, and unusual encodings may escape detection. Redaction is not complete for every possible secret; credential assignment handling may conservatively redact the remainder of a line when a value contains spaces.
- Educational/cautionary context is heuristic. It reduces score without suppressing a finding and can be evaded or misapplied.
- The HTTP path uses `inspect_result`, a non-raising result API that avoids propagating sensitive validation/detector tracebacks to the caller. The exception-based `InspectionRequest`/`inspect_text` APIs sanitize their public exception and Security-owned traceback frames, but Python cannot scrub caller-owned frames or other copies retained by the caller. Callers needing traceback-wide privacy should use the non-raising result API and avoid retaining submitted content in their own frames.
- Risk scores and severity are deterministic rules-based indicators, not calibrated likelihoods or incident-response decisions.
