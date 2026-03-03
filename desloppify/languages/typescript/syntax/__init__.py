"""Shared TypeScript syntax helpers used across detectors and fixers."""

from desloppify.languages.typescript.syntax.scanner import scan_code

_TS_SYNTAX_SHIM = __name__

__all__ = ["scan_code"]
