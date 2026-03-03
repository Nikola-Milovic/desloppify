"""Subprocess-oriented runner helpers for review batch execution."""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from desloppify.app.commands.review.runner_failures import (
    TRANSIENT_RUNNER_PHRASES as _TRANSIENT_RUNNER_PHRASES,
)
from desloppify.core.fallbacks import log_best_effort_failure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexBatchRunnerDeps:
    timeout_seconds: int
    subprocess_run: object
    timeout_error: type[BaseException]
    safe_write_text_fn: object
    use_popen_runner: bool = False
    subprocess_popen: object | None = None
    live_log_interval_seconds: float = 5.0
    stall_after_output_seconds: int = 90
    max_retries: int = 0
    retry_backoff_seconds: float = 0.0
    sleep_fn: object = time.sleep


@dataclass(frozen=True)
class FollowupScanDeps:
    project_root: Path
    timeout_seconds: int
    python_executable: str
    subprocess_run: object
    timeout_error: type[BaseException]
    colorize_fn: object


@dataclass
class _RunnerState:
    """Mutable state shared between threads during a batch run."""

    stdout_chunks: list[str] = field(default_factory=list)
    stderr_chunks: list[str] = field(default_factory=list)
    runner_note: str = ""
    last_stream_activity: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class _AttemptContext:
    """Immutable per-attempt context bundling values that closures captured."""

    header: str
    started_at_iso: str
    started_monotonic: float
    output_file: Path
    log_file: Path
    log_sections: list[str]
    safe_write_text_fn: object


@dataclass
class _ExecutionResult:
    """Unified return from both execution paths."""

    code: int
    stdout_text: str
    stderr_text: str
    timed_out: bool = False
    stalled: bool = False
    recovered_from_stall: bool = False
    early_return: int | None = None


@dataclass(frozen=True)
class _RetryConfig:
    """Normalized retry/runtime policy for codex batch attempts."""

    max_attempts: int
    retry_backoff_seconds: float
    live_log_interval: float
    stall_seconds: int
    use_popen: bool


def codex_batch_command(*, prompt: str, repo_root: Path, output_file: Path) -> list[str]:
    """Build one codex exec command line for a batch prompt."""
    effort = os.environ.get("DESLOPPIFY_CODEX_REASONING_EFFORT", "low").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh"}:
        effort = "low"
    return [
        "codex",
        "exec",
        "--ephemeral",
        "-C",
        str(repo_root),
        "-s",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-o",
        str(output_file),
        prompt,
    ]


def _output_file_status_text(output_file: Path) -> str:
    """Describe output file state for live log snapshots."""
    if not output_file.exists():
        return f"{output_file} (missing)"
    try:
        stat = output_file.stat()
    except OSError as exc:
        return f"{output_file} (exists; stat failed: {exc})"
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(
        timespec="seconds"
    )
    return f"{output_file} (exists; bytes={stat.st_size}; modified={modified_at})"


