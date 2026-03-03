"""Parallel execution and progress-callback helpers for review batches."""

from __future__ import annotations

import json
import logging
import subprocess  # nosec
import threading
import time
from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FuturesTimeoutError,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.core.fallbacks import log_best_effort_failure
from desloppify.core.path_io_api import safe_write_text

from .runner_process import _extract_payload_from_log

logger = logging.getLogger(__name__)

_RUNNER_CALLBACK_EXCEPTIONS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    AssertionError,
    KeyError,
)
_RUNNER_TASK_EXCEPTIONS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    AssertionError,
    subprocess.SubprocessError,
)

BatchTask = Callable[[], int]


@dataclass(frozen=True)
class BatchProgressEvent:
    """Typed progress event emitted by batch runner execution."""

    batch_index: int
    event: str
    code: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchExecutionOptions:
    """Runtime options for serial/parallel batch execution."""

    run_parallel: bool
    max_parallel_workers: int | None = None
    heartbeat_seconds: float | None = 15.0
    clock_fn: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class BatchResult:
    """Typed normalized batch payload passed to merge/import stages."""

    batch_index: int
    assessments: dict[str, float]
    dimension_notes: dict[str, dict[str, Any]]
    issues: list[dict[str, Any]]
    quality: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "assessments": self.assessments,
            "dimension_notes": self.dimension_notes,
            "issues": self.issues,
            "quality": self.quality,
        }
        payload["batch_index"] = self.batch_index
        return payload


def _coerce_batch_execution_options(
    options: BatchExecutionOptions | None = None,
) -> BatchExecutionOptions:
    """Resolve execution options from the typed dataclass contract."""
    base = options or BatchExecutionOptions(run_parallel=False)
    max_parallel_workers = (
        int(base.max_parallel_workers)
        if isinstance(base.max_parallel_workers, int)
        and not isinstance(base.max_parallel_workers, bool)
        else None
    )
    heartbeat_seconds = (
        float(base.heartbeat_seconds)
        if isinstance(base.heartbeat_seconds, int | float)
        and not isinstance(base.heartbeat_seconds, bool)
        else None
    )
    clock_fn = base.clock_fn if callable(base.clock_fn) else time.monotonic

    return BatchExecutionOptions(
        run_parallel=bool(base.run_parallel),
        max_parallel_workers=max_parallel_workers,
        heartbeat_seconds=heartbeat_seconds,
        clock_fn=clock_fn,
    )


def _progress_contract(
    progress_fn,
    *,
    contract_cache: dict[int, str] | None = None,
) -> str:
    """Resolve callback contract once: event callback or none."""
    if not callable(progress_fn):
        return "none"
    fn_id = id(progress_fn)
    cache = contract_cache if contract_cache is not None else {}
    cached = cache.get(fn_id)
    if cached:
        return cached
    contract = "event"
    cache[fn_id] = contract
    return contract


def _emit_progress(
    progress_fn,
    batch_index: int,
    event: str,
    code: int | None = None,
    *,
    details: dict[str, Any] | None = None,
    contract_cache: dict[int, str] | None = None,
) -> Exception | None:
    """Forward a progress event and return callback exceptions to caller."""
    contract = _progress_contract(progress_fn, contract_cache=contract_cache)
    if contract == "none":
        return None
    payload = dict(details or {})
    progress_event = BatchProgressEvent(
        batch_index=batch_index,
        event=event,
        code=code,
        details=payload,
    )
    try:
        progress_fn(progress_event)
        return None
    except _RUNNER_CALLBACK_EXCEPTIONS as exc:
        return RuntimeError(
            f"progress callback failed for event={event} batch={batch_index}: {exc}"
        )


def _record_execution_error(
    *,
    error_log_fn,
    failures: set[int],
    idx: int,
    exc: Exception,
) -> None:
    """Record an execution/progress error through shared failure plumbing."""
    if callable(error_log_fn):
        try:
            error_log_fn(idx, exc)
        except (OSError, TypeError, ValueError) as err:
            log_best_effort_failure(
                logger,
                "record batch execution error via callback",
                err,
            )
    failures.add(idx)


