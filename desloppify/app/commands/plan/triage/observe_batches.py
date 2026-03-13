"""Observe-stage batching helpers for triage planning."""

from __future__ import annotations

from collections import defaultdict

from desloppify.engine._state.schema import Issue
from desloppify.engine.plan_triage import TriageInput


def observe_dimension_breakdown(si: TriageInput) -> tuple[dict[str, int], list[str]]:
    """Count open triage issues by review dimension."""
    review_issues = getattr(si, "review_issues", getattr(si, "open_issues", {}))
    by_dim: dict[str, int] = defaultdict(int)
    for issue in review_issues.values():
        detail = issue.get("detail", {}) if isinstance(issue.get("detail"), dict) else {}
        dim = detail.get("dimension", "unknown")
        by_dim[dim] += 1
    dim_names = sorted(by_dim, key=lambda dim: (-by_dim[dim], dim))
    return dict(by_dim), dim_names


def group_issues_into_observe_batches(
    si: TriageInput,
    max_batches: int = 5,
) -> list[tuple[list[str], dict[str, Issue]]]:
    """Group observe issues into dimension-balanced batches."""
    review_issues = getattr(si, "review_issues", getattr(si, "open_issues", {}))
    by_dim, dim_names = observe_dimension_breakdown(si)
    if len(dim_names) <= 1:
        return [(dim_names, dict(review_issues))]

    num_batches = min(max_batches, len(dim_names))
    batch_dims: list[list[str]] = [[] for _ in range(num_batches)]
    batch_counts: list[int] = [0] * num_batches
    for dim in dim_names:
        lightest = min(range(num_batches), key=lambda idx: batch_counts[idx])
        batch_dims[lightest].append(dim)
        batch_counts[lightest] += by_dim[dim]

    dim_to_issues: dict[str, dict[str, Issue]] = defaultdict(dict)
    for fid, issue in review_issues.items():
        detail = issue.get("detail", {}) if isinstance(issue.get("detail"), dict) else {}
        dim = detail.get("dimension", "unknown")
        dim_to_issues[dim][fid] = issue

    result: list[tuple[list[str], dict[str, Issue]]] = []
    for dims in batch_dims:
        if not dims:
            continue
        subset: dict[str, Issue] = {}
        for dim in dims:
            subset.update(dim_to_issues.get(dim, {}))
        if subset:
            result.append((dims, subset))
    return result


__all__ = [
    "group_issues_into_observe_batches",
    "observe_dimension_breakdown",
]