def _output_file_has_json_payload(output_file: Path) -> bool:
    """Return True when the output file contains a valid JSON object."""
    if not output_file.exists():
        return False
    try:
        payload = json.loads(output_file.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


def _extract_payload_from_log(
    batch_index: int,
    raw_path: Path,
    extract_fn,
) -> dict[str, object] | None:
    """Try to recover a batch payload from the runner log file."""
    log_path = raw_path.parent.parent / "logs" / f"batch-{batch_index + 1}.log"
    if not log_path.exists():
        return None
    try:
        log_text = log_path.read_text()
    except OSError:
        return None

    stdout_marker = "\nSTDOUT:\n"
    stderr_marker = "\n\nSTDERR:\n"
    stdout_start = log_text.rfind(stdout_marker)
    if stdout_start == -1 and log_text.startswith("STDOUT:\n"):
        stdout_start = 0
        stdout_offset = len("STDOUT:\n")
    elif stdout_start >= 0:
        stdout_offset = len(stdout_marker)
    else:
        stdout_offset = 0
    if stdout_start >= 0:
        start_idx = stdout_start + stdout_offset
        stdout_end = log_text.find(stderr_marker, start_idx)
        stdout_text = (
            log_text[start_idx:] if stdout_end == -1 else log_text[start_idx:stdout_end]
        )
        payload = extract_fn(stdout_text)
        if payload is not None:
            return payload
        # If the batch log has a concrete STDOUT section but it contains no parseable
        # payload, do not fallback to parsing the whole log. Full logs include the
        # prompt template (often with JSON examples), which can hide true STDERR failures.
        return None

    return extract_fn(log_text)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate (then kill) a subprocess that may still be running."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
        return
    except (OSError, subprocess.SubprocessError) as exc:
        log_best_effort_failure(
            logger,
            "terminate review subprocess before kill fallback",
            exc,
        )
    try:
        process.kill()
        process.wait(timeout=3)
    except (OSError, subprocess.SubprocessError):
        return


def _drain_stream(stream, sink: list[str], state: _RunnerState) -> None:
    """Read lines from *stream* into *sink*, updating activity timestamp."""
    if stream is None:
        return
    try:
        for chunk in iter(stream.readline, ""):
            if not chunk:
                break
            with state.lock:
                sink.append(chunk)
                state.last_stream_activity = time.monotonic()
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive boundary
        with state.lock:
            sink.append(f"\n[stream read error: {exc}]\n")
    finally:
        try:
            stream.close()
        except (OSError, ValueError) as exc:
            log_best_effort_failure(logger, "close review batch stream", exc)


def _write_live_snapshot(state: _RunnerState, ctx: _AttemptContext) -> None:
    """Write a point-in-time log snapshot while the runner is active."""
    elapsed_seconds = int(max(0.0, time.monotonic() - ctx.started_monotonic))
    with state.lock:
        stdout_preview = "".join(state.stdout_chunks)
        stderr_preview = "".join(state.stderr_chunks)
        note = state.runner_note
    note_block = f"\nRUNNER NOTE: {note}" if note else ""
    ctx.safe_write_text_fn(
        ctx.log_file,
        "\n\n".join(
            ctx.log_sections
            + [
                (
                    f"{ctx.header}\n\n"
                    "STATUS: running\n"
                    f"STARTED AT: {ctx.started_at_iso}\n"
                    f"ELAPSED: {elapsed_seconds}s\n"
                    f"OUTPUT FILE: {_output_file_status_text(ctx.output_file)}"
                    f"{note_block}\n\n"
                    f"STDOUT (live):\n{stdout_preview}\n\n"
                    f"STDERR (live):\n{stderr_preview}\n"
                )
            ]
        ),
    )


def _start_live_writer(
    state: _RunnerState,
    ctx: _AttemptContext,
    interval: float,
) -> threading.Thread:
    """Spawn a daemon thread that periodically writes live log snapshots."""

    def _loop() -> None:
        while not state.stop_event.wait(interval):
            _write_live_snapshot(state, ctx)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def _check_stall(
    output_file: Path,
    prev_sig: tuple[int, int] | None,
    prev_stable: float | None,
    now: float,
    last_activity: float,
    threshold: int,
) -> tuple[bool, tuple[int, int] | None, float | None]:
    """Check for runner stall. Returns (stalled, new_sig, new_stable_since)."""
    try:
        stat = output_file.stat()
        current_signature: tuple[int, int] | None = (
            int(stat.st_size),
            int(stat.st_mtime),
        )
    except OSError:
        current_signature = None
    if current_signature is None:
        baseline = prev_stable if isinstance(prev_stable, int | float) else now
        output_age = now - baseline
        stream_idle = now - last_activity
        if output_age >= threshold and stream_idle >= threshold:
            return True, None, baseline
        return False, None, baseline
    if current_signature != prev_sig:
        return False, current_signature, now
    if prev_stable is None:
        return False, prev_sig, prev_stable
    output_age = now - prev_stable
    stream_idle = now - last_activity
    if output_age >= threshold and stream_idle >= threshold:
        return True, prev_sig, prev_stable
    return False, prev_sig, prev_stable


def _run_via_popen(
    cmd: list[str],
    deps: CodexBatchRunnerDeps,
    state: _RunnerState,
    ctx: _AttemptContext,
    interval: float,
    stall_seconds: int,
) -> _ExecutionResult:
    """Execute batch via Popen with live streaming and stall recovery."""
    writer_thread = _start_live_writer(state, ctx, interval)
    try:
        process = deps.subprocess_popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        state.stop_event.set()
        writer_thread.join(timeout=2)
        ctx.log_sections.append(f"{ctx.header}\n\nRUNNER ERROR:\n{exc}\n")
        ctx.safe_write_text_fn(ctx.log_file, "\n\n".join(ctx.log_sections))
        return _ExecutionResult(code=127, stdout_text="", stderr_text="", early_return=127)
    except (
        RuntimeError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:  # pragma: no cover - defensive boundary
        state.stop_event.set()
        writer_thread.join(timeout=2)
        ctx.log_sections.append(f"{ctx.header}\n\nUNEXPECTED RUNNER ERROR:\n{exc}\n")
        ctx.safe_write_text_fn(ctx.log_file, "\n\n".join(ctx.log_sections))
        return _ExecutionResult(code=1, stdout_text="", stderr_text="", early_return=1)

    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stdout, state.stdout_chunks, state),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(process.stderr, state.stderr_chunks, state),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    stalled = False
    recovered_from_stall = False
    output_signature: tuple[int, int] | None = None
    output_stable_since: float | None = None

    while process.poll() is None:
        now_monotonic = time.monotonic()
        elapsed = int(max(0.0, now_monotonic - ctx.started_monotonic))
        if elapsed >= deps.timeout_seconds:
            with state.lock:
                state.runner_note = f"timeout after {deps.timeout_seconds}s"
            timed_out = True
            _terminate_process(process)
            break
        if stall_seconds > 0:
            with state.lock:
                last_activity = state.last_stream_activity
            stalled, output_signature, output_stable_since = _check_stall(
                ctx.output_file,
                output_signature,
                output_stable_since,
                now_monotonic,
                last_activity,
                stall_seconds,
            )
            if stalled:
                with state.lock:
                    state.runner_note = (
                        f"stall recovery triggered after {stall_seconds}s "
                        "with stable output state"
                    )
                recovered_from_stall = _output_file_has_json_payload(ctx.output_file)
                _terminate_process(process)
                break
        deps.sleep_fn(min(interval, 1.0))

    if process.poll() is None:
        _terminate_process(process)
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    state.stop_event.set()
    writer_thread.join(timeout=2)
    _write_live_snapshot(state, ctx)

    return _ExecutionResult(
        code=int(process.returncode or 0),
        stdout_text="".join(state.stdout_chunks),
        stderr_text="".join(state.stderr_chunks),
        timed_out=timed_out,
        stalled=stalled,
        recovered_from_stall=recovered_from_stall,
    )


def _run_via_subprocess(
    cmd: list[str],
    deps: CodexBatchRunnerDeps,
    state: _RunnerState,
    ctx: _AttemptContext,
    interval: float,
) -> _ExecutionResult:
    """Execute batch via subprocess.run."""
    writer_thread = _start_live_writer(state, ctx, interval)
    try:
        result = deps.subprocess_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=deps.timeout_seconds,
        )
    except deps.timeout_error as exc:
        state.stop_event.set()
        writer_thread.join(timeout=2)
        ctx.log_sections.append(
            f"{ctx.header}\n\nTIMEOUT after {deps.timeout_seconds}s\n{exc}\n"
        )
        ctx.safe_write_text_fn(ctx.log_file, "\n\n".join(ctx.log_sections))
        return _ExecutionResult(code=124, stdout_text="", stderr_text="", early_return=124)
    except OSError as exc:
        state.stop_event.set()
        writer_thread.join(timeout=2)
        ctx.log_sections.append(f"{ctx.header}\n\nRUNNER ERROR:\n{exc}\n")
        ctx.safe_write_text_fn(ctx.log_file, "\n\n".join(ctx.log_sections))
        return _ExecutionResult(code=127, stdout_text="", stderr_text="", early_return=127)
    except (RuntimeError, ValueError, TypeError) as exc:  # pragma: no cover - defensive boundary
        state.stop_event.set()
        writer_thread.join(timeout=2)
        ctx.log_sections.append(f"{ctx.header}\n\nUNEXPECTED RUNNER ERROR:\n{exc}\n")
        ctx.safe_write_text_fn(ctx.log_file, "\n\n".join(ctx.log_sections))
        return _ExecutionResult(code=1, stdout_text="", stderr_text="", early_return=1)
    finally:
        state.stop_event.set()
        writer_thread.join(timeout=2)

    return _ExecutionResult(
        code=int(result.returncode),
        stdout_text=result.stdout or "",
        stderr_text=result.stderr or "",
    )


