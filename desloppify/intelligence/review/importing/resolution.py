"""Auto-resolution helpers for review re-import workflows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desloppify.engine._state.schema import StateModel
from desloppify.state import utc_now


def auto_resolve_review_issues(
    state: StateModel,
    *,
    new_ids: set[str],
    diff: dict[str, Any],
    note: str,
    should_resolve: Callable[[dict[str, Any]], bool],
    utc_now_fn=utc_now,
) -> None:
    """Auto-resolve stale open review issues that match a scope predicate."""
    diff.setdefault("auto_resolved", 0)
    for issue_id, issue in state.get("issues", {}).items():
        if issue_id in new_ids or issue.get("status") != "open":
            continue
        if not should_resolve(issue):
            continue
        issue["status"] = "auto_resolved"
        issue["resolved_at"] = utc_now_fn()
        issue["note"] = note
        diff["auto_resolved"] += 1
