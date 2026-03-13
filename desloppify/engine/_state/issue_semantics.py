"""Canonical issue taxonomy and semantic helpers.

This module owns the semantic meaning of persisted issues. Callers should use
these helpers instead of branching on detector strings or ID prefixes.
"""

from __future__ import annotations

from typing import Any, Mapping, TypeAlias

IssueKind: TypeAlias = str
IssueOrigin: TypeAlias = str

MECHANICAL_FINDING = "mechanical_finding"
REVIEW_FINDING = "review_finding"
CONCERN_FINDING = "concern_finding"
REVIEW_REQUEST = "review_request"

SCAN_ORIGIN = "scan"
REVIEW_IMPORT_ORIGIN = "review_import"
SYNTHETIC_REQUEST_ORIGIN = "synthetic_request"

ISSUE_KINDS: frozenset[str] = frozenset(
    {
        MECHANICAL_FINDING,
        REVIEW_FINDING,
        CONCERN_FINDING,
        REVIEW_REQUEST,
    }
)
ISSUE_ORIGINS: frozenset[str] = frozenset(
    {
        SCAN_ORIGIN,
        REVIEW_IMPORT_ORIGIN,
        SYNTHETIC_REQUEST_ORIGIN,
    }
)

# Mechanical detectors that remain actionable work but stay excluded from
# detector-side scoring rules.
SCORING_EXCLUDED_DETECTORS: frozenset[str] = frozenset(
    {
        "concerns",
        "review",
        "subjective_review",
        "uncalled_functions",
        "unused_enums",
        "signature",
        "stale_wontfix",
    }
)


def infer_issue_kind(
    detector: object,
    *,
    detail: Mapping[str, Any] | None = None,
) -> IssueKind:
    """Infer a persisted issue kind from legacy detector/detail fields."""
    detector_name = str(detector or "").strip()
    detail_dict = detail if isinstance(detail, Mapping) else {}

    if detector_name == "review":
        return REVIEW_FINDING
    if detector_name == "concerns":
        return CONCERN_FINDING
    if detector_name in {"subjective_review", "subjective_assessment", "holistic_review"}:
        return REVIEW_REQUEST
    # Legacy imported confirmed concerns sometimes carried review-like detail;
    # keep explicit concern markers mapped to concern findings.
    if str(detail_dict.get("concern_verdict", "")).strip().lower() == "confirmed":
        return CONCERN_FINDING
    return MECHANICAL_FINDING


def infer_issue_origin(
    detector: object,
    *,
    detail: Mapping[str, Any] | None = None,
) -> IssueOrigin:
    """Infer provenance for a persisted issue."""
    detector_name = str(detector or "").strip()
    detail_dict = detail if isinstance(detail, Mapping) else {}

    if detector_name == "review":
        return REVIEW_IMPORT_ORIGIN
    if detector_name == "concerns":
        verdict = str(detail_dict.get("concern_verdict", "")).strip().lower()
        return REVIEW_IMPORT_ORIGIN if verdict == "confirmed" else SCAN_ORIGIN
    if detector_name in {"subjective_review", "subjective_assessment", "holistic_review"}:
        return SYNTHETIC_REQUEST_ORIGIN
    return SCAN_ORIGIN


def normalized_issue_kind(issue: Mapping[str, Any]) -> IssueKind:
    """Return the canonical issue kind, inferring from legacy data when needed."""
    raw_kind = str(issue.get("issue_kind", "")).strip()
    if raw_kind in ISSUE_KINDS:
        return raw_kind
    return infer_issue_kind(issue.get("detector", ""), detail=_detail_dict(issue))


def normalized_issue_origin(issue: Mapping[str, Any]) -> IssueOrigin:
    """Return the canonical issue origin, inferring from legacy data when needed."""
    raw_origin = str(issue.get("origin", "")).strip()
    if raw_origin in ISSUE_ORIGINS:
        return raw_origin
    return infer_issue_origin(issue.get("detector", ""), detail=_detail_dict(issue))


def ensure_issue_semantics(issue: dict[str, Any]) -> None:
    """Populate canonical semantic fields in-place."""
    issue["issue_kind"] = normalized_issue_kind(issue)
    issue["origin"] = normalized_issue_origin(issue)


def is_objective_finding(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_kind(issue) == MECHANICAL_FINDING


def is_triage_finding(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_kind(issue) in {REVIEW_FINDING, CONCERN_FINDING}


def is_review_finding(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_kind(issue) == REVIEW_FINDING


def is_concern_finding(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_kind(issue) == CONCERN_FINDING


def is_review_request(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_kind(issue) == REVIEW_REQUEST


def is_non_objective_issue(issue: Mapping[str, Any]) -> bool:
    return not is_objective_finding(issue)


def counts_toward_objective_backlog(issue: Mapping[str, Any]) -> bool:
    return is_objective_finding(issue)


def is_import_only_issue(issue: Mapping[str, Any]) -> bool:
    return normalized_issue_origin(issue) == REVIEW_IMPORT_ORIGIN


def is_scoring_excluded_detector(detector: object) -> bool:
    detector_name = str(detector or "").strip()
    return detector_name in SCORING_EXCLUDED_DETECTORS


def _detail_dict(issue: Mapping[str, Any]) -> Mapping[str, Any]:
    detail = issue.get("detail", {})
    return detail if isinstance(detail, Mapping) else {}


__all__ = [
    "CONCERN_FINDING",
    "ISSUE_KINDS",
    "ISSUE_ORIGINS",
    "MECHANICAL_FINDING",
    "REVIEW_FINDING",
    "REVIEW_IMPORT_ORIGIN",
    "REVIEW_REQUEST",
    "SCAN_ORIGIN",
    "SCORING_EXCLUDED_DETECTORS",
    "SYNTHETIC_REQUEST_ORIGIN",
    "counts_toward_objective_backlog",
    "ensure_issue_semantics",
    "infer_issue_kind",
    "infer_issue_origin",
    "is_concern_finding",
    "is_import_only_issue",
    "is_non_objective_issue",
    "is_objective_finding",
    "is_review_finding",
    "is_review_request",
    "is_scoring_excluded_detector",
    "is_triage_finding",
    "normalized_issue_kind",
    "normalized_issue_origin",
]
