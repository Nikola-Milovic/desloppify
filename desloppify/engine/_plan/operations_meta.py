"""Metadata and logging mutations for plan operations."""

from __future__ import annotations

from desloppify.engine._plan.schema import (
    ExecutionLogEntry,
    PlanModel,
    ensure_plan_defaults,
)
from desloppify.engine._state.schema import utc_now

_DEFAULT_MAX_LOG_ENTRIES = 10000


def _get_log_cap() -> int:
    """Read execution_log_max_entries from config. Returns 0 for unlimited."""
    try:
        from desloppify.core.config import load_config

        config = load_config()
        value = config.get("execution_log_max_entries", _DEFAULT_MAX_LOG_ENTRIES)
        return max(0, int(value))
    except (ImportError, OSError, ValueError, TypeError):
        return _DEFAULT_MAX_LOG_ENTRIES


def append_log_entry(
    plan: PlanModel,
    action: str,
    *,
    issue_ids: list[str] | None = None,
    cluster_name: str | None = None,
    actor: str = "user",
    note: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append a structured entry to the plan's execution log."""
    log = plan.get("execution_log", [])
    entry: ExecutionLogEntry = {
        "timestamp": utc_now(),
        "action": action,
        "issue_ids": issue_ids or [],
        "cluster_name": cluster_name,
        "actor": actor,
        "note": note,
        "detail": detail or {},
    }
    log.append(entry)
    cap = _get_log_cap()
    if cap > 0 and len(log) > cap:
        plan["execution_log"] = log[-cap:]


def describe_issue(
    plan: PlanModel, issue_id: str, description: str | None
) -> None:
    """Set or clear an augmented description on a issue."""
    ensure_plan_defaults(plan)
    now = utc_now()
    overrides = plan["overrides"]
    if issue_id not in overrides:
        overrides[issue_id] = {"issue_id": issue_id, "created_at": now}
    overrides[issue_id]["description"] = description
    overrides[issue_id]["updated_at"] = now


def annotate_issue(
    plan: PlanModel, issue_id: str, note: str | None
) -> None:
    """Set or clear a note on a issue."""
    ensure_plan_defaults(plan)
    now = utc_now()
    overrides = plan["overrides"]
    if issue_id not in overrides:
        overrides[issue_id] = {"issue_id": issue_id, "created_at": now}
    overrides[issue_id]["note"] = note
    overrides[issue_id]["updated_at"] = now


__all__ = ["annotate_issue", "append_log_entry", "describe_issue"]
