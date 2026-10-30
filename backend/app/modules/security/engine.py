"""Deterministic, explainable text inspection for the Security module.

This is a rules-based baseline. It never executes submitted text and never logs
or persists the input. Rule evidence contains identifiers and counts only.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from .schemas import (
    FindingContext,
    FindingCategory,
    InspectionRequest,
    InspectionResult,
    InspectionValidationError,
    SecurityFinding,
    SecuritySeverity,
)


RULES_VERSION = "security-rules-1"


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    category: FindingCategory
    description: str
    pattern: re.Pattern[str]
    weight: float


@dataclass(frozen=True)
class _SensitivePattern:
    rule_id: str
    category: FindingCategory
    description: str
    pattern: re.Pattern[str]
    weight: float
    label: str
    priority: int = 10
    group: str | None = None
    luhn_check: bool = False


@dataclass(frozen=True)
class _Redaction:
    start: int
    end: int
    label: str
    priority: int
    categories: frozenset[FindingCategory]


_FLAGS = re.IGNORECASE
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"}


def _rule(
    rule_id: str,
    category: FindingCategory,
    description: str,
    expression: str,
    weight: float,
) -> _Rule:
    return _Rule(rule_id, category, description, re.compile(expression, _FLAGS), weight)


_DETECTION_RULES = (
    _rule(
        "injection.instruction_override",
        FindingCategory.PROMPT_INJECTION,
        "Text attempts to replace or disregard prior instructions.",
        r"\b(?:ignore|disregard|forget|override)\b.{0,100}\b(?:(?:all|previous|prior|above|earlier|system|developer)\s+){0,2}(?:instructions?|directives?|rules?|prompts?)\b",
        0.48,
    ),
    _rule(
        "injection.instruction_disclosure",
        FindingCategory.PROMPT_INJECTION,
        "Text requests disclosure of hidden or privileged instructions.",
        r"\b(?:reveal|print|show|repeat|disclose|output|expose)\b.{0,100}\b(?:the\s+)?(?:hidden|system|developer|internal|secret)\s+(?:prompt|instructions?|message|policy)\b",
        0.38,
    ),
    _rule(
        "injection.tool_override",
        FindingCategory.PROMPT_INJECTION,
        "Text attempts to direct an assistant to misuse tools or override its role.",
        r"\b(?:ignore|override|replace)\b.{0,80}\b(?:your|the)\s+(?:role|tool|function|instructions?)\b|\b(?:call|invoke|use)\s+(?:the\s+)?(?:tool|function)\b.{0,80}\b(?:without|ignore|bypass)\b",
        0.40,
    ),
    _rule(
        "jailbreak.safety_bypass",
        FindingCategory.JAILBREAK,
        "Text asks to bypass or disable safety controls.",
        r"\b(?:bypass|disable|ignore|remove|circumvent|turn\s+off)\b.{0,80}\b(?:safety|guardrails?|safeguards?|policy|content\s+filters?)\b",
        0.62,
    ),
    _rule(
        "jailbreak.unrestricted_mode",
        FindingCategory.JAILBREAK,
        "Text asks for an unrestricted or uncensored operating mode.",
        r"\b(?:developer\s+mode|do\s+anything\s+now|unrestricted\s+mode|uncensored\s+mode|no\s+rules|without\s+(?:any\s+)?restrictions)\b",
        0.62,
    ),
    _rule(
        "jailbreak.roleplay_bypass",
        FindingCategory.JAILBREAK,
        "Text uses a role-play request to evade normal restrictions.",
        r"\bpretend\b.{0,100}\b(?:you\s+are|to\s+be)\b.{0,100}\b(?:unrestricted|uncensored|without\s+(?:any\s+)?rules|evil)\b",
        0.50,
    ),
    _rule(
        "malware.download_execute",
        FindingCategory.MALICIOUS_CONTENT,
        "Text contains a shell pattern that pipes a remote download into a shell.",
        r"\b(?:curl|wget)\b[^\r\n]{0,180}\|\s*(?:sh|bash)\b",
        0.68,
    ),
    _rule(
        "malware.encoded_powershell",
        FindingCategory.MALICIOUS_CONTENT,
        "Text contains an encoded PowerShell execution indicator.",
        r"\b(?:powershell|pwsh)\b[^\r\n]{0,160}(?:-encodedcommand\b|-enc\b|frombase64string\s*\()",
        0.68,
    ),
    _rule(
        "malware.destructive_command",
        FindingCategory.MALICIOUS_CONTENT,
        "Text contains a command targeting broad or root-level deletion.",
        r"\brm\s+-rf\s+(?:/|~|[a-z]:\\)(?:\s|$)",
        0.76,
    ),
    _rule(
        "malware.credential_theft",
        FindingCategory.MALICIOUS_CONTENT,
        "Text describes collecting or exfiltrating credentials or private keys.",
        r"\b(?:steal|exfiltrate|harvest|dump|send)\b.{0,100}\b(?:passwords?|credentials?|access\s+tokens?|private\s+keys?)\b",
        0.72,
    ),
)


_SENSITIVE_PATTERNS = (
    _SensitivePattern(
        "secret.private_key",
        FindingCategory.CREDENTIAL,
        "A private-key block was detected.",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]{1,10000}?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", _FLAGS),
        0.86,
        "CREDENTIAL",
        priority=100,
    ),
    _SensitivePattern(
        "secret.github_token",
        FindingCategory.CREDENTIAL,
        "A GitHub-style access token was detected.",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", _FLAGS),
        0.68,
        "CREDENTIAL",
        priority=90,
    ),
    _SensitivePattern(
        "secret.github_fine_grained_token",
        FindingCategory.CREDENTIAL,
        "A fine-grained GitHub-style access token was detected.",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", _FLAGS),
        0.68,
        "CREDENTIAL",
        priority=90,
    ),
    _SensitivePattern(
        "secret.aws_access_key",
        FindingCategory.CREDENTIAL,
        "An AWS access-key identifier was detected.",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", _FLAGS),
        0.68,
        "CREDENTIAL",
        priority=90,
    ),
    _SensitivePattern(
        "secret.slack_token",
        FindingCategory.CREDENTIAL,
        "A Slack-style token was detected.",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", _FLAGS),
        0.68,
        "CREDENTIAL",
        priority=90,
    ),
    _SensitivePattern(
        "secret.api_key",
        FindingCategory.CREDENTIAL,
        "A common API-key pattern was detected.",
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|sk_(?:live|test)_[A-Za-z0-9]{12,})\b", _FLAGS),
        0.68,
        "CREDENTIAL",
        priority=90,
    ),
    _SensitivePattern(
        "secret.jwt",
        FindingCategory.CREDENTIAL,
        "A JWT-shaped credential was detected.",
        re.compile(r"\beyj[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b", _FLAGS),
        0.58,
        "CREDENTIAL",
        priority=85,
    ),
    _SensitivePattern(
        "secret.bearer_token",
        FindingCategory.CREDENTIAL,
        "A bearer credential was detected.",
        re.compile(r"\bBearer\s+(?P<value>[A-Za-z0-9._~+/=-]{12,})\b", _FLAGS),
        0.58,
        "CREDENTIAL",
        priority=80,
        group="value",
    ),
    _SensitivePattern(
        "secret.credential_assignment",
        FindingCategory.CREDENTIAL,
        "A value assigned to a credential-like field was detected.",
        re.compile(
            r"\b(?:password|passwd|pwd|api[_-]?key|secret|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|aws[_-]?secret[_-]?access[_-]?key|aws[_-]?session[_-]?token)\b\s*[:=]\s*(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,;\r\n]+)",
            _FLAGS,
        ),
        0.58,
        "CREDENTIAL",
        priority=70,
        group="value",
    ),
    _SensitivePattern(
        "pii.ssn",
        FindingCategory.SENSITIVE_PERSONAL_DATA,
        "A US Social Security number-shaped value was detected.",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        0.42,
        "PERSONAL_DATA",
        priority=50,
    ),
    _SensitivePattern(
        "pii.payment_card",
        FindingCategory.SENSITIVE_PERSONAL_DATA,
        "A payment-card-shaped value passing a checksum was detected.",
        re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
        0.48,
        "PERSONAL_DATA",
        priority=50,
        luhn_check=True,
    ),
    _SensitivePattern(
        "pii.email",
        FindingCategory.SENSITIVE_PERSONAL_DATA,
        "An email address was detected.",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", _FLAGS),
        0.18,
        "PERSONAL_DATA",
        priority=40,
    ),
    _SensitivePattern(
        "pii.phone",
        FindingCategory.SENSITIVE_PERSONAL_DATA,
        "A phone-number-shaped value was detected.",
        re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)"),
        0.22,
        "PERSONAL_DATA",
        priority=30,
    ),
)


_ENTITY = re.compile(r"&(?:#(?:x[0-9a-f]+|[0-9]+)|[a-z][a-z0-9]+);", re.IGNORECASE)
_DEFENSIVE_CONTEXT = re.compile(r"\b(?:educational|training|example|explains?|describes?|quoted|unsafe|never\s+run|do\s+not\s+run)\b", re.I)
_DIRECTIVE_PREFIX = re.compile(r"\b(?:please\s+|run\s+|execute\s+|you\s+must\s+|you\s+should\s+)$", re.I)


def _normalize_with_spans(content: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize text while retaining a map back to exact original spans."""
    output: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(content):
        entity = _ENTITY.match(content, index)
        end = entity.end() if entity else index + 1
        source = html.unescape(entity.group(0)) if entity else content[index]
        folded = unicodedata.normalize("NFKC", source).casefold()
        if not (len(source) == 1 and source in _ZERO_WIDTH):
            output.extend(folded)
            spans.extend([(index, end)] * len(folded))
        index = end
    return "".join(output), spans


