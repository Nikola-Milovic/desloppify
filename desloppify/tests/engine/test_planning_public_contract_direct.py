"""Direct coverage for planning package public-contract documentation."""

from __future__ import annotations

import inspect

import desloppify.engine.planning as planning_mod


def test_planning_doc_points_callers_to_engine_plan_facade() -> None:
    src = inspect.getsource(planning_mod)
    assert "use ``engine.plan``" in src
    assert "use ``engine._plan``." not in src