def execute_batches(
    *,
    tasks: dict[int, BatchTask],
    options: BatchExecutionOptions | None = None,
    progress_fn=None,
    error_log_fn=None,
) -> list[int]:
    """Run indexed tasks and return failed index list.

    Each value in *tasks* is a zero-arg callable returning an int exit code.
    All domain knowledge (files, prompts, etc.) is pre-bound by the caller.
    """
    resolved_options = _coerce_batch_execution_options(options)
    contract_cache: dict[int, str] = {}
    indexes = sorted(tasks)
    if resolved_options.run_parallel:
        return _execute_parallel(
            tasks=tasks,
            indexes=indexes,
            progress_fn=progress_fn,
            error_log_fn=error_log_fn,
            max_parallel_workers=resolved_options.max_parallel_workers,
            heartbeat_seconds=resolved_options.heartbeat_seconds,
            clock_fn=resolved_options.clock_fn,
            contract_cache=contract_cache,
        )
    return _execute_serial(
        tasks=tasks,
        indexes=indexes,
        progress_fn=progress_fn,
        error_log_fn=error_log_fn,
        clock_fn=resolved_options.clock_fn,
        contract_cache=contract_cache,
    )


def _execute_serial(
    *,
    tasks: dict[int, BatchTask],
    indexes: list[int],
    progress_fn,
    error_log_fn,
    clock_fn,
    contract_cache: dict[int, str],
) -> list[int]:
    """Run tasks one at a time — no threads, no closures."""
    failures: set[int] = set()
    for idx in indexes:
        t0 = float(clock_fn())
        start_error = _emit_progress(
            progress_fn,
            idx,
            "start",
            None,
            details={"max_workers": 1},
            contract_cache=contract_cache,
        )
        if start_error is not None:
            _record_execution_error(
                error_log_fn=error_log_fn,
                failures=failures,
                idx=idx,
                exc=start_error,
            )
        try:
            code = tasks[idx]()
        except _RUNNER_TASK_EXCEPTIONS as exc:
            _record_execution_error(
                error_log_fn=error_log_fn,
                failures=failures,
                idx=idx,
                exc=exc,
            )
            code = 1
        if code != 0:
            failures.add(idx)
        done_error = _emit_progress(
            progress_fn,
            idx,
            "done",
            code,
            details={"elapsed_seconds": int(max(0.0, clock_fn() - t0))},
            contract_cache=contract_cache,
        )
        if done_error is not None:
            _record_execution_error(
                error_log_fn=error_log_fn,
                failures=failures,
                idx=idx,
                exc=done_error,
            )
    return sorted(failures)


def _execute_parallel(
    *,
    tasks: dict[int, BatchTask],
    indexes: list[int],
    progress_fn,
    error_log_fn,
    max_parallel_workers,
    heartbeat_seconds,
    clock_fn,
    contract_cache: dict[int, str],
) -> list[int]:
    """Run tasks in a thread pool with optional heartbeat monitoring."""
    max_workers, heartbeat = _resolve_parallel_runtime(
        indexes=indexes,
        max_parallel_workers=max_parallel_workers,
        heartbeat_seconds=heartbeat_seconds,
    )
    failures: set[int] = set()
    progress_failures: set[int] = set()
    started_at: dict[int, float] = {}
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = _queue_parallel_tasks(
            executor=executor,
            indexes=indexes,
            tasks=tasks,
            progress_fn=progress_fn,
            error_log_fn=error_log_fn,
            contract_cache=contract_cache,
            max_workers=max_workers,
            failures=failures,
            progress_failures=progress_failures,
            started_at=started_at,
            lock=lock,
            clock_fn=clock_fn,
        )
        pending = set(futures.keys())
        _drain_parallel_completions(
            pending=pending,
            futures=futures,
            heartbeat=heartbeat,
            indexes=indexes,
            progress_fn=progress_fn,
            error_log_fn=error_log_fn,
            contract_cache=contract_cache,
            failures=failures,
            progress_failures=progress_failures,
            started_at=started_at,
            lock=lock,
            clock_fn=clock_fn,
        )
    return sorted(failures)


def _resolve_parallel_runtime(
    *,
    indexes: list[int],
    max_parallel_workers,
    heartbeat_seconds,
) -> tuple[int, float | None]:
    requested = (
        int(max_parallel_workers)
        if isinstance(max_parallel_workers, int) and max_parallel_workers > 0
        else 8
    )
    max_workers = max(1, min(len(indexes), requested))
    heartbeat = (
        float(heartbeat_seconds)
        if isinstance(heartbeat_seconds, int | float) and heartbeat_seconds > 0
        else None
    )
    return max_workers, heartbeat


