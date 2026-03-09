"""Batch payload validation and normalization helpers."""

from __future__ import annotations

from typing import cast

from desloppify.intelligence.review.feedback_contract import (
    DIMENSION_NOTE_ISSUES_KEY,
    HIGH_SCORE_ISSUES_NOTE_THRESHOLD,
    LEGACY_DIMENSION_NOTE_ISSUES_KEY,
    LOW_SCORE_ISSUE_THRESHOLD,
)
from desloppify.intelligence.review.importing.contracts_types import (
    ReviewIssuePayload,
)
from desloppify.intelligence.review.importing.contracts_validation import (
    validate_review_issue_payload,
)
from desloppify.intelligence.review.importing.payload import (
    normalize_legacy_findings_alias,
)

from .core_models import (
    BatchDimensionJudgmentPayload,
    BatchDimensionNotePayload,
    BatchIssuePayload,
    BatchQualityPayload,
    NormalizedBatchIssue,
)


def _validate_dimension_note(
    key: str,
    note_raw: object,
) -> tuple[list[object], str, str, str, str]:
    """Validate a single dimension_notes entry and return parsed fields.

    Returns (evidence, impact_scope, fix_scope, confidence, issues_preventing_higher_score).
    Raises ValueError on invalid structure.
    """
    if not isinstance(note_raw, dict):
        raise ValueError(
            f"dimension_notes missing object for assessed dimension: {key}"
        )
    evidence = note_raw.get("evidence")
    impact_scope = note_raw.get("impact_scope")
    fix_scope = note_raw.get("fix_scope")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(
            f"dimension_notes.{key}.evidence must be a non-empty array"
        )
    if not isinstance(impact_scope, str) or not impact_scope.strip():
        raise ValueError(
            f"dimension_notes.{key}.impact_scope must be a non-empty string"
        )
    if not isinstance(fix_scope, str) or not fix_scope.strip():
        raise ValueError(
            f"dimension_notes.{key}.fix_scope must be a non-empty string"
        )

    confidence_raw = str(note_raw.get("confidence", "medium")).strip().lower()
    confidence = (
        confidence_raw if confidence_raw in {"high", "medium", "low"} else "medium"
    )
    issues_note = str(note_raw.get(DIMENSION_NOTE_ISSUES_KEY, "")).strip()
    if not issues_note:
        issues_note = str(note_raw.get(LEGACY_DIMENSION_NOTE_ISSUES_KEY, "")).strip()
    return evidence, impact_scope, fix_scope, confidence, issues_note


def _normalize_abstraction_sub_axes(
    note_raw: dict[str, object],
    abstraction_sub_axes: tuple[str, ...],
) -> dict[str, float]:
    """Extract and clamp abstraction_fitness sub-axis scores from a note."""
    sub_axes_raw = note_raw.get("sub_axes")
    if sub_axes_raw is not None and not isinstance(sub_axes_raw, dict):
        raise ValueError(
            "dimension_notes.abstraction_fitness.sub_axes must be an object"
        )
    if not isinstance(sub_axes_raw, dict):
        return {}

    normalized: dict[str, float] = {}
    for axis in abstraction_sub_axes:
        axis_value = sub_axes_raw.get(axis)
        if axis_value is None:
            continue
        if isinstance(axis_value, bool) or not isinstance(axis_value, int | float):
            raise ValueError(
                f"dimension_notes.abstraction_fitness.sub_axes.{axis} "
                "must be numeric"
            )
        normalized[axis] = round(
            max(0.0, min(100.0, float(axis_value))),
            1,
        )
    return normalized


def _validate_dimension_judgment(
    key: str,
    raw: object,
    *,
    log_fn,
) -> BatchDimensionJudgmentPayload | None:
    """Validate a single dimension_judgment entry. Returns cleaned payload or None."""
    if not isinstance(raw, dict):
        log_fn(f"  dimension_judgment.{key}: expected object, skipping")
        return None

    strengths_raw = raw.get("strengths")
    if not isinstance(strengths_raw, list):
        strengths: list[str] = []
    else:
        strengths = [
            str(s).strip()
            for s in strengths_raw[:5]
            if isinstance(s, str) and str(s).strip()
        ]

    issue_character = ""
    ic_raw = raw.get("issue_character")
    if isinstance(ic_raw, str) and ic_raw.strip():
        issue_character = ic_raw.strip()
    else:
        log_fn(f"  dimension_judgment.{key}.issue_character: missing or empty")

    score_rationale = ""
    sr_raw = raw.get("score_rationale")
    if isinstance(sr_raw, str) and sr_raw.strip():
        score_rationale = sr_raw.strip()
        if len(score_rationale) < 50:
            log_fn(
                f"  dimension_judgment.{key}.score_rationale: "
                f"too short ({len(score_rationale)} chars, want ≥50)"
            )
    else:
        log_fn(f"  dimension_judgment.{key}.score_rationale: missing or empty")

    if not issue_character and not score_rationale and not strengths:
        return None

    result: BatchDimensionJudgmentPayload = {}
    if strengths:
        result["strengths"] = strengths
    if issue_character:
        result["issue_character"] = issue_character
    if score_rationale:
        result["score_rationale"] = score_rationale
    return result