def _resolve_retry_config(deps: CodexBatchRunnerDeps) -> _RetryConfig:
    retries_raw = deps.max_retries if isinstance(deps.max_retries, int) else 0
    max_retries = max(0, retries_raw)
    max_attempts = max_retries + 1
    backoff_raw = (
        float(deps.retry_backoff_seconds)
        if isinstance(deps.retry_backoff_seconds, int | float)
        else 0.0
    )
    retry_backoff_seconds = max(0.0, backoff_raw)
    live_log_interval = (
        float(deps.live_log_interval_seconds)
        if isinstance(deps.live_log_interval_seconds, int | float)
        and float(deps.live_log_interval_seconds) > 0
        else 5.0
    )
    stall_seconds = (
        int(deps.stall_after_output_seconds)
        if isinstance(deps.stall_after_output_seconds, int | float)
        and int(deps.stall_after_output_seconds) > 0
        else 0
    )
    use_popen = bool(deps.use_popen_runner) and callable(
        getattr(deps, "subprocess_popen", None)
    )
    return _RetryConfig(
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        live_log_interval=live_log_interval,
        stall_seconds=stall_seconds,
        use_popen=use_popen,
    )


def _run_batch_attempt(
    *,
    cmd: list[str],
    deps: CodexBatchRunnerDeps,
    output_file: Path,
    log_file: Path,
    log_sections: list[str],
    attempt: int,
    max_attempts: int,
    use_popen: bool,
    live_log_interval: float,
    stall_seconds: int,
) -> tuple[str, _ExecutionResult]:
    header = f"ATTEMPT {attempt}/{max_attempts}\n$ {' '.join(cmd)}"
    started_monotonic = time.monotonic()
    state = _RunnerState(last_stream_activity=started_monotonic)
    ctx = _AttemptContext(
        header=header,
        started_at_iso=datetime.now(UTC).isoformat(timespec="seconds"),
        started_monotonic=started_monotonic,
        output_file=output_file,
        log_file=log_file,
        log_sections=log_sections,
        safe_write_text_fn=deps.safe_write_text_fn,
    )
    _write_live_snapshot(state, ctx)
    if use_popen:
        result = _run_via_popen(
            cmd,
            deps,
            state,
            ctx,
            live_log_interval,
            stall_seconds,
        )
    else:
        result = _run_via_subprocess(cmd, deps, state, ctx, live_log_interval)
    return header, result


