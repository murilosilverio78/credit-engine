"""In-process lifecycle state for credit analyses."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any, AsyncIterator

import structlog


logger = structlog.get_logger()

_analysis_tasks: set[asyncio.Task[Any]] = set()
_application_loop: asyncio.AbstractEventLoop | None = None
_shutting_down = False
_pending_enqueues = 0
_state_lock = Lock()


def configure_analysis_runtime(loop: asyncio.AbstractEventLoop) -> None:
    """Bind enqueued analyses to the current application event loop."""
    global _application_loop, _shutting_down
    with _state_lock:
        _application_loop = loop
        _shutting_down = False


def begin_shutdown() -> None:
    global _shutting_down
    with _state_lock:
        _shutting_down = True


def is_shutting_down() -> bool:
    with _state_lock:
        return _shutting_down


def active_analysis_count() -> int:
    with _state_lock:
        tracked = sum(1 for task in _analysis_tasks if not task.done())
        return tracked + _pending_enqueues


@asynccontextmanager
async def track_analysis(_operation_id: str) -> AsyncIterator[None]:
    """Track the current task until the analysis exits for any reason."""
    task = asyncio.current_task()
    if task is not None:
        with _state_lock:
            _analysis_tasks.add(task)
    try:
        yield
    finally:
        if task is not None:
            with _state_lock:
                _analysis_tasks.discard(task)


async def wait_for_analyses(timeout_seconds: float) -> int:
    """Wait for tracked analyses, including tasks added while draining."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout_seconds), 0.0)
    current = asyncio.current_task()

    while True:
        with _state_lock:
            tasks = {
                task
                for task in _analysis_tasks
                if not task.done() and task is not current
            }
            pending_enqueues = _pending_enqueues
        if not tasks:
            if pending_enqueues:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return pending_enqueues
                await asyncio.sleep(min(0.01, remaining))
                continue
            return 0

        remaining = deadline - loop.time()
        if remaining <= 0:
            return len(tasks)

        done, _pending = await asyncio.wait(
            tasks,
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            return active_analysis_count()


async def drain_analyses(timeout_seconds: float) -> None:
    """Stop admissions and wait for analyses without failing application shutdown."""
    begin_shutdown()
    grace_seconds = max(float(timeout_seconds), 0.0)
    logger.info(
        "shutdown.aguardando_analises",
        analises_em_andamento=active_analysis_count(),
        limite_segundos=grace_seconds,
    )
    try:
        remaining = await wait_for_analyses(grace_seconds)
        if remaining:
            logger.warning(
                "shutdown.timeout",
                analises_em_andamento=remaining,
                limite_segundos=grace_seconds,
            )
        else:
            logger.info(
                "shutdown.concluido",
                analises_em_andamento=0,
            )
    except Exception as exc:
        logger.warning(
            "shutdown.timeout",
            analises_em_andamento=active_analysis_count(),
            limite_segundos=grace_seconds,
            error=str(exc),
        )


async def _run_enqueued_analysis(operation_id: str) -> None:
    global _pending_enqueues
    with _state_lock:
        _pending_enqueues = max(_pending_enqueues - 1, 0)
    try:
        from app.workers.tasks.orchestrator import start_analysis

        await start_analysis(operation_id)
    except Exception as exc:
        logger.error(
            "analysis_runtime.enqueued_failed",
            operation_id=operation_id,
            error=str(exc),
        )


def enqueue_analysis(operation_id: str) -> bool:
    """Schedule start_analysis from a worker thread onto the app loop."""
    global _pending_enqueues
    with _state_lock:
        loop = _application_loop
        if (
            _shutting_down
            or loop is None
            or loop.is_closed()
            or not loop.is_running()
        ):
            return False
        _pending_enqueues += 1

    def schedule() -> None:
        try:
            loop.create_task(_run_enqueued_analysis(operation_id))
        except Exception as exc:
            global _pending_enqueues
            with _state_lock:
                _pending_enqueues = max(_pending_enqueues - 1, 0)
            logger.error(
                "analysis_runtime.enqueue_failed",
                operation_id=operation_id,
                error=str(exc),
            )

    try:
        loop.call_soon_threadsafe(schedule)
    except RuntimeError:
        with _state_lock:
            _pending_enqueues = max(_pending_enqueues - 1, 0)
        return False
    return True


__all__ = [
    "active_analysis_count",
    "begin_shutdown",
    "configure_analysis_runtime",
    "drain_analyses",
    "enqueue_analysis",
    "is_shutting_down",
    "track_analysis",
    "wait_for_analyses",
]
