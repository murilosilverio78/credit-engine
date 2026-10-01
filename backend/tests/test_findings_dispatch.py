import threading

from app.services.findings import dispatch
from app.workers import base


def test_enabled_hook_returns_without_waiting_for_slow_emitter(monkeypatch):
    import app.core.config as config

    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", True)
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: (started.set(), release.wait()))
    dispatch.reset_dispatcher_for_tests()

    base._dual_write_findings("op", "brasil_api")

    assert started.wait(timeout=1)
    assert not release.is_set()
    release.set()
    dispatch.reset_dispatcher_for_tests()


def test_disabled_hook_does_not_start_dispatcher_or_call_emitter(monkeypatch):
    import app.core.config as config

    called = []
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", False)
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: called.append(True))
    dispatch.reset_dispatcher_for_tests()

    base._dual_write_findings("op", "brasil_api")

    assert called == []
    assert not dispatch.executor_started_for_tests()
