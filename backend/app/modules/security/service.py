"""Security detection service adapter for the shared KavachEvent contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.events import (
    Action,
    EventStatus,
    KavachEvent,
    Priority,
    Severity as _SharedSeverity,
    SourceModule,
)
from app.services.interfaces import SecurityDetectionService

from .engine import inspect_text
from .schemas import (
    InspectionKind,
    InspectionRequest,
    InspectionResult,
    InspectionValidationError,
    SecuritySeverity,
)


_PRIORITY_BY_SEVERITY = {
    SecuritySeverity.CRITICAL: Priority.P0,
    SecuritySeverity.HIGH: Priority.P1,
    SecuritySeverity.MEDIUM: Priority.P2,
    SecuritySeverity.LOW: Priority.P3,
    SecuritySeverity.INFO: Priority.P3,
}

_ACTION_BY_SEVERITY = {
    SecuritySeverity.CRITICAL: Action.ALERT,
    SecuritySeverity.HIGH: Action.ALERT,
    SecuritySeverity.MEDIUM: Action.WARN,
    SecuritySeverity.LOW: Action.WARN,
    SecuritySeverity.INFO: Action.ALLOW,
}

_SEVERITY_TO_SHARED = {
    SecuritySeverity.INFO: _SharedSeverity.INFO,
    SecuritySeverity.LOW: _SharedSeverity.LOW,
    SecuritySeverity.MEDIUM: _SharedSeverity.MEDIUM,
    SecuritySeverity.HIGH: _SharedSeverity.HIGH,
    SecuritySeverity.CRITICAL: _SharedSeverity.CRITICAL,
}


@dataclass(frozen=True)
class InspectionSuccess:
    event: KavachEvent


@dataclass(frozen=True)
class InspectionFailure:
    """Sanitized validation details without a retained exception/traceback."""

    errors: tuple[dict[str, object], ...]
    status_code: int = 422


InspectionOutcome = InspectionSuccess | InspectionFailure


def _invalid_payload_error(message: str) -> InspectionValidationError:
    """Build a fixed-detail error that never incorporates payload values."""
    return InspectionValidationError(
        [
            {
                "type": "invalid_inspection_payload",
                "loc": ("payload",),
                "msg": message,
            }
        ]
    )


def _safe_evidence(result: InspectionResult, kind: InspectionKind) -> dict[str, object]:
    """Copy only fixed, bounded inspection metadata into the event evidence."""
    return {
        "rules_version": result.rules_version,
        "inspection_kind": kind.value,
        "finding_count": len(result.findings),
        "findings": [
            {
                "rule_id": finding.rule_id,
                "category": finding.category.value,
                "occurrences": finding.occurrences,
                "score_weight": finding.score_weight,
                "context": finding.context.value,
            }
            for finding in result.findings
        ],
        "redaction_counts": dict(result.redaction_counts),
    }


class SecurityInspectionService(SecurityDetectionService):
    """Adapt Phase 1 text inspection into a safe, non-persisted KavachEvent.

    Event persistence and audit remain responsibilities of the shared API path.
    This service only emits advisory recommendations; it never enforces them.
    """

    def inspect_result(self, payload: object) -> InspectionOutcome:
        """Non-raising input path for HTTP callers handling sensitive text.

        Validation exceptions are reduced to safe error records and discarded
        before returning, so route handlers need not create validation tracebacks.
        """
        try:
            event = self.inspect(payload)  # type: ignore[arg-type]
        except InspectionValidationError as error:
            return InspectionFailure(tuple(error.errors()))
        except Exception:
            # Fail closed without propagating a traceback that could retain
            # detector/request locals. The HTTP layer receives only this fixed error.
            return InspectionFailure(
                (
                    {
                        "type": "inspection_failed",
                        "loc": ("payload",),
                        "msg": "Security inspection could not be completed",
                    },
                ),
                status_code=500,
            )
        return InspectionSuccess(event)

    def inspect(self, payload: dict) -> KavachEvent:
        validation_error: InspectionValidationError | None = None
        request: InspectionRequest | None = None

        if type(payload) is not dict:
            validation_error = _invalid_payload_error("Inspection payload must be an object")
        elif any(not isinstance(key, str) for key in payload):
            validation_error = _invalid_payload_error("Inspection payload fields must be strings")
        else:
            try:
                # The request schema forbids unknown fields and strictly validates
                # content and kind. Never copy other payload fields into the event.
                request = InspectionRequest(**payload)
            except InspectionValidationError as error:
                validation_error = error
            except TypeError:
                # Covers invalid keyword mappings without reflecting their contents.
                validation_error = _invalid_payload_error("Inspection payload is invalid")

        if validation_error is not None:
            # The traceback retains this frame; scrub the untrusted mapping before
            # propagating a sanitized domain error.
            del payload
            raise validation_error

        assert request is not None  # Guaranteed by the validated-input path above.
        kind = request.kind
        try:
            result = inspect_text(request.content, kind=kind.value)
        finally:
            # Do not leave the request object containing submitted text in this
            # service frame if inspection raises unexpectedly.
            del request, payload

        return KavachEvent(
            event_id=uuid4(),
            timestamp=datetime.now(timezone.utc),
            source_module=SourceModule.SECURITY,
            event_type="security.inspection",
            severity=_SEVERITY_TO_SHARED[result.severity],
            security_risk=result.risk_score,
            situation_risk=None,
            network_health=None,
            priority=_PRIORITY_BY_SEVERITY[result.severity],
            recommended_action=_ACTION_BY_SEVERITY[result.severity],
            evidence=_safe_evidence(result, kind),
            confidence=None,
            status=EventStatus.NEW,
            correlation_id=str(uuid4()),
            simulation=False,
        )
