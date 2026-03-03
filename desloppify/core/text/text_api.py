"""Public text/path helpers for non-core layers."""

from __future__ import annotations

from pathlib import Path

from desloppify.core._internal import text_utils as text_utils_mod


def get_project_root() -> Path:
    """Return the active project root as an absolute resolved path."""
    return text_utils_mod.get_project_root().resolve()


def read_code_snippet(
    filepath: str,
    line: int,
    context: int = 1,
    *,
    project_root: Path | str | None = None,
) -> str | None:
    root = Path(project_root).resolve() if project_root is not None else None
    return text_utils_mod.read_code_snippet(
        str(filepath).strip(),
        line,
        context,
        project_root=root,
    )


def get_area(filepath: str, *, min_depth: int = 2) -> str:
    return text_utils_mod.get_area(str(filepath).strip(), min_depth=min_depth)


def strip_c_style_comments(text: str) -> str:
    if not text:
        return ""
    return text_utils_mod.strip_c_style_comments(text)


def is_numeric(value: object) -> bool:
    return text_utils_mod.is_numeric(value)


__all__ = [
    "get_area",
    "get_project_root",
    "is_numeric",
    "read_code_snippet",
    "strip_c_style_comments",
]