def _record_progress_error(
    *,
    idx: int,
    err: Exception,
    progress_failures: set[int],
    lock: threading.Lock,
    error_log_fn,
) -> None:
    with lock:
        progress_failures.add(idx)
    if not callable(error_log_fn):
        return
    try:
        error_log_fn(idx, err)
    except (OSError, TypeError, ValueError) as exc:
        log_best_effort_failure(
            logger,
            "record batch progress failure via callback",
            exc,
        )


def _run_parallel_task(
    *,
    idx: int,
    tasks: dict[int, BatchTask],
    progress_fn,
    error_log_fn,
    contract_cache: dict[int, str],
    max_workers: int,
    progress_failures: set[int],
    started_at: dict[int, float],
    lock: threading.Lock,
    clock_fn,
) -> int:
    with lock:
        started_at[idx] = float(clock_fn())
    progress_error = _emit_progress(
        progress_fn,
        idx,
        "start",
        None,
        details={"max_workers": max_workers},
        contract_cache=contract_cache,
    )
    if progress_error is not None:
        _record_progress_error(
            idx=idx,
            err=progress_error,
            progress_failures=progress_failures,
            lock=lock,
            error_log_fn=error_log_fn,
        )
    return tasks[idx]()


def _queue_parallel_tasks(
    *,
    executor: ThreadPoolExecutor,
    indexes: list[int],
    tasks: dict[int, BatchTask],
    progress_fn,
    error_log_fn,
    contract_cache: dict[int, str],
    max_workers: int,
    failures: set[int],
    progress_failures: set[int],
    started_at: dict[int, float],
    lock: threading.Lock,
    clock_fn,
) -> dict:
    futures: dict = {}
    for idx in indexes:
        queue_error = _emit_progress(
            progress_fn,
            idx,
            "queued",
            None,
            details={"max_workers": max_workers},
            contract_cache=contract_cache,
        )
        if queue_error is not None:
            _record_progress_error(
                idx=idx,
                err=queue_error,
                progress_failures=progress_failures,
                lock=lock,
                error_log_fn=error_log_fn,
            )
            failures.add(idx)
        futures[
            executor.submit(
                _run_parallel_task,
                idx=idx,
                tasks=tasks,
                progress_fn=progress_fn,
                error_log_fn=error_log_fn,
                contract_cache=contract_cache,
                max_workers=max_workers,
                progress_failures=progress_failures,
                started_at=started_at,
                lock=lock,
                clock_fn=clock_fn,
            )
        ] = idx
    return futures


def _complete_parallel_future(
    *,
    future,
    futures: dict,
    progress_fn,
    error_log_fn,
    contract_cache: dict[int, str],
    failures: set[int],
    progress_failures: set[int],
    started_at: dict[int, float],
    lock: threading.Lock,
    clock_fn,
) -> None:
    idx = futures[future]
    with lock:
        t0 = started_at.get(idx, float(clock_fn()))
    elapsed = int(max(0.0, clock_fn() - t0))
    try:
        code = future.result()
    except _RUNNER_TASK_EXCEPTIONS as exc:
        _record_execution_error(
            error_log_fn=error_log_fn,
            failures=failures,
            idx=idx,
            exc=exc,
        )
        done_error = _emit_progress(
            progress_fn,
            idx,
            "done",
            1,
            details={"elapsed_seconds": elapsed},
            contract_cache=contract_cache,
        )
        if done_error is not None:
            _record_progress_error(
                idx=idx,
                err=done_error,
                progress_failures=progress_failures,
                lock=lock,
                error_log_fn=error_log_fn,
            )
        return

    done_error = _emit_progress(
        progress_fn,
        idx,
        "done",
        code,
        details={"elapsed_seconds": elapsed},
        contract_cache=contract_cache,
    )
    if done_error is not None:
        _record_progress_error(
            idx=idx,
            err=done_error,
            progress_failures=progress_failures,
            lock=lock,
            error_log_fn=error_log_fn,
        )
    with lock:
        had_progress_failure = idx in progress_failures
    if code != 0 or had_progress_failure:
        failures.add(idx)