def _normalize_issues(
    raw_issues: object,
    dimension_notes: dict[str, BatchDimensionNotePayload],
    *,
    max_batch_issues: int,
    allowed_dims: set[str],
    low_score_dimensions: set[str] | None = None,
) -> list[NormalizedBatchIssue]:
    """Validate and normalize the issues array from a batch payload."""
    if not isinstance(raw_issues, list):
        raise ValueError("issues must be an array")

    issues: list[NormalizedBatchIssue] = []
    errors: list[str] = []
    for idx, item in enumerate(raw_issues):
        issue: ReviewIssuePayload | None
        issue, issue_errors = validate_review_issue_payload(
            item,
            label=f"issues[{idx}]",
            allowed_dimensions=allowed_dims,
            allow_dismissed=False,
        )
        if issue_errors:
            errors.extend(issue_errors)
            continue
        if issue is None:
            raise ValueError(
                "batch issue payload missing after validation succeeded"
            )

        dim = issue["dimension"]
        note = dimension_notes.get(dim, {})
        impact_scope = str(
            (item if isinstance(item, dict) else {}).get(
                "impact_scope", note.get("impact_scope", "")
            )
        ).strip()
        fix_scope = str(
            (item if isinstance(item, dict) else {}).get(
                "fix_scope", note.get("fix_scope", "")
            )
        ).strip()
        if not impact_scope or not fix_scope:
            errors.append(
                f"issues[{idx}] requires impact_scope and fix_scope "
                "(or dimension_notes defaults)"
            )
            continue
        issues.append(
            NormalizedBatchIssue(
                dimension=issue["dimension"],
                identifier=issue["identifier"],
                summary=issue["summary"],
                confidence=issue["confidence"],
                suggestion=issue["suggestion"],
                related_files=list(issue.get("related_files", [])),
                evidence=list(issue.get("evidence", [])),
                impact_scope=impact_scope,
                fix_scope=fix_scope,
                reasoning=str(issue.get("reasoning", "")),
                evidence_lines=list(issue.get("evidence_lines", []))
                if isinstance(issue.get("evidence_lines"), list)
                else None,
            )
        )
    if errors:
        visible = errors[:10]
        remaining = len(errors) - len(visible)
        if remaining > 0:
            visible.append(f"... {remaining} additional issue schema error(s) omitted")
        raise ValueError("; ".join(visible))
    if len(issues) <= max_batch_issues:
        return issues

    required_dims = set(low_score_dimensions or set())
    if not required_dims:
        return issues[:max_batch_issues]

    # Preserve at least one issue per low-score dimension before trimming.
    selected: list[NormalizedBatchIssue] = []
    selected_indexes: set[int] = set()
    covered: set[str] = set()
    for idx, issue in enumerate(issues):
        if len(selected) >= max_batch_issues:
            break
        dim = issue.dimension.strip()
        if dim not in required_dims or dim in covered:
            continue
        selected.append(issue)
        selected_indexes.add(idx)
        covered.add(dim)

    for idx, issue in enumerate(issues):
        if len(selected) >= max_batch_issues:
            break
        if idx in selected_indexes:
            continue
        selected.append(issue)
    return selected


def _low_score_dimensions(assessments: dict[str, float]) -> set[str]:
    """Return assessed dimensions requiring explicit defect issues."""
    return {
        dim
        for dim, score in assessments.items()
        if score < LOW_SCORE_ISSUE_THRESHOLD
    }


def _enforce_low_score_issues(
    *,
    assessments: dict[str, float],
    issues: list[NormalizedBatchIssue],
) -> None:
    """Fail closed when low scores do not report explicit issues."""
    required_dims = _low_score_dimensions(assessments)
    if not required_dims:
        return
    issue_dims = {
        issue.dimension.strip() for issue in issues
    }
    missing = sorted(dim for dim in required_dims if dim not in issue_dims)
    if not missing:
        return
    joined = ", ".join(missing)
    raise ValueError(
        "low-score dimensions must include at least one explicit issue: "
        f"{joined} (threshold {LOW_SCORE_ISSUE_THRESHOLD:.1f})"
    )


