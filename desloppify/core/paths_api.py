"""Public path and snippet helpers used by command/runtime code."""

from __future__ import annotations

import os
from pathlib import Path

from desloppify.core._internal import text_utils as _text_utils

PROJECT_ROOT = _text_utils.get_project_root().resolve()
DEFAULT_PATH = PROJECT_ROOT / "src"
SRC_PATH = PROJECT_ROOT / os.environ.get("DESLOPPIFY_SRC", "src")


def get_project_root() -> Path:
    """Return the runtime project root."""
    return _text_utils.get_project_root().resolve()


def get_default_path() -> Path:
    """Return default scan path."""
    return get_project_root() / "src"


def get_src_path() -> Path:
    """Return TypeScript source root."""
    return get_project_root() / os.environ.get("DESLOPPIFY_SRC", "src")


def read_code_snippet(
    filepath: str,
    line: int,
    context: int = 1,
    *,
    project_root: Path | str | None = None,
) -> str | None:
    """Read a snippet around a 1-based line number."""
    return _text_utils.read_code_snippet(
        filepath,
        line,
        context,
        project_root=(
            Path(project_root).resolve()
            if project_root is not None
            else get_project_root()
        ),
    )


def get_area(filepath: str, *, min_depth: int = 2) -> str:
    """Public wrapper for area derivation from a relative file path."""
    return _text_utils.get_area(filepath, min_depth=min_depth)


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_PATH",
    "SRC_PATH",
    "get_area",
    "get_project_root",
    "get_default_path",
    "get_src_path",
    "read_code_snippet",
]
