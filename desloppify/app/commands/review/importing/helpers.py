"""Import/reporting helpers for holistic review command flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.intelligence.review.feedback_contract import (
    ASSESSMENT_FEEDBACK_THRESHOLD,
    LOW_SCORE_ISSUE_THRESHOLD,
)
from desloppify.intelligence.review.importing.contracts_models import (
    AssessmentImportPolicyModel,
)
from desloppify.intelligence.review.importing.contracts_types import (
    ReviewImportPayload,
)

from .output import (
    print_assessment_mode_banner,
    print_assessments_summary,
    print_import_load_errors,
    print_open_review_summary,
    print_review_import_scores_and_integrity,
    print_skipped_validation_details,
)
from .parse import (
    _validate_assessment_feedback,
    _validate_holistic_issues_schema,
    resolve_override_context,
)
from .policy import (
    ASSESSMENT_POLICY_KEY,
    ATTESTED_EXTERNAL_ATTEST_EXAMPLE,
    apply_assessment_import_policy,
    assessment_mode_label,
    assessment_policy_from_payload,
    assessment_policy_model_from_payload,
)


class ImportPayloadLoadError(ValueError):
    """Raised when review import payload parsing/validation fails."""

    def __init__(self, errors: list[str]) -> None:
        cleaned = [str(error).strip() for error in errors if str(error).strip()]
        self.errors = cleaned
        message = "; ".join(cleaned) if cleaned else "import payload validation failed"
        super().__init__(message)


@dataclass(frozen=True)
class ImportLoadConfig:
    """Config bundle for import payload parsing/validation options."""

    lang_name: str | None = None
    allow_partial: bool = False
    trusted_assessment_source: bool = False
    trusted_assessment_label: str | None = None
    attested_external: bool = False
    manual_override: bool = False
    manual_attest: str | None = None


def _config_and_legacy_kwargs_conflict(
    *,
    config: ImportLoadConfig | None,
    lang_name: str | None,
    allow_partial: bool,
    trusted_assessment_source: bool,
    trusted_assessment_label: str | None,
    attested_external: bool,
    manual_override: bool,
    manual_attest: str | None,
) -> list[str]:
    """Return config/kwargs conflict errors to keep import API precedence explicit."""
    if config is None:
        return []

    collisions: list[str] = []
    if lang_name is not None:
        collisions.append("lang_name")
    if allow_partial:
        collisions.append("allow_partial")
    if trusted_assessment_source:
        collisions.append("trusted_assessment_source")
    if trusted_assessment_label is not None:
        collisions.append("trusted_assessment_label")
    if attested_external:
        collisions.append("attested_external")
    if manual_override:
        collisions.append("manual_override")
    if manual_attest is not None:
        collisions.append("manual_attest")

    if not collisions:
        return []

    joined = ", ".join(collisions)
    return [
        "Import config conflict: pass either `config=ImportLoadConfig(...)` "
        f"or legacy kwargs, not both (conflicting args: {joined})."
    ]


def _resolve_import_load_config(
    *,
    config: ImportLoadConfig | None,
    lang_name: str | None,
    allow_partial: bool,
    trusted_assessment_source: bool,
    trusted_assessment_label: str | None,
    attested_external: bool,
    manual_override: bool,
    manual_attest: str | None,
) -> tuple[ImportLoadConfig | None, list[str]]:
    """Resolve legacy kwargs into ImportLoadConfig while preserving conflict checks."""
    conflict_errors = _config_and_legacy_kwargs_conflict(
        config=config,
        lang_name=lang_name,
        allow_partial=allow_partial,
        trusted_assessment_source=trusted_assessment_source,
        trusted_assessment_label=trusted_assessment_label,
        attested_external=attested_external,
        manual_override=manual_override,
        manual_attest=manual_attest,
    )
    if conflict_errors:
        return None, conflict_errors
    if config is not None:
        return config, []
    return (
        ImportLoadConfig(
            lang_name=lang_name,
            allow_partial=allow_partial,
            trusted_assessment_source=trusted_assessment_source,
            trusted_assessment_label=trusted_assessment_label,
            attested_external=attested_external,
            manual_override=manual_override,
            manual_attest=manual_attest,
        ),
        [],
    )


def _normalize_issues(payload: dict[str, Any], *, errors: list[str]) -> list[Any]:
    """Normalize the required ``issues`` key to a list."""
    issues = payload.get("issues")
    if isinstance(issues, list):
        return issues
    errors.append("issues must be a JSON array")
    return []


def _normalize_optional_mapping(
    payload: dict[str, Any],
    *,
    key: str,
    errors: list[str],
    type_error: str,
) -> dict[str, Any]:
    """Normalize optional object fields to dict values."""
    value = payload.get(key)
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    errors.append(type_error)
    return {}


def _normalize_reviewed_files(payload: dict[str, Any], *, errors: list[str]) -> list[str]:
    """Normalize optional reviewed-files list."""
    reviewed_files = payload.get("reviewed_files")
    if reviewed_files is None:
        return []
    if not isinstance(reviewed_files, list):
        errors.append("reviewed_files must be an array when provided")
        return []
    return [
        str(item).strip()
        for item in reviewed_files
        if isinstance(item, str) and str(item).strip()
    ]


def _normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional assessment policy blob."""
    policy = payload.get(ASSESSMENT_POLICY_KEY)
    if isinstance(policy, dict):
        return policy
    return AssessmentImportPolicyModel().to_dict()


