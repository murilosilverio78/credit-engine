"""Best-effort asynchronous delivery for shadow findings.

Pending work is intentionally not drained on process shutdown: findings are
idempotent and a later component event can emit them again, while the critical
component and score paths never wait for this optional audit write.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.thread import _threads_queues, _worker
from contextlib import contextmanager
import threading
import weakref
from typing import Any, Iterator

import structlog

logger = structlog.get_logger()


class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor variant whose best-effort workers do not hold shutdown."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, work_queue=self._work_queue):
            work_queue.put(None)

        if len(self._threads) >= self._max_workers:
            return
        thread_name = "%s_%d" % (self._thread_name_prefix or self, len(self._threads))
        thread = threading.Thread(
            name=thread_name,
            target=_worker,
            args=(weakref.ref(self, weakref_cb), self._create_worker_context(), self._work_queue),
            daemon=True,
        )
        thread.start()
        self._threads.add(thread)
        _threads_queues[thread] = self._work_queue


_executor: DaemonThreadPoolExecutor | None = None
_inline_for_tests = False
_lock = threading.Lock()


def _get_executor() -> DaemonThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = DaemonThreadPoolExecutor(max_workers=2, thread_name_prefix="findings")
        return _executor


def _emit_findings(operation_id: str, especialista: str, overrides: dict[str, Any] | None) -> None:
    try:
        from app.services.findings.emitter import emit_findings

        emit_findings(operation_id, especialista, overrides=overrides)
    except Exception as exc:
        logger.warning("findings.dispatch_failed", operation_id=operation_id, especialista=especialista, error=str(exc))


def dispatch_emission(operation_id: str, especialista: str, *, overrides: dict[str, Any] | None = None) -> None:
    """Schedule an idempotent, non-critical emission and return immediately."""
    if _inline_for_tests:
        _emit_findings(operation_id, especialista, overrides)
        return
    try:
        _get_executor().submit(_emit_findings, operation_id, especialista, overrides)
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


def reset_dispatcher_for_tests() -> None:
    """Release test-only state; never call this from production code."""
    global _executor, _inline_for_tests
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
    _inline_for_tests = False


def executor_started_for_tests() -> bool:
    return _executor is not None