def _normalize_for_detection(content: str) -> str:
    return _normalize_with_spans(content)[0]


def _original_span(spans: list[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    return spans[start][0], spans[end - 1][1]


def _is_luhn_valid(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _detection_findings(normalized: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for rule in _DETECTION_RULES:
        matches = list(rule.pattern.finditer(normalized))
        if matches:
            contexts = []
            for match in matches:
                context = normalized[max(0, match.start() - 100): min(len(normalized), match.end() + 100)]
                prefix = normalized[max(0, match.start() - 45):match.start()]
                educational = bool(_DEFENSIVE_CONTEXT.search(context)) and not _DIRECTIVE_PREFIX.search(prefix)
                contexts.append(FindingContext.EDUCATIONAL if educational else FindingContext.DIRECT)
            findings.append(
                SecurityFinding(
                    category=rule.category,
                    rule_id=rule.rule_id,
                    description=rule.description,
                    occurrences=len(matches),
                    score_weight=rule.weight,
                    context=FindingContext.DIRECT if FindingContext.DIRECT in contexts else FindingContext.EDUCATIONAL,
                )
            )
    return findings


def _sensitive_findings_and_redactions(
    content: str, normalized: str, spans: list[tuple[int, int]],
) -> tuple[list[SecurityFinding], list[_Redaction]]:
    findings: list[SecurityFinding] = []
    redactions: list[_Redaction] = []

    for rule in _SENSITIVE_PATTERNS:
        matches = []
        for match in rule.pattern.finditer(normalized):
            if rule.luhn_check and not _is_luhn_valid(match.group(0)):
                continue
            if rule.rule_id == "pii.phone":
                digits = "".join(char for char in match.group(0) if char.isdigit())
                if re.fullmatch(r"\d{3}-\d{2}-\d{4}", match.group(0)) or _is_luhn_valid(digits):
                    continue
            normalized_span = match.span(rule.group) if rule.group else match.span()
            value_span = _original_span(spans, *normalized_span)
            matches.append(value_span)

        if not matches:
            continue

        findings.append(
            SecurityFinding(
                category=rule.category,
                rule_id=rule.rule_id,
                description=rule.description,
                occurrences=len(matches),
                score_weight=rule.weight,
            )
        )
        redactions.extend(
            _Redaction(start, end, rule.label, rule.priority, frozenset({rule.category}))
            for start, end in matches
            if end > start
        )

    return findings, redactions


def _merge_redactions(candidates: list[_Redaction]) -> list[_Redaction]:
    ordered = sorted(candidates, key=lambda item: (item.start, -item.priority, -item.end))
    merged: list[_Redaction] = []
    for candidate in ordered:
        if merged and candidate.start < merged[-1].end:
            previous = merged[-1]
            label = previous.label if previous.label == candidate.label else "SENSITIVE_DATA"
            merged[-1] = _Redaction(
                previous.start,
                max(previous.end, candidate.end),
                label,
                max(previous.priority, candidate.priority),
                previous.categories | candidate.categories,
            )
        else:
            merged.append(candidate)

    return merged


def _redact(content: str, candidates: list[_Redaction]) -> str:
    if not candidates:
        return content

    merged = _merge_redactions(candidates)
    chunks: list[str] = []
    cursor = 0
    for item in merged:
        chunks.append(content[cursor : item.start])
        chunks.append(f"[REDACTED_{item.label}]")
        cursor = item.end
    chunks.append(content[cursor:])
    return "".join(chunks)


def _risk_score(findings: tuple[SecurityFinding, ...]) -> float:
    total = sum(
        (finding.score_weight + min(0.05 * (finding.occurrences - 1), 0.10))
        * (0.1 if finding.context is FindingContext.EDUCATIONAL else 1.0)
        for finding in findings
    )
    return round(min(total, 1.0), 3)


def _severity(score: float) -> SecuritySeverity:
    if score >= 0.85:
        return SecuritySeverity.CRITICAL
    if score >= 0.65:
        return SecuritySeverity.HIGH
    if score >= 0.40:
        return SecuritySeverity.MEDIUM
    if score >= 0.15:
        return SecuritySeverity.LOW
    return SecuritySeverity.INFO


def inspect_text(content: str, *, kind: str = "TEXT") -> InspectionResult:
    """Inspect text deterministically and return findings plus redacted text.

    The input is processed in memory only. Findings intentionally omit matched
    values and offsets so they are safe to copy into later event evidence.
    """

    try:
        request = InspectionRequest(content=content, kind=kind)
    except InspectionValidationError:
        # The exception traceback keeps this frame. Scrub the submitted values
        # from its locals before allowing the sanitized error to escape.
        del content, kind
        raise
    normalized, spans = _normalize_with_spans(request.content)
    findings = _detection_findings(normalized)
    sensitive_findings, redactions = _sensitive_findings_and_redactions(request.content, normalized, spans)
    findings.extend(sensitive_findings)
    findings.sort(key=lambda item: (-item.score_weight, item.rule_id))
    stable_findings = tuple(findings)
    score = _risk_score(stable_findings)
    redaction_counts: dict[str, int] = {}
    merged_redactions = _merge_redactions(redactions)
    unique_redactions = {
        (item.start, item.end, category)
        for item in merged_redactions
        for category in item.categories
    }
    for _start, _end, category in unique_redactions:
        redaction_counts[category.value] = redaction_counts.get(category.value, 0) + 1

    return InspectionResult(
        rules_version=RULES_VERSION,
        risk_score=score,
        severity=_severity(score),
        findings=stable_findings,
        redacted_content=_redact(request.content, redactions),
        redaction_counts=redaction_counts,
    )
