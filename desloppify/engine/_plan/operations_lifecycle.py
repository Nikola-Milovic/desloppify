"""Lifecycle and reset mutations for plan operations."""

from __future__ import annotations

from desloppify.engine._plan.promoted_ids import prune_promoted_ids
from desloppify.engine._plan.schema import (
    PlanModel,
    SkipEntry,
    empty_plan,
    ensure_plan_defaults,
)
from desloppify.engine._state.schema import utc_now


def set_focus(plan: PlanModel, cluster_name: str) -> None:
    """Set the active cluster focus."""
    ensure_plan_defaults(plan)
    if cluster_name not in plan["clusters"]:
        raise ValueError(f"Cluster {cluster_name!r} does not exist")
    plan["active_cluster"] = cluster_name


def clear_focus(plan: PlanModel) -> None:
    """Clear the active cluster focus."""
    ensure_plan_defaults(plan)
    plan["active_cluster"] = None


def reset_plan(plan: PlanModel) -> None:
    """Reset plan to empty state, preserving version and created timestamp.

    Sets ``plan_start_scores`` to a sentinel so the next scan seeds real
    scores instead of incorrectly treating the reset as a completed cycle.
    """
    created = plan.get("created", utc_now())
    plan.clear()
    for k, v in empty_plan().items():
        plan[k] = v
    plan["created"] = created
    plan["plan_start_scores"] = {"reset": True}


def purge_ids(plan: PlanModel, issue_ids: list[str]) -> int:
    """Remove issue IDs from the plan entirely.

    Cleans queue_order, skipped, and all cluster memberships.
    Does NOT touch overrides (descriptions/notes are kept for history).
    Returns count of IDs that were actually present somewhere.
    """
    ensure_plan_defaults(plan)
    found = 0

    purge_set = set(issue_ids)
    prune_promoted_ids(plan, purge_set)

    order: list[str] = plan["queue_order"]
    skipped: dict[str, SkipEntry] = plan["skipped"]
    for fid in issue_ids:
        was_present = False
        if fid in order:
            order.remove(fid)
            was_present = True
        if fid in skipped:
            skipped.pop(fid)
            was_present = True
        for cluster in plan.get("clusters", {}).values():
            ids = cluster.get("issue_ids", [])
            if fid in ids:
                ids.remove(fid)
                was_present = True
        override = plan.get("overrides", {}).get(fid)
        if override and override.get("cluster"):
            override["cluster"] = None
            override["updated_at"] = utc_now()
        if was_present:
            found += 1

    return found


__all__ = ["clear_focus", "purge_ids", "reset_plan", "set_focus"]
