"""Plan mutation helpers for epic triage."""

from __future__ import annotations

from dataclasses import dataclass

from desloppify.engine._plan.schema import (
    EPIC_PREFIX,
    Cluster,
    PlanModel,
    ensure_plan_defaults,
)
from desloppify.engine._plan.stale_dimensions import review_issue_snapshot_hash
from desloppify.engine._state.schema import StateModel, utc_now

from .epic_triage_prompt import TriageResult


@dataclass
class TriageMutationResult:
    """What changed when triage was applied to the plan."""

    epics_created: int = 0
    epics_updated: int = 0
    epics_completed: int = 0
    issues_dismissed: int = 0
    issues_reassigned: int = 0
    strategy_summary: str = ""
    triage_version: int = 0
    dry_run: bool = False


def _epic_sort_key(epic_data: dict) -> int:
    return int(epic_data.get("dependency_order", 999))


def _normalized_epic_name(raw_name: str) -> str:
    return raw_name if raw_name.startswith(EPIC_PREFIX) else f"{EPIC_PREFIX}{raw_name}"


def _update_existing_epic_cluster(
    existing: Cluster,
    epic_data: dict,
    *,
    now: str,
    version: int,
) -> None:
    existing["thesis"] = epic_data["thesis"]
    existing["direction"] = epic_data["direction"]
    existing["root_cause"] = epic_data.get("root_cause", "")
    existing["issue_ids"] = epic_data["issue_ids"]
    existing["dismissed"] = epic_data.get("dismissed", [])
    existing["agent_safe"] = epic_data.get("agent_safe", False)
    existing["dependency_order"] = epic_data["dependency_order"]
    existing["action_steps"] = epic_data.get("action_steps", [])
    existing["updated_at"] = now
    existing["triage_version"] = version
    existing["description"] = epic_data["thesis"]
    # Don't overwrite in_progress status from agent
    if existing.get("status") != "in_progress":
        existing["status"] = epic_data.get("status", "pending")


def _create_epic_cluster(
    *,
    epic_name: str,
    epic_data: dict,
    now: str,
    version: int,
) -> Cluster:
    return {
        "name": epic_name,
        "description": epic_data["thesis"],
        "issue_ids": epic_data["issue_ids"],
        "auto": True,
        "cluster_key": f"epic::{epic_name}",
        "action": f"desloppify plan focus {epic_name}",
        "user_modified": False,
        "created_at": now,
        "updated_at": now,
        # Epic fields
        "thesis": epic_data["thesis"],
        "direction": epic_data["direction"],
        "root_cause": epic_data.get("root_cause", ""),
        "supersedes": [],
        "dismissed": epic_data.get("dismissed", []),
        "agent_safe": epic_data.get("agent_safe", False),
        "dependency_order": epic_data["dependency_order"],
        "action_steps": epic_data.get("action_steps", []),
        "source_clusters": [],
        "status": epic_data.get("status", "pending"),
        "triage_version": version,
    }


def _upsert_triage_clusters(
    *,
    clusters: dict[str, Cluster],
    triage: TriageResult,
    now: str,
    version: int,
) -> tuple[int, int]:
    created = 0
    updated = 0
    for epic_data in sorted(triage.epics, key=_epic_sort_key):
        raw_name = epic_data["name"]
        epic_name = _normalized_epic_name(raw_name)
        existing = clusters.get(epic_name)
        if existing and existing.get("thesis"):
            _update_existing_epic_cluster(existing, epic_data, now=now, version=version)
            updated += 1
            continue
        clusters[epic_name] = _create_epic_cluster(
            epic_name=epic_name,
            epic_data=epic_data,
            now=now,
            version=version,
        )
        created += 1
    return created, updated


def _triaged_out_payload(
    *,
    issue_id: str,
    reason: str,
    note: str | None,
    now: str,
    scan_count: int,
) -> dict:
    return {
        "issue_id": issue_id,
        "kind": "triaged_out",
        "reason": reason,
        "note": note,
        "attestation": None,
        "created_at": now,
        "review_after": None,
        "skipped_at_scan": scan_count,
    }


