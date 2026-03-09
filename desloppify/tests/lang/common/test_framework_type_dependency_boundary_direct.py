"""Direct coverage for neutral detector-type dependency boundaries."""

from __future__ import annotations

import inspect

import desloppify.core.detector_types as core_types_mod
import desloppify.engine.detectors.base as detector_base_mod
import desloppify.languages._framework.base.types as framework_types_mod


def test_framework_types_import_function_info_from_neutral_module() -> None:
    src = inspect.getsource(framework_types_mod)
    assert "from desloppify.core.detector_types import FunctionInfo" in src
    assert "from desloppify.engine.detectors.base import FunctionInfo" not in src


def test_engine_detector_base_reexports_neutral_types() -> None:
    assert detector_base_mod.FunctionInfo is core_types_mod.FunctionInfo
    assert detector_base_mod.ClassInfo is core_types_mod.ClassInfo
    assert detector_base_mod.ComplexitySignal is core_types_mod.ComplexitySignal
    assert detector_base_mod.GodRule is core_types_mod.GodRule
