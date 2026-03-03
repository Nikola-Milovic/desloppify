"""Cross-platform grep-style helpers."""

from __future__ import annotations

import os
import re

from desloppify.core.source_discovery import read_file_text as _read_file_text
from desloppify.core.text.text_api import get_project_root
from desloppify.search import grep_files_containing as _search_grep_files_containing


def grep_files(
    pattern: str, file_list: list[str], *, flags: int = 0
) -> list[tuple[str, int, str]]:
    """Search files for a regex pattern. Returns (filepath, lineno, line_text)."""
    compiled = re.compile(pattern, flags)
    results: list[tuple[str, int, str]] = []
    for filepath in file_list:
        abs_path = filepath if os.path.isabs(filepath) else str(get_project_root() / filepath)
        content = _read_file_text(abs_path)
        if content is None:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if compiled.search(line):
                results.append((filepath, lineno, line))
    return results


def grep_files_containing(
    names: set[str], file_list: list[str], *, word_boundary: bool = True
) -> dict[str, set[str]]:
    r"""Find which files contain which names. Returns {name: set(filepaths)}."""
    return _search_grep_files_containing(
        names,
        file_list,
        word_boundary=word_boundary,
    )


def grep_count_files(
    name: str, file_list: list[str], *, word_boundary: bool = True
) -> list[str]:
    """Return list of files containing name."""
    if word_boundary:
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
    else:
        pat = re.compile(re.escape(name))
    matching: list[str] = []
    for filepath in file_list:
        abs_path = filepath if os.path.isabs(filepath) else str(get_project_root() / filepath)
        content = _read_file_text(abs_path)
        if content is None:
            continue
        if pat.search(content):
            matching.append(filepath)
    return matching


__all__ = [
    "grep_count_files",
    "grep_files",
    "grep_files_containing",
]
