"""Direct tests for review batch execution helper modules."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from desloppify.app.commands.review.batch import execution_adapter as adapter_mod
from desloppify.app.commands.review.batch import execution_dry_run as dry_run_mod
from desloppify.app.commands.review.batch import execution_progress as progress_mod
from desloppify.app.commands.review.batch import execution_results as results_mod
from desloppify.app.commands.review.runner_parallel import BatchProgressEvent
from desloppify.base.exception_sets import CommandError


def _safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_maybe_handle_dry_run_false_noop(tmp_path: Path) -> None:
    logs: list[str] = []
    handled = dry_run_mod.maybe_handle_dry_run(
        args=SimpleNamespace(dry_run=False),
        stamp="s1",
        selected_indexes=[0],
        run_dir=tmp_path / "run",
        logs_dir=tmp_path / "run" / "logs",
        immutable_packet_path=tmp_path / "immutable.json",
        prompt_packet_path=tmp_path / "prompt.json",
        prompt_files={0: tmp_path / "run" / "prompts" / "batch-1.md"},
        output_files={0: tmp_path / "run" / "results" / "batch-1.json"},
        safe_write_text_fn=_safe_write_text,
        colorize_fn=lambda text, _tone=None: text,
        append_run_log=logs.append,
    )
    assert handled is False
    assert logs == []


def test_maybe_handle_dry_run_writes_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    logs: list[str] = []
    handled = dry_run_mod.maybe_handle_dry_run(
        args=SimpleNamespace(dry_run=True),
        stamp="s2",
        selected_indexes=[0, 2],
        run_dir=run_dir,
        logs_dir=run_dir / "logs",
        immutable_packet_path=tmp_path / "immutable.json",
        prompt_packet_path=tmp_path / "prompt.json",
        prompt_files={
            0: run_dir / "prompts" / "batch-1.md",
            2: run_dir / "prompts" / "batch-3.md",
        },
        output_files={
            0: run_dir / "results" / "batch-1.json",
            2: run_dir / "results" / "batch-3.json",
        },
        safe_write_text_fn=_safe_write_text,
        colorize_fn=lambda text, _tone=None: text,
        append_run_log=logs.append,
    )
    assert handled is True
    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["runner"] == "dry-run"
    assert summary["selected_batches"] == [1, 3]
    assert "run-finished dry-run" in logs


def test_progress_reporter_tracks_lifecycle_and_stalls(tmp_path: Path) -> None:
    logs: list[str] = []
    batch_status: dict[str, dict[str, object]] = {}
    stall_warned: set[int] = set()
    reporter = progress_mod.build_progress_reporter(
        batch_positions={0: 1, 1: 2},
        batch_status=batch_status,
        stall_warned_batches=stall_warned,
        total_batches=2,
        stall_warning_seconds=5.0,
        prompt_files={0: tmp_path / "p0", 1: tmp_path / "p1"},
        output_files={0: tmp_path / "o0", 1: tmp_path / "o1"},
        log_files={0: tmp_path / "l0", 1: tmp_path / "l1"},
        append_run_log=logs.append,
        colorize_fn=lambda text, _tone=None: text,
    )
    reporter(BatchProgressEvent(batch_index=0, event="queued"))
    reporter(BatchProgressEvent(batch_index=0, event="start"))
    reporter(
        BatchProgressEvent(
            batch_index=0,
            event="heartbeat",
            details={"active_batches": [0], "queued_batches": [1], "elapsed_seconds": {0: 7}},
        )
    )
    reporter(BatchProgressEvent(batch_index=0, event="done", code=0, details={"elapsed_seconds": 9}))
    assert batch_status["1"]["status"] == "succeeded"
    assert batch_status["1"]["elapsed_seconds"] == 9
    assert 0 not in stall_warned
    assert any("stall-warning" in line for line in logs)
    assert any("batch-done batch=1" in line for line in logs)


def test_collect_and_reconcile_results_marks_failure_modes(tmp_path: Path) -> None:
    out0 = tmp_path / "out0.json"
    out0.write_text("{}")
    output_files = {0: out0, 1: tmp_path / "out1.json", 2: tmp_path / "out2.json"}
    batch_status: dict[str, dict[str, object]] = {}
    batch_results, successful, failures, failure_set = results_mod.collect_and_reconcile_results(
        collect_batch_results_fn=lambda **_kwargs: ([{"ok": True}], [1, 2]),
        selected_indexes=[0, 1, 2],
        execution_failures=[1],
        output_files=output_files,
        packet={"dimensions": ["design_coherence"]},
        batch_positions={0: 1, 1: 2, 2: 3},
        batch_status=batch_status,
    )
    assert batch_results == [{"ok": True}]
    assert successful == [0]
    assert failures == [1, 2]
    assert failure_set == {1, 2}
    assert batch_status["1"]["status"] == "succeeded"
    assert batch_status["2"]["status"] == "failed"
    assert batch_status["3"]["status"] == "missing_output"


def test_merge_and_finalize_helpers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        results_mod,
        "collect_reviewed_files_from_batches",
        lambda **_kwargs: ["a.py", "b.py"],
    )
    monkeypatch.setattr(results_mod, "normalize_dimension_list", lambda dims: [str(d) for d in dims if d])
    monkeypatch.setattr(
        results_mod,
        "print_import_dimension_coverage_notice",
        lambda **_kwargs: ["missing_dim"],
    )
    monkeypatch.setattr(results_mod, "print_review_quality", lambda *_args, **_kwargs: None)

    run_dir = tmp_path / "run"
    merged_path, missing = results_mod.merge_and_write_results(
        merge_batch_results_fn=lambda _batch_results: {
            "assessments": {"design_coherence": 80.0},
            "issues": [{"dimension": "type_safety"}],
            "review_quality": {"overall": 0.8},
        },
        build_import_provenance_fn=lambda **_kwargs: {"trusted": True},
        batch_results=[{"dummy": True}],
        batches=[{"name": "Full codebase sweep"}],
        successful_indexes=[0],
        packet={"dimensions": ["design_coherence"], "total_files": 10},
        packet_dimensions=["design_coherence"],
        scored_dimensions=["design_coherence", "type_safety"],
        scan_path=".",
        runner="codex",
        prompt_packet_path=tmp_path / "packet.json",
        stamp="r1",
        run_dir=run_dir,
        safe_write_text_fn=_safe_write_text,
        colorize_fn=lambda text, _tone=None: text,
    )
    assert merged_path.exists()
    assert missing == ["missing_dim"]
    merged_payload = json.loads(merged_path.read_text())
    assert merged_payload["review_scope"]["reviewed_files_count"] == 2
    assert merged_payload["provenance"]["trusted"] is True

    logs: list[str] = []
    args = SimpleNamespace(scan_after_import=True, path=".")
    results_mod.import_and_finalize(
        do_import_fn=lambda *_args, **_kwargs: None,
        run_followup_scan_fn=lambda **_kwargs: 0,
        merged_path=merged_path,
        state={},
        lang=SimpleNamespace(name="python"),
        state_file=tmp_path / "state.json",
        config={},
        allow_partial=False,
        successful_indexes=[0],
        failure_set={1},
        append_run_log=logs.append,
        args=args,
    )
    assert any("run-finished" in line for line in logs)


def test_import_and_finalize_raises_when_followup_scan_fails(tmp_path: Path) -> None:
    merged_path = tmp_path / "merged.json"
    merged_path.write_text("{}")
    args = SimpleNamespace(scan_after_import=True, path=".")
    with pytest.raises(CommandError):
        results_mod.import_and_finalize(
            do_import_fn=lambda *_args, **_kwargs: None,
            run_followup_scan_fn=lambda **_kwargs: 7,
            merged_path=merged_path,
            state={},
            lang=SimpleNamespace(name="python"),
            state_file=tmp_path / "state.json",
            config={},
            allow_partial=False,
            successful_indexes=[],
            failure_set=set(),
            append_run_log=lambda _msg: None,
            args=args,
        )


def test_execution_adapter_builds_runtime_wrappers(tmp_path: Path) -> None:
    calls: dict[str, object] = {}
    args = SimpleNamespace(only_batches="1")
    policy = SimpleNamespace(
        batch_timeout_seconds=120,
        batch_max_retries=3,
        batch_retry_backoff_seconds=2.0,
        heartbeat_seconds=4.0,
        stall_kill_seconds=45.0,
    )

    def _selected_raw(*, raw_selection, batch_count, parse_fn, colorize_fn):
        calls["selected_raw"] = {
            "raw_selection": raw_selection,
            "batch_count": batch_count,
            "parse_fn": parse_fn,
            "colorize_fn": colorize_fn,
        }
        return [0]

    def _prepare_raw(
        *,
        stamp,
        selected_indexes,
        batches,
        packet_path,
        run_root,
        repo_root,
        build_prompt_fn,
        safe_write_text_fn,
        colorize_fn,
    ):
        calls["prepare_raw"] = {
            "stamp": stamp,
            "selected_indexes": selected_indexes,
            "batches": batches,
            "packet_path": packet_path,
            "run_root": run_root,
            "repo_root": repo_root,
            "build_prompt_fn": build_prompt_fn,
            "safe_write_text_fn": safe_write_text_fn,
            "colorize_fn": colorize_fn,
        }
        return ("run", "logs", {}, {}, {})

    def _run_codex_raw(*, prompt, repo_root, output_file, log_file, deps):
        calls["run_codex_raw"] = {
            "prompt": prompt,
            "repo_root": repo_root,
            "output_file": output_file,
            "log_file": log_file,
            "deps": deps,
        }
        return 0

    def _execute_raw(*, tasks, options, progress_fn=None, error_log_fn=None):
        calls["execute_raw"] = {
            "tasks": tasks,
            "options": options,
            "progress_fn": progress_fn,
            "error_log_fn": error_log_fn,
        }
        return []

    def _extract_raw(raw, *, log_fn):
        calls["extract_raw"] = {"raw": raw, "log_fn": log_fn}
        return {"issues": []}

    def _normalize_raw(payload, dims, *, max_batch_issues, abstraction_sub_axes):
        calls["normalize_raw"] = {
            "payload": payload,
            "dims": dims,
            "max_batch_issues": max_batch_issues,
            "abstraction_sub_axes": abstraction_sub_axes,
        }
        return payload

    def _collect_raw(
        *,
        selected_indexes,
        failures,
        output_files,
        allowed_dims,
        extract_payload_fn,
        normalize_result_fn,
    ):
        calls["collect_raw"] = {
            "selected_indexes": selected_indexes,
            "failures": failures,
            "output_files": output_files,
            "allowed_dims": allowed_dims,
        }
        payload = extract_payload_fn("raw-result")
        normalize_result_fn(payload, ["dim_a"])
        return ([], [])

    def _followup_scan_raw(*, lang_name, scan_path, deps):
        calls["followup_scan_raw"] = {
            "lang_name": lang_name,
            "scan_path": scan_path,
            "deps": deps,
        }
        return 0

    kwargs = adapter_mod.build_execution_adapter_kwargs(
        args=args,
        policy=policy,
        runtime_project_root=tmp_path / "repo",
        subagent_runs_dir=tmp_path / "runs",
        run_stamp_fn=lambda: "stamp1",
        load_or_prepare_packet_fn=(
            lambda *_args, **_kwargs: ({}, tmp_path / "packet.json", tmp_path / "blind.json")
        ),
        selected_batch_indexes_fn_raw=_selected_raw,
        parse_batch_selection_fn=lambda raw, batch_count: [0],
        prepare_run_artifacts_fn_raw=_prepare_raw,
        build_prompt_fn=lambda *_args, **_kwargs: "prompt",
        run_codex_batch_fn_raw=_run_codex_raw,
        execute_batches_fn=_execute_raw,
        collect_batch_results_fn_raw=_collect_raw,
        extract_payload_fn=_extract_raw,
        normalize_batch_result_fn=_normalize_raw,
        max_batch_issues_for_dimension_count_fn=lambda count: count + 5,
        print_failures_fn=lambda **_kwargs: None,
        print_failures_and_raise_fn=lambda **_kwargs: None,
        merge_batch_results_fn=lambda _results: {},
        build_import_provenance_fn=lambda **_kwargs: {},
        do_import_fn=lambda *_args, **_kwargs: None,
        run_followup_scan_fn_raw=_followup_scan_raw,
        safe_write_text_fn=_safe_write_text,
        colorize_fn=lambda text, _tone=None: text,
        log_fn=lambda message: calls.setdefault("log_messages", []).append(message),
        abstraction_sub_axes=("axis_a",),
        followup_scan_timeout_seconds=900,
    )

    assert kwargs["project_root"] == tmp_path / "repo"
    assert kwargs["subagent_runs_dir"] == tmp_path / "runs"

    selected = kwargs["selected_batch_indexes_fn"](SimpleNamespace(only_batches="2"), batch_count=3)
    assert selected == [0]
    selected_raw = calls["selected_raw"]
    assert selected_raw["raw_selection"] == "1"
    assert selected_raw["batch_count"] == 3
    assert callable(selected_raw["parse_fn"])
    assert callable(selected_raw["colorize_fn"])

    kwargs["prepare_run_artifacts_fn"](
        stamp="s1",
        selected_indexes=[0],
        batches=[{"name": "b1"}],
        packet_path=tmp_path / "packet.json",
        run_root=tmp_path / "runs",
        repo_root=tmp_path / "repo",
    )
    assert "prepare_raw" in calls

    kwargs["run_codex_batch_fn"](
        prompt="prompt",
        repo_root=tmp_path / "repo",
        output_file=tmp_path / "out.json",
        log_file=tmp_path / "log.txt",
    )
    deps = calls["run_codex_raw"]["deps"]
    assert deps.timeout_seconds == 120
    assert deps.max_retries == 3
    assert deps.retry_backoff_seconds == 2.0
    assert deps.live_log_interval_seconds == 4.0
    assert deps.stall_after_output_seconds == 45.0

    kwargs["execute_batches_fn"](
        tasks=[],
        options=SimpleNamespace(run_parallel=True, max_parallel_workers=2, heartbeat_seconds=6.0),
    )
    execute_options = calls["execute_raw"]["options"]
    assert execute_options.run_parallel is True
    assert execute_options.max_parallel_workers == 2
    assert execute_options.heartbeat_seconds == 6.0

    kwargs["collect_batch_results_fn"](
        selected_indexes=[0],
        failures=[],
        output_files={0: tmp_path / "out.json"},
        allowed_dims={"dim_a"},
    )
    assert calls["extract_raw"]["raw"] == "raw-result"
    assert calls["normalize_raw"]["max_batch_issues"] == 6
    assert calls["normalize_raw"]["abstraction_sub_axes"] == ("axis_a",)

    kwargs["run_followup_scan_fn"](lang_name="python", scan_path=".")
    followup_deps = calls["followup_scan_raw"]["deps"]
    assert followup_deps.project_root == tmp_path / "repo"
    assert followup_deps.timeout_seconds == 900