def _drain_parallel_completions(
    *,
    pending: set,
    futures: dict,
    heartbeat: float | None,
    indexes: list[int],
    progress_fn,
    error_log_fn,
    contract_cache: dict[int, str],
    failures: set[int],
    progress_failures: set[int],
    started_at: dict[int, float],
    lock: threading.Lock,
    clock_fn,
) -> None:
    if heartbeat is None:
        for future in as_completed(pending):
            _complete_parallel_future(
                future=future,
                futures=futures,
                progress_fn=progress_fn,
                error_log_fn=error_log_fn,
                contract_cache=contract_cache,
                failures=failures,
                progress_failures=progress_failures,
                started_at=started_at,
                lock=lock,
                clock_fn=clock_fn,
            )
        return

    while pending:
        try:
            future = next(as_completed(pending, timeout=heartbeat))
        except FuturesTimeoutError:
            _heartbeat(
                pending,
                futures,
                started_at,
                lock,
                indexes,
                progress_fn,
                clock_fn,
                error_log_fn=error_log_fn,
                contract_cache=contract_cache,
            )
            continue
        pending.discard(future)
        _complete_parallel_future(
            future=future,
            futures=futures,
            progress_fn=progress_fn,
            error_log_fn=error_log_fn,
            contract_cache=contract_cache,
            failures=failures,
            progress_failures=progress_failures,
            started_at=started_at,
            lock=lock,
            clock_fn=clock_fn,
        )


def _heartbeat(
    pending,
    futures,
    started_at,
    lock,
    indexes,
    progress_fn,
    clock_fn,
    *,
    error_log_fn=None,
    contract_cache: dict[int, str] | None = None,
):
    """Build and emit a heartbeat with active/queued batch status."""
    with lock:
        active = sorted(futures[f] for f in pending if futures[f] in started_at)
    active_set = set(active)
    queued = sorted(futures[f] for f in pending if futures[f] not in active_set)
    elapsed = {
        idx: int(max(0.0, clock_fn() - started_at.get(idx, clock_fn())))
        for idx in active
    }
    heartbeat_error = _emit_progress(
        progress_fn,
        -1,
        "heartbeat",
        None,
        details={
            "active_batches": active,
            "queued_batches": queued,
            "elapsed_seconds": elapsed,
            "active_count": len(active),
            "queued_count": len(queued),
            "total_count": len(indexes),
        },
        contract_cache=contract_cache,
    )
    if heartbeat_error is not None and callable(error_log_fn):
        try:
            error_log_fn(-1, heartbeat_error)
        except _RUNNER_CALLBACK_EXCEPTIONS as exc:
            log_best_effort_failure(
                logger,
                "record batch heartbeat failure via callback",
                exc,
            )


def collect_batch_results(
    *,
    selected_indexes: list[int],
    failures: list[int],
    output_files: dict[int, Path],
    allowed_dims: set[str],
    extract_payload_fn,
    normalize_result_fn,
) -> tuple[list[BatchResult], list[int]]:
    """Parse and normalize batch outputs, preserving prior failures."""
    batch_results: list[BatchResult] = []
    failure_set = set(failures)
    for idx in selected_indexes:
        had_execution_failure = idx in failure_set
        raw_path = output_files[idx]
        payload = None
        parsed_from_log = False
        if raw_path.exists():
            try:
                payload = extract_payload_fn(raw_path.read_text())
            except OSError:
                payload = None
        if payload is None:
            payload = _extract_payload_from_log(idx, raw_path, extract_payload_fn)
            parsed_from_log = payload is not None
        if payload is None:
            failure_set.add(idx)
            continue
        if parsed_from_log:
            try:
                safe_write_text(raw_path, json.dumps(payload, indent=2) + "\n")
            except OSError:
                pass
        try:
            assessments, issues, dimension_notes, quality = normalize_result_fn(
                payload,
                allowed_dims,
            )
        except ValueError:
            failure_set.add(idx)
            continue
        if had_execution_failure:
            failure_set.discard(idx)
        batch_results.append(
            BatchResult(
                batch_index=idx + 1,
                assessments=assessments,
                dimension_notes=dimension_notes,
                issues=issues,
                quality=quality,
            )
        )
    return batch_results, sorted(failure_set)


__all__ = [
    "BatchResult",
    "BatchExecutionOptions",
    "BatchProgressEvent",
    "collect_batch_results",
    "execute_batches",
]