def _handle_early_attempt_return(result: _ExecutionResult) -> int | None:
    return result.early_return


def _handle_timeout_or_stall(
    *,
    header: str,
    result: _ExecutionResult,
    deps: CodexBatchRunnerDeps,
    output_file: Path,
    log_file: Path,
    log_sections: list[str],
    stall_seconds: int,
) -> int | None:
    if not result.timed_out and not result.stalled:
        return None
    if result.timed_out:
        log_sections.append(
            f"{header}\n\nTIMEOUT after {deps.timeout_seconds}s\n\n"
            f"STDOUT:\n{result.stdout_text}\n\nSTDERR:\n{result.stderr_text}\n"
        )
    else:
        log_sections.append(
            f"{header}\n\nSTALL RECOVERY after {stall_seconds}s "
            "of stable output and no stream activity.\n\n"
            f"STDOUT:\n{result.stdout_text}\n\nSTDERR:\n{result.stderr_text}\n"
        )
    if _output_file_has_json_payload(output_file):
        recovery_message = (
            "Recovered timed-out batch from JSON output file; "
            "continuing as success."
            if result.timed_out
            else "Recovered stalled batch from JSON output file; "
            "continuing as success."
        )
        log_sections.append(recovery_message)
        deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
        return 0
    deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
    return 124


def _handle_successful_attempt(
    *,
    result: _ExecutionResult,
    output_file: Path,
    log_file: Path,
    deps: CodexBatchRunnerDeps,
    log_sections: list[str],
) -> int | None:
    if result.code != 0:
        return None
    if not _output_file_has_json_payload(output_file):
        log_sections.append(
            "Runner exited 0 but output file is missing or invalid; "
            "treating as execution failure."
        )
        deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
        return 1
    deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
    return 0


