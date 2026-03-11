"""Lifecycle visibility filtering for work-queue items."""

from __future__ import annotations

from desloppify.engine.plan_queue import (
    NON_OBJECTIVE_DETECTORS,
    WORKFLOW_DEFERRED_DISPOSITION_ID,
    WORKFLOW_RUN_SCAN_ID,
)
from desloppify.engine._work_queue.types import WorkQueueItem

# Detectors whose issues should only surface after objective queue is drained.
# Must be a subset of NON_OBJECTIVE_DETECTORS.
ENDGAME_ONLY_DETECTORS: frozenset[str] = NON_OBJECTIVE_DETECTORS


def _validate_endgame_only_detectors() -> None:
    missing = ENDGAME_ONLY_DETECTORS - NON_OBJECTIVE_DETECTORS
    if missing:
        raise RuntimeError(
            "ENDGAME_ONLY_DETECTORS has items not in NON_OBJECTIVE_DETECTORS: "
            f"{missing}"
        )


_validate_endgame_only_detectors()


def _has_objective_items(items: list[WorkQueueItem]) -> bool:
    """True if any objective mechanical work items remain in the queue.

    Checks both individual issues and collapsed clusters — clusters
    contain objective issues grouped by the queue builder.
    """
    return any(
        item.get("kind") in ("issue", "cluster")
        and item.get("detector", "") not in NON_OBJECTIVE_DETECTORS
        for item in items
    )


def _has_initial_reviews(items: list[WorkQueueItem]) -> bool:
    """True if any unassessed subjective dimensions need initial review."""
    return any(
        item.get("kind") == "subjective_dimension"
        and item.get("initial_review")
        for item in items
    )


def _is_endgame_only(item: WorkQueueItem) -> bool:
    """True if this item should only appear when the objective queue is drained."""
    if item.get("kind") == "subjective_dimension":
        return not item.get("initial_review")
    return item.get("detector", "") in ENDGAME_ONLY_DETECTORS


def _has_endgame_subjective(items: list[WorkQueueItem]) -> bool:
    """True if any non-initial subjective review items are pending."""
    return any(_is_endgame_only(item) for item in items)


def _has_triage_stages(items: list[WorkQueueItem]) -> bool:
    """True if any pending triage stage items are in the queue."""
    return any(
        item.get("kind") == "workflow_stage"
        and str(item.get("id", "")).startswith("triage::")
        for item in items
    )


def _is_deferred_disposition(item: WorkQueueItem) -> bool:
    return item.get("id") == WORKFLOW_DEFERRED_DISPOSITION_ID


def _has_deferred_disposition(items: list[WorkQueueItem]) -> bool:
    return any(_is_deferred_disposition(item) for item in items)


def _is_postflight_scan(item: WorkQueueItem) -> bool:
    return item.get("id") == WORKFLOW_RUN_SCAN_ID


def _has_postflight_scan(items: list[WorkQueueItem]) -> bool:
    return any(_is_postflight_scan(item) for item in items)


def _is_triage_stage(item: WorkQueueItem) -> bool:
    """True when item is a triage workflow stage."""
    return (
        item.get("kind") == "workflow_stage"
        and str(item.get("id", "")).startswith("triage::")
    )


def _is_postflight_workflow(item: WorkQueueItem) -> bool:
    return (
        item.get("kind") == "workflow_action"
        and not _is_deferred_disposition(item)
        and not _is_postflight_scan(item)
    )


def _has_postflight_workflow(items: list[WorkQueueItem]) -> bool:
    return any(_is_postflight_workflow(item) for item in items)


def _is_force_visible(item: WorkQueueItem) -> bool:
    """True when the item is explicitly escalated past objective gating."""
    return bool(item.get("force_visible"))


def _is_postflight_phase_item(item: WorkQueueItem) -> bool:
    return (
        _is_endgame_only(item)
        or _is_triage_stage(item)
        or _is_deferred_disposition(item)
        or _is_postflight_scan(item)
        or _is_postflight_workflow(item)
    )


def apply_lifecycle_filter(items: list[WorkQueueItem]) -> list[WorkQueueItem]:
    """Enforce lifecycle visibility rules."""
    if _has_initial_reviews(items):
        return [
            item for item in items
            if item.get("kind") == "subjective_dimension" and item.get("initial_review")
        ]

    if _has_objective_items(items):
        return [
            item for item in items
            if not _is_postflight_phase_item(item) or _is_force_visible(item)
        ]

    # Explicit post-flight sequence:
    #   deferred backlog disposition -> scan -> subjective review -> workflow -> triage
    if _has_deferred_disposition(items):
        return [
            item for item in items
            if _is_deferred_disposition(item) or _is_force_visible(item)
        ]
    if _has_postflight_scan(items):
        return [
            item for item in items
            if _is_postflight_scan(item) or _is_force_visible(item)
        ]
    if _has_endgame_subjective(items):
        return [
            item for item in items
            if _is_endgame_only(item) or _is_force_visible(item)
        ]
    if _has_postflight_workflow(items):
        return [
            item for item in items
            if _is_postflight_workflow(item) or _is_force_visible(item)
        ]
    if _has_triage_stages(items):
        return [item for item in items if _is_triage_stage(item) or _is_force_visible(item)]
    return items


__all__ = ["apply_lifecycle_filter"]
