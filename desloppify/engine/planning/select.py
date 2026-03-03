"""Issue prioritization/selection helpers for next items."""

from __future__ import annotations

from desloppify.engine._work_queue.core import (
    QueueBuildOptions,
    build_work_queue,
)
from desloppify.engine.planning.types import PlanItem, PlanState


def get_next_items(
    state: PlanState,
    count: int = 1,
    scan_path: str | None = None,
) -> list[PlanItem]:
    """Get the N highest-priority open issues.

    Legacy plan API intentionally returns only issue items (not synthetic
    subjective queue items) so existing planner consumers stay stable.
    """
    result = build_work_queue(
        state,
        options=QueueBuildOptions(
            count=count,
            scan_path=scan_path,
            status="open",
            include_subjective=False,
        ),
    )
    return [item for item in result["items"] if item.get("kind") == "issue"]


def get_next_item(
    state: PlanState,
    scan_path: str | None = None,
) -> PlanItem | None:
    """Get the highest-priority open issue."""
    items = get_next_items(state, count=1, scan_path=scan_path)
    return items[0] if items else None