def _dismiss_triage_issues(
    *,
    triage: TriageResult,
    order: list[str],
    skipped: dict,
    now: str,
    version: int,
    scan_count: int,
) -> tuple[list[str], int]:
    dismissed_ids: list[str] = []
    dismiss_count = 0
    for dismissed in triage.dismissed_issues:
        fid = dismissed.issue_id
        dismissed_ids.append(fid)
        if fid in order:
            order.remove(fid)
        skipped[fid] = _triaged_out_payload(
            issue_id=fid,
            reason=dismissed.reason,
            note=f"Dismissed by epic triage v{version}",
            now=now,
            scan_count=scan_count,
        )
        dismiss_count += 1

    for epic_data in triage.epics:
        for fid in epic_data.get("dismissed", []):
            if fid in dismissed_ids or fid not in order:
                continue
            order.remove(fid)
            dismissed_ids.append(fid)
            skipped[fid] = _triaged_out_payload(
                issue_id=fid,
                reason=f"Dismissed by epic triage v{version}",
                note=None,
                now=now,
                scan_count=scan_count,
            )
            dismiss_count += 1

    return dismissed_ids, dismiss_count


def _reorder_queue_by_dependency(
    *,
    order: list[str],
    triage: TriageResult,
    dismissed_ids: list[str],
) -> None:
    epic_issue_ids: set[str] = set()
    epic_ordered_ids: list[str] = []
    dismissed_set = set(dismissed_ids)
    for epic_data in sorted(triage.epics, key=_epic_sort_key):
        for fid in epic_data["issue_ids"]:
            if fid in epic_issue_ids or fid in dismissed_set:
                continue
            epic_issue_ids.add(fid)
            epic_ordered_ids.append(fid)

    non_epic_items = [fid for fid in order if fid not in epic_issue_ids]
    order.clear()
    order.extend(epic_ordered_ids)
    order.extend(non_epic_items)


def _set_triage_meta(
    *,
    plan: PlanModel,
    state: StateModel,
    triage: TriageResult,
    now: str,
    version: int,
    dismissed_ids: list[str],
    trigger: str,
) -> None:
    current_hash = review_issue_snapshot_hash(state)
    open_review_ids = sorted(
        fid
        for fid, issue in state.get("issues", {}).items()
        if issue.get("status") == "open"
        and issue.get("detector") in ("review", "concerns")
    )

    plan["epic_triage_meta"] = {
        "triaged_ids": open_review_ids,
        "last_run": now,
        "version": version,
        "dismissed_ids": dismissed_ids,
        "issue_snapshot_hash": current_hash,
        "strategy_summary": triage.strategy_summary,
        "trigger": trigger,
    }


def apply_triage_to_plan(
    plan: PlanModel,
    state: StateModel,
    triage: TriageResult,
    *,
    trigger: str = "manual",
) -> TriageMutationResult:
    """Apply parsed triage result to the living plan.

    1. Creates/updates triage-clusters in plan["clusters"]
    2. Marks dismissed issues as triaged_out skips
    3. Reorders queue_order to group epic members by dependency_order
    4. Updates epic_triage_meta with snapshot hash
    """
    ensure_plan_defaults(plan)
    now = utc_now()
    result = TriageMutationResult()
    result.strategy_summary = triage.strategy_summary

    clusters = plan["clusters"]
    skipped: dict = plan["skipped"]
    order: list[str] = plan["queue_order"]
    meta = plan.get("epic_triage_meta", {})
    version = int(meta.get("version", 0)) + 1
    result.triage_version = version

    created, updated = _upsert_triage_clusters(
        clusters=clusters,
        triage=triage,
        now=now,
        version=version,
    )
    result.epics_created += created
    result.epics_updated += updated

    dismissed_ids, dismiss_count = _dismiss_triage_issues(
        triage=triage,
        order=order,
        skipped=skipped,
        now=now,
        version=version,
        scan_count=int(state.get("scan_count", 0)),
    )
    result.issues_dismissed += dismiss_count

    _reorder_queue_by_dependency(
        order=order,
        triage=triage,
        dismissed_ids=dismissed_ids,
    )

    _set_triage_meta(
        plan=plan,
        state=state,
        triage=triage,
        now=now,
        version=version,
        dismissed_ids=dismissed_ids,
        trigger=trigger,
    )
    plan["updated"] = now

    return result

__all__ = ["TriageMutationResult", "apply_triage_to_plan"]
