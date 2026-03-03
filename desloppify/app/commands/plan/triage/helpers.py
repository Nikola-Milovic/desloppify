"""Helper utilities for plan triage workflow."""

from __future__ import annotations

import argparse
from collections import defaultdict

from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.core.output import colorize
from desloppify.engine.plan import (
    TRIAGE_IDS,
    TRIAGE_STAGE_IDS,
    purge_ids,
    review_issue_snapshot_hash,
    save_plan,
)
from desloppify.state import utc_now

_STAGE_ORDER = ["observe", "reflect", "organize"]


def _has_triage_in_queue(plan: dict) -> bool:
    """Check if any triage stage ID is in the queue."""
    order = set(plan.get("queue_order", []))
    return bool(order & TRIAGE_IDS)

def _inject_triage_stages(plan: dict) -> None:
    """Inject all 4 triage stage IDs into the queue (fresh start)."""
    order: list[str] = plan.setdefault("queue_order", [])
    existing = set(order)
    for sid in TRIAGE_STAGE_IDS:
        if sid not in existing:
            order.insert(0 if sid == TRIAGE_STAGE_IDS[0] else len(order), sid)
    # Re-insert in correct order at front
    for sid in reversed(TRIAGE_STAGE_IDS):
        if sid in order:
            order.remove(sid)
    insert_at = 0
    for sid in TRIAGE_STAGE_IDS:
        order.insert(insert_at, sid)
        insert_at += 1

def _purge_triage_stage(plan: dict, stage_name: str) -> None:
    """Purge a single triage stage ID from the queue."""
    sid = f"triage::{stage_name}"
    purge_ids(plan, [sid])

def _cascade_clear_later_confirmations(stages: dict, from_stage: str) -> list[str]:
    """Clear confirmed_at/confirmed_text on stages AFTER *from_stage*. Returns cleared names."""
    try:
        idx = _STAGE_ORDER.index(from_stage)
    except ValueError:
        return []
    cleared: list[str] = []
    for later in _STAGE_ORDER[idx + 1:]:
        if later in stages and stages[later].get("confirmed_at"):
            stages[later].pop("confirmed_at", None)
            stages[later].pop("confirmed_text", None)
            cleared.append(later)
    return cleared

def _print_cascade_clear_feedback(cleared: list[str], stages: dict) -> None:
    """Print yellow cascade-clear message with next-step guidance."""
    if not cleared:
        return
    print(colorize(f"  Cleared confirmations on: {', '.join(cleared)}", "yellow"))
    next_unconfirmed = next(
        (s for s in _STAGE_ORDER if s in stages and not stages[s].get("confirmed_at")),
        None,
    )
    if next_unconfirmed:
        print(colorize(
            f"  Re-confirm with: desloppify plan triage --confirm {next_unconfirmed}",
            "dim",
        ))

def _observe_dimension_breakdown(si) -> tuple[dict[str, int], list[str]]:
    """Count issues per dimension from a TriageInput. Returns (by_dim, sorted_dim_names)."""
    by_dim: dict[str, int] = defaultdict(int)
    for _fid, f in si.open_issues.items():
        detail = f.get("detail", {}) if isinstance(f.get("detail"), dict) else {}
        dim = detail.get("dimension", "unknown")
        by_dim[dim] += 1
    dim_names = sorted(by_dim, key=lambda d: (-by_dim[d], d))
    return dict(by_dim), dim_names

def _open_review_ids_from_state(state: dict) -> set[str]:
    """Return IDs of all open review/concerns issues in state."""
    return {
        fid for fid, f in state.get("issues", {}).items()
        if f.get("status") == "open" and f.get("detector") in ("review", "concerns")
    }

