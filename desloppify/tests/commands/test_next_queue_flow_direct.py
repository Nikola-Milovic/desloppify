"""Direct tests for next queue flow helpers."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

import desloppify.app.commands.next.queue_flow as queue_flow_mod


def test_build_next_payload_includes_scores_and_subjective_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        queue_flow_mod.state_mod,
        "score_snapshot",
        lambda _state: SimpleNamespace(overall=91.0, objective=93.5, strict=90.2),
    )
    monkeypatch.setattr(
        queue_flow_mod,
        "scorecard_dimensions_payload",
        lambda *_a, **_k: [
            {"name": "Test health", "subjective": False},
            {"name": "Naming quality", "subjective": True},
        ],
    )
    monkeypatch.setattr(
        queue_flow_mod.next_output_mod,
        "build_query_payload",
        lambda *_a, **_k: {"command": "next"},
    )

    payload = queue_flow_mod._build_next_payload(
        queue={"items": []},
        items=[],
        state={},
        narrative={},
        plan_data=None,
    )

    assert payload["overall_score"] == 91.0
    assert payload["objective_score"] == 93.5
    assert payload["strict_score"] == 90.2
    assert payload["subjective_measures"] == [{"name": "Naming quality", "subjective": True}]


def test_emit_requested_output_raises_when_output_file_write_fails(monkeypatch) -> None:
    opts = queue_flow_mod.NextOptions(output_file="out.json", output_format="terminal")
    monkeypatch.setattr(
        queue_flow_mod.next_output_mod,
        "write_output_file",
        lambda *_a, **_k: False,
    )

    with pytest.raises(queue_flow_mod.CommandError):
        queue_flow_mod._emit_requested_output(opts, payload={}, items=[])


def _args(**overrides):
    base = {
        "count": 1,
        "scope": None,
        "status": "open",
        "group": "item",
        "explain": False,
        "cluster": None,
        "include_skipped": False,
        "output": None,
        "format": "terminal",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_and_render_queue_empty_queue_writes_payload(monkeypatch) -> None:
    written: list[dict] = []
    monkeypatch.setattr(queue_flow_mod, "triage_guardrail_messages", lambda **_k: ["guard"])
    monkeypatch.setattr(queue_flow_mod, "target_strict_score_from_config", lambda _cfg: 95.0)
    monkeypatch.setattr(queue_flow_mod, "queue_context", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(queue_flow_mod, "_resolve_cluster_focus", lambda *_a, **_k: None)
    monkeypatch.setattr(queue_flow_mod, "_render_queue_header", lambda *_a, **_k: None)
    monkeypatch.setattr(queue_flow_mod, "_show_empty_queue", lambda *_a, **_k: False)
    monkeypatch.setattr(queue_flow_mod, "_plan_queue_context", lambda **_k: (None, None))
    monkeypatch.setattr(
        queue_flow_mod,
        "_build_next_payload",
        lambda **_k: {"command": "next"},
    )
    monkeypatch.setattr(
        queue_flow_mod.state_mod,
        "score_snapshot",
        lambda _state: SimpleNamespace(strict=88.0),
    )

    queue_flow_mod.build_and_render_queue(
        _args(),
        state={"issues": {}, "dimension_scores": {}, "scan_path": "."},
        config={},
        load_plan_fn=lambda: {},
        build_work_queue_fn=lambda *_a, **_k: {"items": [], "total": 0},
        write_query_fn=lambda payload: written.append(payload),
    )

    assert written and written[0]["warnings"] == ["guard"]


def test_build_and_render_queue_non_terminal_path_renders_items(monkeypatch) -> None:
    rendered_items: list[list[dict]] = []
    user_messages: list[str] = []
    written: list[dict] = []
    monkeypatch.setattr(queue_flow_mod, "triage_guardrail_messages", lambda **_k: [])
    monkeypatch.setattr(queue_flow_mod, "target_strict_score_from_config", lambda _cfg: 95.0)
    monkeypatch.setattr(queue_flow_mod, "queue_context", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(queue_flow_mod, "_resolve_cluster_focus", lambda *_a, **_k: None)
    monkeypatch.setattr(queue_flow_mod, "_render_queue_header", lambda *_a, **_k: None)
    monkeypatch.setattr(queue_flow_mod, "_show_empty_queue", lambda *_a, **_k: False)
    monkeypatch.setattr(
        queue_flow_mod,
        "_plan_queue_context",
        lambda **_k: (80.0, SimpleNamespace(queue_total=1)),
    )
    monkeypatch.setattr(
        queue_flow_mod,
        "_build_next_payload",
        lambda **_k: {"command": "next"},
    )
    monkeypatch.setattr(queue_flow_mod, "compute_narrative", lambda *_a, **_k: {"narrative": 1})
    monkeypatch.setattr(
        queue_flow_mod.state_mod,
        "score_snapshot",
        lambda _state: SimpleNamespace(strict=88.0),
    )
    monkeypatch.setattr(
        queue_flow_mod.state_mod,
        "path_scoped_issues",
        lambda issues, _scan_path: issues,
    )
    monkeypatch.setattr(
        queue_flow_mod.next_render_mod,
        "render_terminal_items",
        lambda items, *_a, **_k: rendered_items.append(items),
    )
    monkeypatch.setattr(
        queue_flow_mod.next_nudges_mod,
        "render_single_item_resolution_hint",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        queue_flow_mod.next_nudges_mod,
        "render_uncommitted_reminder",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        queue_flow_mod.next_nudges_mod,
        "render_followup_nudges",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(queue_flow_mod, "print_user_message", lambda msg: user_messages.append(msg))
    monkeypatch.setattr(
        queue_flow_mod,
        "_emit_requested_output",
        lambda *_a, **_k: False,
    )

    item = {"id": "smells::a.py::x", "detector": "smells"}
    queue_flow_mod.build_and_render_queue(
        _args(),
        state={"issues": {"x": {}}, "dimension_scores": {}, "scan_path": "."},
        config={},
        resolve_lang_fn=lambda _args: SimpleNamespace(name="python"),
        load_plan_fn=lambda: {"queue_order": ["x"]},
        build_work_queue_fn=lambda *_a, **_k: {"items": [item], "total": 1},
        write_query_fn=lambda payload: written.append(payload),
    )

    assert written and written[0]["command"] == "next"
    assert rendered_items == [[item]]
    assert user_messages
