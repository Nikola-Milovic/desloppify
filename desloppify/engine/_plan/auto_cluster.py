"""Auto-clustering algorithm — groups issues into task clusters."""

from __future__ import annotations

from desloppify.base.config import DEFAULT_TARGET_STRICT_SCORE
from desloppify.engine._plan.constants import AUTO_PREFIX
from desloppify.engine._plan.auto_cluster_sync import (
    prune_stale_clusters as _prune_stale_clusters,
    sync_issue_clusters as _sync_issue_clusters,
    sync_subjective_clusters as _sync_subjective_clusters,
)
from desloppify.engine._plan.schema import PlanModel, ensure_plan_defaults
from desloppify.engine._plan.subjective_policy import SubjectiveVisibility
from desloppify.engine._state.schema import StateModel, utc_now

# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def _repair_ghost_cluster_refs(plan: PlanModel, now: str) -> int:
    """Cross-check cluster membership between cluster.issue_ids and overrides.

    Repairs three kinds of drift:
    1. Override points to a cluster that does not exist → clear override.
    2. Override points to cluster X, but issue is not in X's issue_ids → add it.
    3. Issue is in cluster.issue_ids but override points elsewhere (or has no
       cluster ref) → update override to match cluster.issue_ids.

    cluster.issue_ids is treated as authoritative when it disagrees with
    the override, because the sync helpers always write issue_ids first.
    """
    clusters = plan.get("clusters", {})
    overrides = plan.get("overrides", {})
    repaired = 0

    # Direction 1: override → cluster that doesn't exist
    for override in overrides.values():
        cluster_name = override.get("cluster")
        if cluster_name and cluster_name not in clusters:
            override["cluster"] = None
            override["updated_at"] = now
            repaired += 1

    # Build authoritative map: issue_id → cluster_name from cluster.issue_ids.
    # When an issue appears in multiple clusters, prefer manual (non-auto)
    # clusters, matching the priority in collapse_clusters.
    canonical: dict[str, str] = {}
    for name, cluster in clusters.items():
        for issue_id in cluster.get("issue_ids", []):
            if issue_id not in canonical or not cluster.get("auto"):
                canonical[issue_id] = name

    # Direction 2+3: ensure overrides agree with cluster.issue_ids
    for issue_id, cluster_name in canonical.items():
        override = overrides.get(issue_id)
        if override is None:
            overrides[issue_id] = {
                "issue_id": issue_id,
                "cluster": cluster_name,
                "created_at": now,
                "updated_at": now,
            }
            repaired += 1
        elif override.get("cluster") != cluster_name:
            override["cluster"] = cluster_name
            override["updated_at"] = now
            repaired += 1

    # Direction 2 (reverse): override says cluster X, but issue not in X.issue_ids.
    # If the issue isn't in *any* cluster's issue_ids, clear the override ref.
    for issue_id, override in overrides.items():
        ref = override.get("cluster")
        if not ref or ref not in clusters:
            continue
        if issue_id in canonical:
            continue  # already handled above
        # Override points to a real cluster, but issue is not in any
        # cluster's issue_ids → stale ref, clear it.
        override["cluster"] = None
        override["updated_at"] = now
        repaired += 1

    return repaired


def auto_cluster_issues(
    plan: PlanModel,
    state: StateModel,
    *,
    target_strict: float = DEFAULT_TARGET_STRICT_SCORE,
    policy: SubjectiveVisibility | None = None,
    cycle_just_completed: bool = False,
) -> int:
    """Regenerate auto-clusters from current open issues.

    Returns count of changes made (clusters created, updated, or deleted).
    """
    ensure_plan_defaults(plan)

    issues = state.get("issues", {})
    clusters = plan.get("clusters", {})

    # Map existing auto-clusters by cluster_key
    existing_by_key: dict[str, str] = {}  # cluster_key → cluster_name
    for name, cluster in list(clusters.items()):
        if cluster.get("auto"):
            ck = cluster.get("cluster_key", "")
            if ck:
                existing_by_key[ck] = name

    now = utc_now()
    active_auto_keys: set[str] = set()
    changes = 0

    changes += _sync_issue_clusters(
        plan, issues, clusters, existing_by_key, active_auto_keys, now,
    )
    changes += _sync_subjective_clusters(
        plan, state, issues, clusters, existing_by_key, active_auto_keys, now,
        target_strict=target_strict,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    )
    changes += _prune_stale_clusters(
        plan, issues, clusters, active_auto_keys, now,
    )
    changes += _repair_ghost_cluster_refs(plan, now)

    plan["updated"] = now
    return changes


__all__ = [
    "AUTO_PREFIX",
    "auto_cluster_issues",
]
