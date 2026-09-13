from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from fastapi import HTTPException


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints import operations  # noqa: E402
from app.services import analysis_runtime  # noqa: E402
from app.workers.tasks import orchestrator  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.info_calls: list[tuple[str, dict[str, Any]]] = []
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs):
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs):
        self.warning_calls.append((event, kwargs))

    def error(self, event: str, **kwargs):
        self.warning_calls.append((event, kwargs))


@pytest.mark.asyncio
async def test_shutdown_waits_for_running_analysis(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    fake_logger = FakeLogger()

    async def fake_run(_operation_id: str):
        entered.set()
        await release.wait()
        return {"status": "completed"}

    monkeypatch.setattr(analysis_runtime, "logger", fake_logger)
    monkeypatch.setattr(orchestrator, "_run_analysis", fake_run)

    loop = asyncio.get_running_loop()
    task: asyncio.Task[Any] | None = None
    try:
        analysis_runtime.configure_analysis_runtime(loop)
        task = asyncio.create_task(orchestrator.start_analysis("op-1"))
        await entered.wait()
        loop.call_later(0.02, release.set)
        await analysis_runtime.drain_analyses(1)

        assert task.done()
        assert any(
            event == "shutdown.aguardando_analises"
            and payload["analises_em_andamento"] == 1
            for event, payload in fake_logger.info_calls
        )
        assert any(
            event == "shutdown.concluido"
            for event, _payload in fake_logger.info_calls
        )
    finally:
        release.set()
        if task is not None:
            await task
        analysis_runtime.configure_analysis_runtime(loop)


@pytest.mark.asyncio
async def test_shutdown_timeout_is_logged_without_raising(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    fake_logger = FakeLogger()

    async def fake_run(_operation_id: str):
        entered.set()
        await release.wait()
        return {"status": "completed"}

    monkeypatch.setattr(analysis_runtime, "logger", fake_logger)
    monkeypatch.setattr(orchestrator, "_run_analysis", fake_run)

    loop = asyncio.get_running_loop()
    task: asyncio.Task[Any] | None = None
    try:
        analysis_runtime.configure_analysis_runtime(loop)
        task = asyncio.create_task(orchestrator.start_analysis("op-2"))
        await entered.wait()
        await analysis_runtime.drain_analyses(0)

        assert any(
            event == "shutdown.timeout"
            and payload["analises_em_andamento"] == 1
            for event, payload in fake_logger.warning_calls
        )
    finally:
        release.set()
        if task is not None:
            await task
        analysis_runtime.configure_analysis_runtime(loop)


@pytest.mark.asyncio
async def test_create_operation_is_rejected_during_shutdown():
    loop = asyncio.get_running_loop()
    analysis_runtime.configure_analysis_runtime(loop)
    analysis_runtime.begin_shutdown()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await operations.create_operation(None, None)
    finally:
        analysis_runtime.configure_analysis_runtime(loop)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == (
        "Serviço em encerramento; tente novamente em instantes"
    )


@pytest.mark.asyncio
async def test_failed_analysis_is_removed_from_runtime_tracker(monkeypatch):
    async def fake_run(_operation_id: str):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(orchestrator, "_run_analysis", fake_run)
    monkeypatch.setattr(orchestrator, "_mark_operation_failed", lambda *_args: None)

    loop = asyncio.get_running_loop()
    analysis_runtime.configure_analysis_runtime(loop)
    try:
        with pytest.raises(RuntimeError, match="falha simulada"):
            await orchestrator.start_analysis("op-3")
        assert analysis_runtime.active_analysis_count() == 0
    finally:
        analysis_runtime.configure_analysis_runtime(loop)
