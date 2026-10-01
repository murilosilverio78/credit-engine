import threading
import pytest

from app.services.findings import dispatch
from app.workers import base


@pytest.fixture(autouse=True)
def reset_dispatcher():
    dispatch.reset_dispatcher_for_tests()
    yield
    dispatch.reset_dispatcher_for_tests()


def test_enabled_hook_returns_without_waiting_for_slow_emitter(monkeypatch):
    import app.core.config as config

    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", True)
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: (started.set(), release.wait()))

    base._dual_write_findings("op", "brasil_api")

    assert started.wait(timeout=1)
    assert not release.is_set()
    release.set()


def test_disabled_hook_does_not_start_dispatcher_or_call_emitter(monkeypatch):
    import app.core.config as config

    called = []
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", False)
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: called.append(True))

    base._dual_write_findings("op", "brasil_api")

    assert called == []
    assert not dispatch.executor_started_for_tests()


def test_emission_runs_in_a_daemon_worker(monkeypatch):
    caller = threading.get_ident()
    completed = threading.Event()
    identifiers = []
    monkeypatch.setattr(
        dispatch,
        "_emit_findings",
        lambda *_args: (identifiers.append(threading.get_ident()), completed.set()),
    )

    dispatch.dispatch_emission("op", "cadastro_regularidade")

    assert completed.wait(timeout=1)
    assert identifiers == [identifiers[0]]
    assert identifiers[0] != caller


def test_full_queue_drops_excess_without_blocking(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    warnings = []
    dispatch.reset_dispatcher_for_tests(queue_maxsize=1)
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: (started.set(), release.wait()))
    monkeypatch.setattr(dispatch.logger, "warning", lambda event, **kwargs: warnings.append((event, kwargs)))

    dispatch.dispatch_emission("op-1", "cadastro_regularidade")
    assert started.wait(timeout=1)
    dispatch.dispatch_emission("op-2", "cadastro_regularidade")
    dispatch.dispatch_emission("op-3", "cadastro_regularidade")

    assert any(event == "findings.dispatch_queue_full" for event, _kwargs in warnings)
    release.set()


def test_emitter_exception_does_not_kill_worker(monkeypatch):
    first = threading.Event()
    second = threading.Event()
    calls = []

    def flaky(*_args):
        calls.append(True)
        if len(calls) == 1:
            first.set()
            raise RuntimeError("offline")
        second.set()

    monkeypatch.setattr(dispatch, "_emit_findings", flaky)
    dispatch.dispatch_emission("op-1", "cadastro_regularidade")
    assert first.wait(timeout=1)
    dispatch.dispatch_emission("op-2", "cadastro_regularidade")

    assert second.wait(timeout=1)
    assert dispatch.executor_started_for_tests()


def test_reset_leaves_no_findings_workers_alive(monkeypatch):
    completed = threading.Event()
    monkeypatch.setattr(dispatch, "_emit_findings", lambda *_args: completed.set())
    dispatch.dispatch_emission("op", "cadastro_regularidade")
    assert completed.wait(timeout=1)

    dispatch.reset_dispatcher_for_tests()

    assert not [thread for thread in threading.enumerate() if thread.name.startswith("findings-")]
