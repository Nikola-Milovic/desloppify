"""Compatibility exports for detector data types.

Canonical definitions live in ``desloppify.core.detector_types`` so both
engine and language framework layers can depend on the same neutral module.
"""

from __future__ import annotations

from desloppify.core.detector_types import (
    ClassInfo,
    ComplexitySignal,
    ELEVATED_LOC_THRESHOLD,
    ELEVATED_NESTING_THRESHOLD,
    ELEVATED_PARAMS_THRESHOLD,
    FunctionInfo,
    GodRule,
)

__all__ = [
    "ClassInfo",
    "ComplexitySignal",
    "ELEVATED_LOC_THRESHOLD",
    "ELEVATED_NESTING_THRESHOLD",
    "ELEVATED_PARAMS_THRESHOLD",
    "FunctionInfo",
    "GodRule",
]
