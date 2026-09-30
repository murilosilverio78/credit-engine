from types import SimpleNamespace

from app.workers import base


def test_findings_flag_disabled_does_not_call_emitter(monkeypatch):
    import app.core.config as config

    calls = []
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", False)
    monkeypatch.setattr("app.services.findings.emitter.emit_findings", lambda *args, **kwargs: calls.append(args))
    snapshot = {"value": 1}
    base._dual_write_findings("op", "brasil_api", snapshot)
    assert calls == []
    assert snapshot == {"value": 1}


def test_findings_emitter_exception_is_non_blocking_and_does_not_mutate_snapshot(monkeypatch):
    import app.core.config as config

    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", True)
    monkeypatch.setattr("app.services.findings.emitter.emit_findings", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    snapshot = {"status": "completed", "parsed": {"score": 70}}
    base._dual_write_findings("op", "brasil_api", snapshot)
    assert snapshot == {"status": "completed", "parsed": {"score": 70}}