def _compute_batch_quality(
    assessments: dict[str, float],
    issues: list[NormalizedBatchIssue],
    dimension_notes: dict[str, BatchDimensionNotePayload],
    high_score_missing_issue_note: float,
    expected_dimensions: int,
) -> BatchQualityPayload:
    """Compute quality metrics for a single batch result."""
    return {
        "dimension_coverage": round(
            len(assessments) / max(expected_dimensions, 1),
            3,
        ),
        "evidence_density": round(
            sum(len(note.get("evidence", [])) for note in dimension_notes.values())
            / max(len(issues), 1),
            3,
        ),
        "high_score_missing_issue_note": high_score_missing_issue_note,
    }


def normalize_batch_result(
    payload: dict[str, object],
    allowed_dims: set[str],
    *,
    max_batch_issues: int,
    abstraction_sub_axes: tuple[str, ...],
    log_fn=lambda _msg: None,
) -> tuple[
    dict[str, float],
    list[BatchIssuePayload],
    dict[str, BatchDimensionNotePayload],
    dict[str, BatchDimensionJudgmentPayload],
    BatchQualityPayload,
]:
    """Validate and normalize one batch payload."""
    if "assessments" not in payload:
        raise ValueError("payload missing required key: assessments")
    key_error = normalize_legacy_findings_alias(
        payload,
        missing_issues_error="payload missing required key: issues",
    )
    if key_error is not None:
        raise ValueError(key_error)

    raw_assessments = payload.get("assessments")
    if not isinstance(raw_assessments, dict):
        raise ValueError("assessments must be an object")

    raw_dimension_notes = payload.get("dimension_notes", {})
    if not isinstance(raw_dimension_notes, dict):
        raise ValueError("dimension_notes must be an object")

    assessments: dict[str, float] = {}
    dimension_notes: dict[str, BatchDimensionNotePayload] = {}
    high_score_missing_issue_note = 0.0
    for key, value in raw_assessments.items():
        if not isinstance(key, str) or not key:
            continue
        if key not in allowed_dims:
            continue
        if isinstance(value, bool):
            continue
        if not isinstance(value, int | float):
            continue
        score = round(max(0.0, min(100.0, float(value))), 1)

        note_raw = raw_dimension_notes.get(key)
        evidence, impact_scope, fix_scope, confidence, issues_note = (
            _validate_dimension_note(key, note_raw)
        )
        if not isinstance(note_raw, dict):
            raise ValueError(
                f"dimension_notes missing object for assessed dimension: {key}"
            )
        if score > HIGH_SCORE_ISSUES_NOTE_THRESHOLD and not issues_note:
            high_score_missing_issue_note += 1

        normalized_sub_axes: dict[str, float] = {}
        if key == "abstraction_fitness":
            normalized_sub_axes = _normalize_abstraction_sub_axes(
                note_raw, abstraction_sub_axes
            )

        assessments[key] = score
        dimension_notes[key] = {
            "evidence": [str(item).strip() for item in evidence if str(item).strip()],
            "impact_scope": impact_scope.strip(),
            "fix_scope": fix_scope.strip(),
            "confidence": confidence,
            "issues_preventing_higher_score": issues_note,
        }
        if normalized_sub_axes:
            dimension_notes[key]["sub_axes"] = normalized_sub_axes

    raw_judgment = payload.get("dimension_judgment", {})
    dimension_judgment: dict[str, BatchDimensionJudgmentPayload] = {}
    if isinstance(raw_judgment, dict):
        for key in assessments:
            judgment_raw = raw_judgment.get(key)
            if judgment_raw is None:
                continue
            validated = _validate_dimension_judgment(key, judgment_raw, log_fn=log_fn)
            if validated is not None:
                dimension_judgment[key] = validated

    issues = _normalize_issues(
        payload.get("issues"),
        dimension_notes,
        max_batch_issues=max_batch_issues,
        allowed_dims=allowed_dims,
        low_score_dimensions=_low_score_dimensions(assessments),
    )
    _enforce_low_score_issues(assessments=assessments, issues=issues)

    quality = _compute_batch_quality(
        assessments,
        issues,
        dimension_notes,
        high_score_missing_issue_note,
        expected_dimensions=len(allowed_dims),
    )
    return (
        assessments,
        [issue.to_payload() for issue in issues],
        dimension_notes,
        dimension_judgment,
        quality,
    )


__all__ = ["normalize_batch_result"]