def _triage_coverage(
    plan: dict,
    open_review_ids: set[str] | None = None,
) -> tuple[int, int, dict]:
    """Return (organized, total, clusters) for review issues in triage.

    When *open_review_ids* is provided, use it as the full set of review
    issues (from state) instead of falling back to queue_order.
    """
    clusters = plan.get("clusters", {})
    all_cluster_ids: set[str] = set()
    for c in clusters.values():
        all_cluster_ids.update(c.get("issue_ids", []))
    if open_review_ids is not None:
        review_ids = list(open_review_ids)
    else:
        review_ids = [
            fid for fid in plan.get("queue_order", [])
            if not fid.startswith("triage::") and not fid.startswith("workflow::") and (fid.startswith("review::") or fid.startswith("concerns::"))
        ]
    organized = sum(1 for fid in review_ids if fid in all_cluster_ids)
    return organized, len(review_ids), clusters

def _manual_clusters_with_issues(plan: dict) -> list[str]:
    """Return names of non-auto clusters that have issues."""
    return [
        name for name, c in plan.get("clusters", {}).items()
        if c.get("issue_ids") and not c.get("auto")
    ]

def _apply_completion(args: argparse.Namespace, plan: dict, strategy: str) -> None:
    """Shared completion logic: update meta, remove triage::pending, save."""
    runtime = command_runtime(args)
    state = runtime.state

    organized, total, clusters = _triage_coverage(
        plan, open_review_ids=_open_review_ids_from_state(state),
    )

    # Purge all triage stage IDs.
    purge_ids(plan, list(TRIAGE_IDS))

    current_hash = review_issue_snapshot_hash(state)

    meta = plan.setdefault("epic_triage_meta", {})
    meta["issue_snapshot_hash"] = current_hash
    open_review_ids = sorted(
        fid for fid, f in state.get("issues", {}).items()
        if f.get("status") == "open" and f.get("detector") in ("review", "concerns")
    )
    meta["triaged_ids"] = open_review_ids
    if strategy.strip().lower() != "same":
        meta["strategy_summary"] = strategy
    meta["trigger"] = "manual_triage"
    meta["last_completed_at"] = utc_now()
    # Archive stages before clearing so previous analysis is preserved
    stages = meta.get("triage_stages", {})
    if stages:
        meta["last_triage"] = {
            "completed_at": utc_now(),
            "stages": {k: dict(v) for k, v in stages.items()},
            "strategy": strategy if strategy.strip().lower() != "same" else meta.get("strategy_summary", ""),
        }
    meta["triage_stages"] = {}  # clear stages on completion
    meta.pop("stage_refresh_required", None)
    meta.pop("stage_snapshot_hash", None)

    save_plan(plan)

    cluster_count = len([c for c in clusters.values() if c.get("issue_ids")])
    print(colorize(f"  Triage complete: {organized}/{total} issues in {cluster_count} cluster(s).", "green"))
    effective_strategy = strategy if strategy.strip().lower() != "same" else meta.get("strategy_summary", "")
    if effective_strategy:
        print(colorize(f"  Strategy: {effective_strategy}", "cyan"))
    print(colorize("  Run `desloppify next` to start implementation.", "green"))

def _find_cluster_for(fid: str, clusters: dict) -> str | None:
    """Return the cluster name containing *fid*, or None."""
    for name, c in clusters.items():
        if fid in c.get("issue_ids", []):
            return name
    return None

def _count_log_activity_since(plan: dict, since: str) -> dict[str, int]:
    """Count execution log entries by action since *since* timestamp."""
    counts: dict[str, int] = defaultdict(int)
    for entry in plan.get("execution_log", []):
        if entry.get("timestamp", "") >= since:
            counts[entry.get("action", "unknown")] += 1
    return dict(counts)

__all__ = [
    "_apply_completion",
    "_cascade_clear_later_confirmations",
    "_count_log_activity_since",
    "_find_cluster_for",
    "_has_triage_in_queue",
    "_inject_triage_stages",
    "_manual_clusters_with_issues",
    "_observe_dimension_breakdown",
    "_open_review_ids_from_state",
    "_print_cascade_clear_feedback",
    "_purge_triage_stage",
    "_triage_coverage",
]
