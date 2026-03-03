"""Helper routines shared by resolve render output."""

from __future__ import annotations

from desloppify import state as state_mod
from desloppify.core.output import colorize
from desloppify.engine._state.schema import StateModel


def score_snapshot_or_warn(state: StateModel):
    snapshot = state_mod.score_snapshot(state)
    if (
        snapshot.overall is None
        or snapshot.objective is None
        or snapshot.strict is None
        or snapshot.verified is None
    ):
        print(colorize("\n  Scores unavailable — run `desloppify scan`.", "yellow"))
        return None
    return snapshot


def print_strict_gap_note(status: str, *, overall: float, strict: float) -> None:
    if status != "wontfix":
        return
    strict_gap = round(overall - strict, 1)
    if strict_gap <= 0:
        return
    print(
        colorize(
            f"  Note: wontfix items still count against strict score. "
            f"Current gap: overall {overall:.1f} vs strict {strict:.1f} ({strict_gap:.1f} pts of hidden debt).",
            "yellow",
        )
    )


def print_post_resolve_guidance(
    *,
    status: str,
    has_review_issues: bool,
    overall_delta: float,
) -> None:
    if has_review_issues and abs(overall_delta) < 0.05:
        print(
            colorize(
                "  Scores unchanged (review issues don't affect scores directly).",
                "yellow",
            )
        )
        print(
            colorize(
                "  Run `desloppify review --prepare` to get updated assessment scores.",
                "dim",
            )
        )
        return
    if status == "fixed":
        print(
            colorize(
                "  Verified score updates after a scan confirms the issue disappeared.",
                "yellow",
            )
        )


__all__ = [
    "print_post_resolve_guidance",
    "print_strict_gap_note",
    "score_snapshot_or_warn",
]
