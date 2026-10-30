"""Typed inputs and privacy-safe outputs for the Security inspection core."""

from __future__ import annotations

import copy
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator


class InspectionKind(StrEnum):
    TEXT = "TEXT"
    DOCUMENT_TEXT = "DOCUMENT_TEXT"


class SecuritySeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingCategory(StrEnum):
    PROMPT_INJECTION = "PROMPT_INJECTION"
    JAILBREAK = "JAILBREAK"
    MALICIOUS_CONTENT = "MALICIOUS_CONTENT"
    CREDENTIAL = "CREDENTIAL"
    SENSITIVE_PERSONAL_DATA = "SENSITIVE_PERSONAL_DATA"


class FindingContext(StrEnum):
    DIRECT = "DIRECT"
    EDUCATIONAL = "EDUCATIONAL"


class InspectionValidationError(ValueError):
    """Validation failure whose message and structured forms omit submitted input."""

    def __init__(self, errors: list[dict[str, object]], serialized: str | None = None):
        self._safe_errors = copy.deepcopy(errors)
        self._safe_json = serialized or json.dumps(errors, ensure_ascii=False)
        messages = "; ".join(str(error.get("msg", "Invalid input")) for error in errors)
        super().__init__(messages or "Invalid inspection request")

    def errors(self) -> list[dict[str, object]]:
        return copy.deepcopy(self._safe_errors)

    def json(self) -> str:
        return self._safe_json


class _InspectionRequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        hide_input_in_errors=True,
    )

    content: StrictStr = Field(min_length=1, max_length=100_000)
    kind: InspectionKind = InspectionKind.TEXT

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must contain non-whitespace characters")
        return value


class InspectionRequest:
    """Validated text input with Pydantic errors sanitized before exposure."""

    __slots__ = ("content", "kind")

    content: str
    kind: InspectionKind

    def __init__(self, content: object, kind: object = InspectionKind.TEXT, **extra: object):
        safe_errors: list[dict[str, object]] | None = None
        safe_json: str | None = None
        try:
            parsed = _InspectionRequestModel(content=content, kind=kind, **extra)
        except ValidationError as error:
            # Extract only sanitized data, then leave the handler before raising the
            # public exception so Python cannot attach this error as __context__.
            safe_errors = error.errors(include_input=False)
            safe_json = error.json(include_input=False)
        if safe_errors is not None and safe_json is not None:
            # Tracebacks retain this frame and its locals. Drop all submitted
            # arguments before raising the public exception.
            del content, kind, extra
            raise InspectionValidationError(safe_errors, safe_json)
        self.content = parsed.content
        self.kind = parsed.kind


class SecurityFinding(BaseModel):
    """A rule match without matched text, offsets, or submitted secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: FindingCategory
    rule_id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)
    occurrences: int = Field(ge=1)
    score_weight: float = Field(ge=0, le=1)
    context: FindingContext = FindingContext.DIRECT


class InspectionResult(BaseModel):
    """Inspection outcome; sensitive matches are represented only by redactions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rules_version: str = Field(min_length=1, max_length=32)
    risk_score: float = Field(ge=0, le=1)
    severity: SecuritySeverity
    findings: tuple[SecurityFinding, ...] = ()
    redacted_content: str
    redaction_counts: dict[str, int] = Field(default_factory=dict)
