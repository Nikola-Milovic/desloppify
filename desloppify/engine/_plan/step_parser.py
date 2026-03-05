"""Parse and format numbered-steps text files for ActionStep data."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from desloppify.engine._plan.schema import ActionStep

_STEP_HEADER_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_REFS_RE = re.compile(r"^\s*Refs?:\s*(.+)$", re.IGNORECASE)


def parse_steps_file(text: str) -> list[ActionStep]:
    """Parse a numbered-steps text format into ActionStep dicts.

    Format::

        1. Step title here
           Detail lines indented by 2+ spaces.
           More detail.
           Refs: abc123, def456

        2. Another step
           Its detail block.
    """
    steps: list[ActionStep] = []
    current: ActionStep | None = None
    detail_lines: list[str] = []

    def _flush() -> None:
        nonlocal current, detail_lines
        if current is None:
            return
        detail = "\n".join(detail_lines).strip()
        if detail:
            current["detail"] = detail
        steps.append(current)
        current = None
        detail_lines = []

    for line in text.splitlines():
        m = _STEP_HEADER_RE.match(line)
        if m:
            _flush()
            current = {"title": m.group(2).strip()}
            continue

        if current is None:
            continue

        # Indented continuation line
        if line and (line[0] == " " or line[0] == "\t"):
            stripped = line.strip()
            ref_match = _REFS_RE.match(line)
            if ref_match:
                refs = [r.strip() for r in ref_match.group(1).split(",") if r.strip()]
                current.setdefault("issue_refs", []).extend(refs)
            else:
                detail_lines.append(stripped)
        elif line.strip() == "":
            # Blank line within detail — preserve it
            if detail_lines:
                detail_lines.append("")
        # Non-indented non-blank line that isn't a step header: ignore

    _flush()
    return steps


def format_steps(steps: list[str | dict]) -> str:
    """Format a list of ActionStep dicts (or legacy strings) into numbered-steps text.

    Round-trips with ``parse_steps_file``: ``parse_steps_file(format_steps(steps))``
    reproduces the same data (modulo whitespace normalization).
    """
    lines: list[str] = []
    for i, step in enumerate(steps, 1):
        if isinstance(step, str):
            lines.append(f"{i}. {step}")
        elif isinstance(step, dict):
            title = step.get("title", "")
            done = step.get("done", False)
            prefix = "[x] " if done else ""
            lines.append(f"{i}. {prefix}{title}")
            detail = step.get("detail", "")
            if detail:
                for dline in detail.splitlines():
                    lines.append(f"   {dline}")
            refs = step.get("issue_refs", [])
            if refs:
                lines.append(f"   Refs: {', '.join(refs)}")
        lines.append("")
    return "\n".join(lines)


def normalize_step(step: str | dict) -> dict:
    """Ensure a step is an ActionStep dict. Wraps plain strings."""
    if isinstance(step, dict):
        return step
    return {"title": step}


def step_summary(step: str | dict) -> str:
    """Return a one-line summary of a step for display."""
    if isinstance(step, str):
        return step
    return step.get("title", "")


__all__ = [
    "format_steps",
    "normalize_step",
    "parse_steps_file",
    "step_summary",
]
