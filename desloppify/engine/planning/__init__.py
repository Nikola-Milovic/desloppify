"""Plan rendering, output, and query interface.

This package produces human-readable plan output (markdown, terminal tables,
scorecards). It reads from the plan state but does not mutate it.

For plan data operations (queue moves, skips, clusters), use ``engine.plan``.
``engine._plan`` is private implementation detail and not a stable import
surface for callers outside the plan internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from desloppify.engine.planning.helpers import CONFIDENCE_ORDER

if TYPE_CHECKING:
    from pathlib import Path

    from desloppify.engine.planning.scan import PlanScanOptions
    from desloppify.engine.planning.types import PlanItem, PlanState
    from desloppify.languages._framework.base.types import LangConfig
    from desloppify.languages._framework.runtime import LangRun
    from desloppify.state import Issue


def generate_plan_md(state: PlanState, plan: dict | None = None) -> str:
    from desloppify.engine.planning.render import generate_plan_md as _generate_plan_md

    if plan is None:
        return _generate_plan_md(state)
    return _generate_plan_md(state, plan)


def generate_issues(
    path: Path,
    lang: LangConfig | LangRun | None = None,
    *,
    options: PlanScanOptions | None = None,
) -> tuple[list[Issue], dict[str, int]]:
    from desloppify.engine.planning.scan import generate_issues as _generate_issues

    if lang is None and options is None:
        return _generate_issues(path)
    if options is None:
        return _generate_issues(path, lang)
    if lang is None:
        return _generate_issues(path, options=options)
    return _generate_issues(path, lang, options=options)


def get_next_item(
    state: PlanState,
    scan_path: str | None = None,
) -> PlanItem | None:
    from desloppify.engine.planning.select import get_next_item as _get_next_item

    if scan_path is None:
        return _get_next_item(state)
    return _get_next_item(state, scan_path=scan_path)


def get_next_items(
    state: PlanState,
    count: int = 1,
    scan_path: str | None = None,
) -> list[PlanItem]:
    from desloppify.engine.planning.select import get_next_items as _get_next_items

    if count == 1 and scan_path is None:
        return _get_next_items(state)
    if scan_path is None:
        return _get_next_items(state, count=count)
    if count == 1:
        return _get_next_items(state, scan_path=scan_path)
    return _get_next_items(state, count=count, scan_path=scan_path)


__all__ = [
    "CONFIDENCE_ORDER",
    "generate_issues",
    "generate_plan_md",
    "get_next_item",
    "get_next_items",
]
