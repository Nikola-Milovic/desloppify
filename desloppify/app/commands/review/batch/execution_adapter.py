"""Dependency wiring adapter for review batch execution core."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from ..runner_parallel import BatchExecutionOptions
from ..runner_process import CodexBatchRunnerDeps, FollowupScanDeps


def build_execution_adapter_kwargs(
    *,
    args,
    policy,
    runtime_project_root: Path,
    subagent_runs_dir: Path,
    run_stamp_fn,
    load_or_prepare_packet_fn,
    selected_batch_indexes_fn_raw,
    parse_batch_selection_fn,
    prepare_run_artifacts_fn_raw,
    build_prompt_fn,
    run_codex_batch_fn_raw,
    execute_batches_fn,
    collect_batch_results_fn_raw,
    extract_payload_fn,
    normalize_batch_result_fn,
    max_batch_issues_for_dimension_count_fn,
    print_failures_fn,
    print_failures_and_raise_fn,
    merge_batch_results_fn,
    build_import_provenance_fn,
    do_import_fn,
    run_followup_scan_fn_raw,
    safe_write_text_fn,
    colorize_fn,
    log_fn,
    abstraction_sub_axes: tuple[str, ...],
    followup_scan_timeout_seconds: int,
) -> dict[str, Any]:
    """Build kwargs that adapt CLI/runtime wiring to execution.do_run_batches."""
    batch_timeout_seconds = policy.batch_timeout_seconds
    batch_max_retries = policy.batch_max_retries
    batch_retry_backoff_seconds = policy.batch_retry_backoff_seconds
    batch_heartbeat_seconds = policy.heartbeat_seconds
    batch_live_log_interval_seconds = (
        max(1.0, min(batch_heartbeat_seconds, 10.0))
        if batch_heartbeat_seconds > 0
        else 5.0
    )
    batch_stall_kill_seconds = policy.stall_kill_seconds

    def _prepare_run_artifacts(*, stamp, selected_indexes, batches, packet_path, run_root, repo_root):
        return prepare_run_artifacts_fn_raw(
            stamp=stamp,
            selected_indexes=selected_indexes,
            batches=batches,
            packet_path=packet_path,
            run_root=run_root,
            repo_root=repo_root,
            build_prompt_fn=build_prompt_fn,
            safe_write_text_fn=safe_write_text_fn,
            colorize_fn=colorize_fn,
        )

    def _collect_batch_results(*, selected_indexes, failures, output_files, allowed_dims):
        return collect_batch_results_fn_raw(
            selected_indexes=selected_indexes,
            failures=failures,
            output_files=output_files,
            allowed_dims=allowed_dims,
            extract_payload_fn=lambda raw: extract_payload_fn(raw, log_fn=log_fn),
            normalize_result_fn=lambda payload, dims: normalize_batch_result_fn(
                payload,
                dims,
                max_batch_issues=max_batch_issues_for_dimension_count_fn(len(dims)),
                abstraction_sub_axes=abstraction_sub_axes,
            ),
        )

    return {
        "run_stamp_fn": run_stamp_fn,
        "load_or_prepare_packet_fn": load_or_prepare_packet_fn,
        "selected_batch_indexes_fn": (
            lambda _args, *, batch_count: selected_batch_indexes_fn_raw(
                raw_selection=getattr(args, "only_batches", None),
                batch_count=batch_count,
                parse_fn=parse_batch_selection_fn,
                colorize_fn=colorize_fn,
            )
        ),
        "prepare_run_artifacts_fn": _prepare_run_artifacts,
        "run_codex_batch_fn": (
            lambda *, prompt, repo_root, output_file, log_file: run_codex_batch_fn_raw(
                prompt=prompt,
                repo_root=repo_root,
                output_file=output_file,
                log_file=log_file,
                deps=CodexBatchRunnerDeps(
                    timeout_seconds=batch_timeout_seconds,
                    subprocess_run=subprocess.run,
                    timeout_error=subprocess.TimeoutExpired,
                    safe_write_text_fn=safe_write_text_fn,
                    use_popen_runner=(getattr(subprocess.run, "__module__", "") == "subprocess"),
                    subprocess_popen=subprocess.Popen,
                    live_log_interval_seconds=batch_live_log_interval_seconds,
                    stall_after_output_seconds=batch_stall_kill_seconds,
                    max_retries=batch_max_retries,
                    retry_backoff_seconds=batch_retry_backoff_seconds,
                ),
            )
        ),
        "execute_batches_fn": (
            lambda **kwargs: execute_batches_fn(
                tasks=kwargs["tasks"],
                options=BatchExecutionOptions(
                    run_parallel=kwargs["options"].run_parallel,
                    max_parallel_workers=kwargs["options"].max_parallel_workers,
                    heartbeat_seconds=kwargs["options"].heartbeat_seconds,
                ),
                progress_fn=kwargs.get("progress_fn"),
                error_log_fn=kwargs.get("error_log_fn"),
            )
        ),
        "collect_batch_results_fn": _collect_batch_results,
        "print_failures_fn": print_failures_fn,
        "print_failures_and_raise_fn": print_failures_and_raise_fn,
        "merge_batch_results_fn": merge_batch_results_fn,
        "build_import_provenance_fn": build_import_provenance_fn,
        "do_import_fn": do_import_fn,
        "run_followup_scan_fn": (
            lambda *, lang_name, scan_path: run_followup_scan_fn_raw(
                lang_name=lang_name,
                scan_path=scan_path,
                deps=FollowupScanDeps(
                    project_root=runtime_project_root,
                    timeout_seconds=followup_scan_timeout_seconds,
                    python_executable=sys.executable,
                    subprocess_run=subprocess.run,
                    timeout_error=subprocess.TimeoutExpired,
                    colorize_fn=colorize_fn,
                ),
            )
        ),
        "safe_write_text_fn": safe_write_text_fn,
        "colorize_fn": colorize_fn,
        "project_root": runtime_project_root,
        "subagent_runs_dir": subagent_runs_dir,
    }


__all__ = ["build_execution_adapter_kwargs"]