def _normalize_import_payload_shape(
    payload: dict[str, Any],
) -> tuple[ReviewImportPayload | None, list[str]]:
    """Normalize payload into required-key contract with strict type checks."""
    errors: list[str] = []
    issues = _normalize_issues(payload, errors=errors)
    assessments = _normalize_optional_mapping(
        payload,
        key="assessments",
        errors=errors,
        type_error="assessments must be an object when provided",
    )
    normalized_reviewed_files = _normalize_reviewed_files(payload, errors=errors)
    review_scope = _normalize_optional_mapping(
        payload,
        key="review_scope",
        errors=errors,
        type_error="review_scope must be an object when provided",
    )
    provenance = _normalize_optional_mapping(
        payload,
        key="provenance",
        errors=errors,
        type_error="provenance must be an object when provided",
    )
    dimension_notes = _normalize_optional_mapping(
        payload,
        key="dimension_notes",
        errors=errors,
        type_error="dimension_notes must be an object when provided",
    )
    normalized_policy = _normalize_policy(payload)
    if errors:
        return None, errors
    return (
        {
            "issues": issues,
            "assessments": assessments,
            "reviewed_files": normalized_reviewed_files,
            "review_scope": review_scope,
            "provenance": provenance,
            "dimension_notes": dimension_notes,
            ASSESSMENT_POLICY_KEY: normalized_policy,
        },
        [],
    )


def _load_raw_import_payload(import_file: str) -> tuple[Any | None, list[str]]:
    """Read raw JSON payload from disk."""
    issues_path = Path(import_file)
    if not issues_path.exists():
        return None, [f"file not found: {import_file}"]
    try:
        return json.loads(issues_path.read_text()), []
    except (json.JSONDecodeError, OSError) as exc:
        return None, [f"error reading issues: {exc}"]


