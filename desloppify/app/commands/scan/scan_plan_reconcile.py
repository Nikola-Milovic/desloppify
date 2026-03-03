"""Post-scan plan reconciliation — sync plan queue metadata after a scan merge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from desloppify import state as state_mod
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.core.output import colorize
from desloppify.engine.plan import (
    append_log_entry,
    auto_cluster_issues,
    load_plan,
    reconcile_plan_after_scan,
    save_plan,
    sync_communicate_score_needed,
    sync_create_plan_needed,
    sync_stale_dimensions,
    sync_triage_needed,
    sync_unscored_dimensions,
)

if TYPE_CHECKING:
    from desloppify.app.commands.scan.scan_workflow import ScanRuntime


def _plan_has_user_content(plan: dict[str, object]) -> bool:
    """Return True when the living plan has any user-managed queue metadata."""
    return bool(
        plan.get("queue_order")
        or plan.get("overrides")
        or plan.get("clusters")
        or plan.get("skipped")
    )


def _apply_plan_reconciliation(plan: dict[str, object], state: state_mod.StateModel, reconcile_fn) -> bool:
    """Apply standard post-scan plan reconciliation when user content exists."""
    if not _plan_has_user_content(plan):
        return False
    recon = reconcile_fn(plan, state)
    if recon.resurfaced:
        print(
            colorize(
                f"  Plan: {len(recon.resurfaced)} skipped item(s) re-surfaced after review period.",
                "cyan",
            )
        )
    return bool(recon.changes)


def _sync_unscored_dimensions(plan: dict[str, object], state: state_mod.StateModel, sync_fn) -> bool:
    """Sync unscored subjective dimensions into the plan queue."""
    sync = sync_fn(plan, state)
    if sync.injected:
        print(
            colorize(
                f"  Plan: {len(sync.injected)} unscored subjective dimension(s) queued for initial review.",
                "cyan",
            )
        )
    return bool(sync.changes)


def _sync_stale_dimensions(plan: dict[str, object], state: state_mod.StateModel, sync_fn) -> bool:
    """Sync stale subjective dimensions (prune refreshed + inject stale) in plan queue."""
    sync = sync_fn(plan, state)
    if sync.pruned:
        print(
            colorize(
                f"  Plan: {len(sync.pruned)} refreshed subjective dimension(s) removed from queue.",
                "cyan",
            )
        )
    if sync.injected:
        print(
            colorize(
                f"  Plan: {len(sync.injected)} subjective dimension(s) queued for review.",
                "cyan",
            )
        )
    return bool(sync.changes)


def _sync_auto_clusters(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    target_strict: float = 95.0,
    policy=None,
    cycle_just_completed: bool = False,
) -> bool:
    """Regenerate automatic task clusters after scan merge."""
    return bool(auto_cluster_issues(
        plan, state,
        target_strict=target_strict,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    ))


def _seed_plan_start_scores(plan: dict[str, object], state: state_mod.StateModel) -> bool:
    """Set plan_start_scores when beginning a new queue cycle."""
    existing = plan.get("plan_start_scores")
    if existing and not isinstance(existing, dict):
        return False
    # Seed when empty OR when it's the reset sentinel ({"reset": True})
    if existing and not existing.get("reset"):
        return False
    scores = state_mod.score_snapshot(state)
    if scores.strict is None:
        return False
    plan["plan_start_scores"] = {
        "strict": scores.strict,
        "overall": scores.overall,
        "objective": scores.objective,
        "verified": scores.verified,
    }
    return True


def _clear_plan_start_scores_if_queue_empty(
    state: state_mod.StateModel, plan: dict[str, object]
) -> bool:
    """Clear plan-start score snapshot once the queue is fully drained."""
    if not plan.get("plan_start_scores"):
        return False

    try:
        from desloppify.app.commands.helpers.queue_progress import (
            plan_aware_queue_breakdown,
        )

        breakdown = plan_aware_queue_breakdown(state, plan)
        queue_empty = breakdown.actionable == 0
    except PLAN_LOAD_EXCEPTIONS as exc:
        logging.debug("Plan operation skipped: %s", exc)
        return False
    if not queue_empty:
        return False
    state["_plan_start_scores_for_reveal"] = dict(plan["plan_start_scores"])
    plan["plan_start_scores"] = {}
    return True


def _subjective_policy_context(
    runtime: ScanRuntime,
    plan: dict[str, object],
) -> tuple[float, object, bool]:
    from desloppify.app.commands.helpers.score import target_strict_score_from_config
    from desloppify.engine.plan import compute_subjective_visibility

    target_strict = target_strict_score_from_config(runtime.config, fallback=95.0)
    policy = compute_subjective_visibility(
        runtime.state,
        target_strict=target_strict,
        plan=plan,
    )
    cycle_just_completed = not plan.get("plan_start_scores")
    return target_strict, policy, cycle_just_completed


def _sync_unscored_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    changed = _sync_unscored_dimensions(plan, state, sync_unscored_dimensions)
    if changed:
        append_log_entry(plan, "sync_unscored", actor="system", detail={"changes": True})
    return changed


def _sync_stale_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
    cycle_just_completed: bool,
) -> bool:
    changed = _sync_stale_dimensions(
        plan,
        state,
        lambda p, s: sync_stale_dimensions(
            p,
            s,
            policy=policy,
            cycle_just_completed=cycle_just_completed,
        ),
    )
    if changed:
        append_log_entry(plan, "sync_stale", actor="system", detail={"changes": True})
    return changed


def _sync_auto_clusters_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    target_strict: float,
    policy,
    cycle_just_completed: bool,
) -> bool:
    changed = _sync_auto_clusters(
        plan,
        state,
        target_strict=target_strict,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    )
    if changed:
        append_log_entry(plan, "auto_cluster", actor="system", detail={"changes": True})
    return changed


def _sync_triage_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    triage_sync = sync_triage_needed(plan, state)
    if not triage_sync.changes:
        return False
    if triage_sync.injected:
        print(
            colorize(
                "  Plan: planning mode needed — review issues changed since last triage.",
                "cyan",
            )
        )
        append_log_entry(plan, "sync_triage", actor="system", detail={"injected": True})
    return True


def _sync_communicate_score_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    communicate_sync = sync_communicate_score_needed(plan, state, policy=policy)
    if not communicate_sync.changes:
        return False
    append_log_entry(
        plan,
        "sync_communicate_score",
        actor="system",
        detail={"injected": True},
    )
    return True


def _sync_create_plan_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    create_plan_sync = sync_create_plan_needed(plan, state, policy=policy)
    if not create_plan_sync.changes:
        return False
    if create_plan_sync.injected:
        print(
            colorize(
                "  Plan: reviews complete — `workflow::create-plan` queued.",
                "cyan",
            )
        )
        append_log_entry(plan, "sync_create_plan", actor="system", detail={"injected": True})
    return True


def _sync_plan_start_scores_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    seeded = _seed_plan_start_scores(plan, state)
    if seeded:
        append_log_entry(plan, "seed_start_scores", actor="system", detail={})
        return True
    # Only clear scores that existed before this reconcile pass —
    # never clear scores we just seeded in the same scan.
    cleared = _clear_plan_start_scores_if_queue_empty(state, plan)
    if cleared:
        append_log_entry(plan, "clear_start_scores", actor="system", detail={})
    return cleared


def reconcile_plan_post_scan(runtime: ScanRuntime) -> None:
    """Reconcile plan queue metadata and stale subjective review dimensions."""
    try:
        plan_path = runtime.state_path.parent / "plan.json" if runtime.state_path else None
        plan = load_plan(plan_path)
        dirty = False

        if _apply_plan_reconciliation(plan, runtime.state, reconcile_plan_after_scan):
            dirty = True

        if _sync_unscored_and_log(plan, runtime.state):
            dirty = True

        target_strict, policy, cycle_just_completed = _subjective_policy_context(
            runtime,
            plan,
        )
        if _sync_stale_and_log(
            plan,
            runtime.state,
            policy=policy,
            cycle_just_completed=cycle_just_completed,
        ):
            dirty = True

        if _sync_auto_clusters_and_log(
            plan,
            runtime.state,
            target_strict=target_strict,
            policy=policy,
            cycle_just_completed=cycle_just_completed,
        ):
            dirty = True

        if _sync_triage_and_log(plan, runtime.state):
            dirty = True
        if _sync_communicate_score_and_log(plan, runtime.state, policy=policy):
            dirty = True
        if _sync_create_plan_and_log(plan, runtime.state, policy=policy):
            dirty = True
        if _sync_plan_start_scores_and_log(plan, runtime.state):
            dirty = True

        if dirty:
            save_plan(plan, plan_path)
    except PLAN_LOAD_EXCEPTIONS as exc:
        logging.debug("Plan operation skipped: %s", exc)
