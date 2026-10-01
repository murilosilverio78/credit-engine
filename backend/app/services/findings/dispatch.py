"""Best-effort asynchronous delivery for shadow findings.

The queue is deliberately not drained during process shutdown.  A pending
emission may be lost, but it is idempotent and a later component event can
emit the same inputs again; critical component and score paths never wait.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from queue import Empty, Full, Queue
import threading
from typing import Any, Iterator

import structlog

logger = structlog.get_logger()

MAX_WORKERS = 2
THREAD_PREFIX = "findings"
DEFAULT_QUEUE_MAXSIZE = 500


def _queue_maxsize() -> int:
    try:
        return max(1, int(os.getenv("FINDINGS_DISPATCH_QUEUE_MAXSIZE", DEFAULT_QUEUE_MAXSIZE)))
    except ValueError:
        return DEFAULT_QUEUE_MAXSIZE


Task = tuple[str, str, dict[str, Any] | None]
_STOP = object()
_queue: Queue[Task | object] = Queue(maxsize=_queue_maxsize())
_threads: list[threading.Thread] = []
_inline_for_tests = False
_lock = threading.Lock()


def _emit_findings(operation_id: str, especialista: str, overrides: dict[str, Any] | None) -> None:
    try:
        from app.services.findings.emitter import emit_findings

        emit_findings(operation_id, especialista, overrides=overrides)
    except Exception as exc:
        logger.warning("findings.dispatch_failed", operation_id=operation_id, especialista=especialista, error=str(exc))


def _worker(work_queue: Queue[Task | object]) -> None:
    while True:
        task = work_queue.get()
        try:
            if task is _STOP:
                return
            operation_id, especialista, overrides = task
            try:
                _emit_findings(operation_id, especialista, overrides)
            except Exception as exc:
                logger.warning("findings.dispatch_failed", operation_id=operation_id, especialista=especialista, error=str(exc))
        finally:
            work_queue.task_done()


def _start_workers() -> None:
    global _threads
    with _lock:
        _threads = [thread for thread in _threads if thread.is_alive()]
        wanted = min(MAX_WORKERS, max(1, _queue.qsize()))
        while len(_threads) < wanted:
            thread = threading.Thread(
                target=_worker,
                args=(_queue,),
                name=f"{THREAD_PREFIX}-{len(_threads)}",
                daemon=True,
            )
            thread.start()
            _threads.append(thread)


def dispatch_emission(operation_id: str, especialista: str, *, overrides: dict[str, Any] | None = None) -> None:
    """Queue an idempotent, non-critical emission without blocking the caller."""
    if _inline_for_tests:
        _emit_findings(operation_id, especialista, overrides)
        return
    try:
        _queue.put_nowait((operation_id, especialista, overrides))
    except Full:
        logger.warning("findings.dispatch_queue_full", operation_id=operation_id, especialista=especialista)
        return
    try:
        _start_workers()
    except Exception as exc:
        logger.warning("findings.dispatch_submit_failed", operation_id=operation_id, especialista=especialista, error=str(exc))


@contextmanager
def run_inline_for_tests() -> Iterator[None]:
    """Make dispatch deterministic without creating a worker thread."""
    global _inline_for_tests
    previous = _inline_for_tests
    _inline_for_tests = True
    try:
        yield
    finally:
        _inline_for_tests = previous


def reset_dispatcher_for_tests(*, queue_maxsize: int | None = None) -> None:
    """Stop workers, discard queued tasks, and reset state for deterministic tests."""
    global _queue, _threads, _inline_for_tests
    with _lock:
        old_queue = _queue
        old_threads = list({
            *_threads,
            *(thread for thread in threading.enumerate() if thread.name.startswith(f"{THREAD_PREFIX}-")),
        })
        _queue = Queue(maxsize=_queue_maxsize() if queue_maxsize is None else queue_maxsize)
        _threads = []
        _inline_for_tests = False
    while True:
        try:
            old_queue.get_nowait()
            old_queue.task_done()
        except Empty:
            break
    for _thread in old_threads:
        old_queue.put_nowait(_STOP)
    for thread in old_threads:
        thread.join(timeout=1)


def executor_started_for_tests() -> bool:
    return any(thread.is_alive() for thread in _threads)