def _normalize_raw_import_payload(
    raw_payload: Any,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize top-level import document shape."""
    payload = raw_payload
    if isinstance(payload, list):
        payload = {"issues": payload}
    if not isinstance(payload, dict):
        return None, ["issues file must contain a JSON array or object"]
    if "issues" not in payload:
        return None, ["issues object must contain a 'issues' key"]
    return payload, []


def _validate_assessment_flag_combo(
    *,
    options: ImportLoadConfig,
    override_enabled: bool,
) -> str | None:
    """Return policy-flag validation error, if any."""
    if options.attested_external and override_enabled:
        return "--attested-external cannot be combined with --manual-override"
    if options.attested_external and options.allow_partial:
        return (
            "--attested-external cannot be combined with --allow-partial; "
            "attested score imports require fully valid issues payloads"
        )
    if override_enabled and options.allow_partial:
        return (
            "--manual-override cannot be combined with --allow-partial; "
            "manual score imports require fully valid issues payloads"
        )
    return None


def _apply_assessment_policy_checked(
    *,
    normalized_issues_data: ReviewImportPayload,
    import_file: str,
    options: ImportLoadConfig,
    override_enabled: bool,
    override_attest: str | None,
) -> tuple[ReviewImportPayload | None, list[str]]:
    """Apply assessment trust policy and propagate policy validation failures."""
    issues_data, policy_errors = apply_assessment_import_policy(
        normalized_issues_data,
        import_file=import_file,
        attested_external=options.attested_external,
        attested_attest=override_attest,
        manual_override=override_enabled,
        manual_attest=override_attest,
        trusted_assessment_source=options.trusted_assessment_source,
        trusted_assessment_label=options.trusted_assessment_label,
    )
    if policy_errors:
        return None, policy_errors
    if issues_data is None:
        raise ValueError(
            "assessment import policy returned no payload without reporting errors"
        )
    return issues_data, []


def _manual_override_attest_errors(
    *,
    override_enabled: bool,
    override_attest: str | None,
) -> list[str]:
    """Enforce attestation requirement when manual override is active."""
    if not override_enabled:
        return []
    if isinstance(override_attest, str) and override_attest.strip():
        return []
    return ["--manual-override requires --attest"]


def _validate_assessment_feedback_requirements(
    *,
    issues_data: ReviewImportPayload,
    override_enabled: bool,
    override_attest: str | None,
) -> list[str]:
    """Validate assessment-to-feedback consistency constraints."""
    missing_feedback, missing_low_score_issues = _validate_assessment_feedback(
        issues_data
    )
    if missing_low_score_issues:
        attest_errors = _manual_override_attest_errors(
            override_enabled=override_enabled,
            override_attest=override_attest,
        )
        if attest_errors:
            return attest_errors
        if override_enabled:
            return []
        return [
            f"assessments below {LOW_SCORE_ISSUE_THRESHOLD:.1f} must include at "
            "least one issue for that same dimension with a concrete suggestion. "
            f"Missing: {', '.join(missing_low_score_issues)}"
        ]
    if missing_feedback:
        attest_errors = _manual_override_attest_errors(
            override_enabled=override_enabled,
            override_attest=override_attest,
        )
        if attest_errors:
            return attest_errors
        if override_enabled:
            return []
        return [
            f"assessments below {ASSESSMENT_FEEDBACK_THRESHOLD:.1f} must include explicit feedback "
            "(issue with same dimension and non-empty suggestion, or "
            "dimension_notes evidence for that dimension). "
            f"Missing: {', '.join(missing_feedback)}"
        ]
    return []


def _schema_validation_errors(
    *,
    issues_data: ReviewImportPayload,
    lang_name: str | None,
    allow_partial: bool,
) -> list[str]:
    """Return schema validation errors visible to users."""
    schema_errors = _validate_holistic_issues_schema(
        issues_data,
        lang_name=lang_name,
    )
    if not schema_errors or allow_partial:
        return []
    visible_errors = schema_errors[:10]
    remaining = len(schema_errors) - len(visible_errors)
    errors = [
        "issues schema validation failed for holistic import. "
        "Fix payload or rerun with --allow-partial to continue."
    ]
    errors.extend(visible_errors)
    if remaining > 0:
        errors.append(f"... {remaining} additional schema error(s) omitted")
    return errors


def _parse_and_validate_import(
    import_file: str,
    *,
    config: ImportLoadConfig,
) -> tuple[ReviewImportPayload | None, list[str]]:
    """Load, parse, and validate a review import file."""
    options = config
    raw_payload, read_errors = _load_raw_import_payload(import_file)
    if read_errors:
        return None, read_errors
    normalized_payload, payload_errors = _normalize_raw_import_payload(raw_payload)
    if payload_errors:
        return None, payload_errors
    if normalized_payload is None:
        raise ValueError("import payload normalization failed without validation errors")

    normalized_issues_data, shape_errors = _normalize_import_payload_shape(
        normalized_payload
    )
    if shape_errors:
        return None, shape_errors
    if normalized_issues_data is None:
        raise ValueError(
            "normalized import payload missing after successful shape validation"
        )

    override_enabled, override_attest = resolve_override_context(
        manual_override=options.manual_override,
        manual_attest=options.manual_attest,
    )
    flag_error = _validate_assessment_flag_combo(
        options=options,
        override_enabled=override_enabled,
    )
    if flag_error:
        return None, [flag_error]

    issues_data, policy_errors = _apply_assessment_policy_checked(
        normalized_issues_data=normalized_issues_data,
        import_file=import_file,
        options=options,
        override_enabled=override_enabled,
        override_attest=override_attest,
    )
    if policy_errors:
        return None, policy_errors
    if issues_data is None:
        raise ValueError(
            "assessment import policy returned no payload without reporting errors"
        )

    feedback_errors = _validate_assessment_feedback_requirements(
        issues_data=issues_data,
        override_enabled=override_enabled,
        override_attest=override_attest,
    )
    if feedback_errors:
        return None, feedback_errors

    schema_errors = _schema_validation_errors(
        issues_data=issues_data,
        lang_name=options.lang_name,
        allow_partial=options.allow_partial,
    )
    if schema_errors:
        return None, schema_errors
    return issues_data, []


def load_import_issues_data(
    import_file: str,
    *,
    config: ImportLoadConfig | None = None,
    colorize_fn=None,
    lang_name: str | None = None,
    allow_partial: bool = False,
    trusted_assessment_source: bool = False,
    trusted_assessment_label: str | None = None,
    attested_external: bool = False,
    manual_override: bool = False,
    manual_attest: str | None = None,
) -> ReviewImportPayload:
    """Load and normalize review import payload to object format.

    Raises ``ImportPayloadLoadError`` when validation fails.
    """
    _ = colorize_fn
    options, conflict_errors = _resolve_import_load_config(
        config=config,
        lang_name=lang_name,
        allow_partial=allow_partial,
        trusted_assessment_source=trusted_assessment_source,
        trusted_assessment_label=trusted_assessment_label,
        attested_external=attested_external,
        manual_override=manual_override,
        manual_attest=manual_attest,
    )
    if conflict_errors:
        raise ImportPayloadLoadError(conflict_errors)

    if options is None:
        raise ValueError("resolved import options missing after conflict validation")
    data, errors = _parse_and_validate_import(
        import_file,
        config=options,
    )
    if errors:
        raise ImportPayloadLoadError(errors)
    if data is None:
        raise ValueError(
            "import payload missing after parse completed without validation errors"
    )
    return data


def _print_trusted_notice(*, policy_model: AssessmentImportPolicyModel, colorize_fn) -> None:
    packet_path = policy_model.provenance.packet_path.strip() or None
    detail = f" · blind packet {packet_path}" if packet_path else ""
    print(
        colorize_fn(
            f"  Assessment provenance: trusted blind batch artifact{detail}.",
            "dim",
        )
    )


def _print_trusted_internal_notice(
    *,
    policy_model: AssessmentImportPolicyModel,
    colorize_fn,
) -> None:
    count = int(policy_model.assessment_count or 0)
    reason_text = policy_model.reason.strip()
    suffix = f" ({reason_text})" if reason_text else ""
    print(
        colorize_fn(
            f"  Assessment updates applied: {count} dimension(s){suffix}.",
            "dim",
        )
    )


def _print_manual_override_notice(
    *,
    policy_model: AssessmentImportPolicyModel,
    reason: str,
    colorize_fn,
) -> None:
    count = int(policy_model.assessment_count or 0)
    print(
        colorize_fn(
            f"  WARNING: applying {count} assessment update(s) via manual override from untrusted provenance.",
            "yellow",
        )
    )
    if reason:
        print(colorize_fn(f"  Reason: {reason}", "dim"))


def _print_attested_external_notice(
    *,
    policy_model: AssessmentImportPolicyModel,
    reason: str,
    colorize_fn,
) -> None:
    count = int(policy_model.assessment_count or 0)
    print(
        colorize_fn(
            f"  Assessment updates applied via attested external blind review: {count} dimension(s).",
            "dim",
        )
    )
    if reason:
        print(colorize_fn(f"  Reason: {reason}", "dim"))


def _print_issues_only_notice(
    *,
    policy_model: AssessmentImportPolicyModel,
    reason: str,
    import_file: str,
    colorize_fn,
) -> None:
    count = int(policy_model.assessment_count or 0)
    print(
        colorize_fn(
            "  WARNING: untrusted assessment source detected. "
            f"Imported issues only; skipped {count} assessment score update(s).",
            "yellow",
        )
    )
    if reason:
        print(colorize_fn(f"  Reason: {reason}", "dim"))
    print(
        colorize_fn(
            "  Assessment scores in state were left unchanged.",
            "dim",
        )
    )
    print(
        colorize_fn(
            "  Happy path: use `desloppify review --run-batches --parallel --scan-after-import`.",
            "dim",
        )
    )
    print(
        colorize_fn(
            "  If you intentionally want manual assessment import, rerun with "
            f"`desloppify review --import {import_file} --manual-override --attest \"<why this is justified>\"`.",
            "dim",
        )
    )
    print(
        colorize_fn(
            "  Claude cloud path for durable scores: "
            f"`desloppify review --import {import_file} --attested-external "
            f"--attest \"{ATTESTED_EXTERNAL_ATTEST_EXAMPLE}\"`",
            "dim",
        )
    )


def print_assessment_policy_notice(
    policy,
    *,
    import_file: str,
    colorize_fn,
) -> None:
    """Render trust/override status for assessment-bearing imports."""
    policy_model = AssessmentImportPolicyModel.from_mapping(policy)
    if not policy_model.assessments_present:
        return
    mode = policy_model.mode.strip().lower()
    reason = policy_model.reason.strip()
    handlers = {
        "trusted": lambda: _print_trusted_notice(
            policy_model=policy_model,
            colorize_fn=colorize_fn,
        ),
        "trusted_internal": lambda: _print_trusted_internal_notice(
            policy_model=policy_model,
            colorize_fn=colorize_fn,
        ),
        "manual_override": lambda: _print_manual_override_notice(
            policy_model=policy_model,
            reason=reason,
            colorize_fn=colorize_fn,
        ),
        "attested_external": lambda: _print_attested_external_notice(
            policy_model=policy_model,
            reason=reason,
            colorize_fn=colorize_fn,
        ),
        "issues_only": lambda: _print_issues_only_notice(
            policy_model=policy_model,
            reason=reason,
            import_file=import_file,
            colorize_fn=colorize_fn,
        ),
    }
    handler = handlers.get(mode)
    if handler is None:
        return
    handler()


__all__ = [
    "ImportLoadConfig",
    "ImportPayloadLoadError",
    "assessment_mode_label",
    "assessment_policy_model_from_payload",
    "assessment_policy_from_payload",
    "load_import_issues_data",
    "print_assessment_mode_banner",
    "print_import_load_errors",
    "print_assessment_policy_notice",
    "print_assessments_summary",
    "print_open_review_summary",
    "print_review_import_scores_and_integrity",
    "print_skipped_validation_details",
    "resolve_override_context",
]