def _handle_failed_attempt(
    *,
    result: _ExecutionResult,
    deps: CodexBatchRunnerDeps,
    attempt: int,
    max_attempts: int,
    retry_backoff_seconds: float,
    log_file: Path,
    log_sections: list[str],
) -> int | None:
    combined = f"{result.stdout_text}\n{result.stderr_text}".lower()
    is_transient = any(needle in combined for needle in _TRANSIENT_RUNNER_PHRASES)
    if not is_transient or attempt >= max_attempts:
        deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
        return result.code
    delay_seconds = retry_backoff_seconds * (2 ** (attempt - 1))
    log_sections.append(
        "Transient runner failure detected; "
        f"retrying in {delay_seconds:.1f}s (attempt {attempt + 1}/{max_attempts})."
    )
    try:
        if delay_seconds > 0:
            deps.sleep_fn(delay_seconds)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        log_sections.append(
            f"Retry delay hook failed: {exc} — aborting remaining retries."
        )
        deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
        return 1
    return None


def run_codex_batch(
    *,
    prompt: str,
    repo_root: Path,
    output_file: Path,
    log_file: Path,
    deps: CodexBatchRunnerDeps,
    codex_batch_command_fn=None,
) -> int:
    """Execute one codex batch and return a stable CLI-style status code."""
    if codex_batch_command_fn is None:
        codex_batch_command_fn = codex_batch_command
    cmd = codex_batch_command_fn(
        prompt=prompt,
        repo_root=repo_root,
        output_file=output_file,
    )
    config = _resolve_retry_config(deps)
    log_sections: list[str] = []

    for attempt in range(1, config.max_attempts + 1):
        header, result = _run_batch_attempt(
            cmd=cmd,
            deps=deps,
            output_file=output_file,
            log_file=log_file,
            log_sections=log_sections,
            attempt=attempt,
            max_attempts=config.max_attempts,
            use_popen=config.use_popen,
            live_log_interval=config.live_log_interval,
            stall_seconds=config.stall_seconds,
        )
        early_return = _handle_early_attempt_return(result)
        if early_return is not None:
            return early_return
        timeout_or_stall = _handle_timeout_or_stall(
            header=header,
            result=result,
            deps=deps,
            output_file=output_file,
            log_file=log_file,
            log_sections=log_sections,
            stall_seconds=config.stall_seconds,
        )
        if timeout_or_stall is not None:
            return timeout_or_stall

        log_sections.append(
            f"{header}\n\nSTDOUT:\n{result.stdout_text}\n\nSTDERR:\n{result.stderr_text}\n"
        )

        success_code = _handle_successful_attempt(
            result=result,
            output_file=output_file,
            log_file=log_file,
            deps=deps,
            log_sections=log_sections,
        )
        if success_code is not None:
            return success_code
        failure_code = _handle_failed_attempt(
            result=result,
            deps=deps,
            attempt=attempt,
            max_attempts=config.max_attempts,
            retry_backoff_seconds=config.retry_backoff_seconds,
            log_file=log_file,
            log_sections=log_sections,
        )
        if failure_code is not None:
            return failure_code

    deps.safe_write_text_fn(log_file, "\n\n".join(log_sections))
    return 1


def run_followup_scan(
    *,
    lang_name: str,
    scan_path: str,
    deps: FollowupScanDeps,
    force_queue_bypass: bool = False,
) -> int:
    """Run a follow-up scan and return a non-zero status when it fails."""
    scan_cmd = [
        deps.python_executable,
        "-m",
        "desloppify",
        "--lang",
        lang_name,
        "scan",
        "--path",
        scan_path,
    ]
    if force_queue_bypass:
        followup_attest = (
            "I understand this is not the intended workflow and "
            "I am intentionally skipping queue completion"
        )
        scan_cmd.extend(["--force-rescan", "--attest", followup_attest])
        print(
            deps.colorize_fn(
                "  Follow-up scan queue bypass enabled (--force-followup-scan).",
                "yellow",
            )
        )
    print(deps.colorize_fn("\n  Running follow-up scan...", "bold"))
    try:
        result = deps.subprocess_run(
            scan_cmd,
            cwd=str(deps.project_root),
            timeout=deps.timeout_seconds,
        )
    except deps.timeout_error:
        print(
            deps.colorize_fn(
                f"  Follow-up scan timed out after {deps.timeout_seconds}s.",
                "yellow",
            ),
            file=sys.stderr,
        )
        return 124
    except OSError as exc:
        print(
            deps.colorize_fn(f"  Follow-up scan failed: {exc}", "red"),
            file=sys.stderr,
        )
        return 1
    return int(getattr(result, "returncode", 0) or 0)


__all__ = [
    "CodexBatchRunnerDeps",
    "FollowupScanDeps",
    "codex_batch_command",
    "run_codex_batch",
    "run_followup_scan",
]
