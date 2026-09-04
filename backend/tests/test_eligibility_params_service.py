import os


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import eligibility_params_service  # noqa: E402


PARAMS = {
    "ticket_minimo": 10_000,
    "ticket_maximo": 5_000_000,
    "pct_max_margem": 0.50,
    "prazo_padrao_meses": 12,
    "dias_minimos_expiracao": 5,
    "prazo_minimo_dias": 60,
    "cnpj_idade_minima_meses": 12,
}


def test_config_is_cached_for_60_seconds(monkeypatch):
    calls = []

    def load():
        calls.append(True)
        return PARAMS.copy()

    monkeypatch.setattr(eligibility_params_service, "_load_from_db", load)
    monkeypatch.setattr(eligibility_params_service.time, "time", lambda: 100.0)
    eligibility_params_service._cache.update({"params": None, "ts": 0.0})

    first = eligibility_params_service.get_eligibility_config()
    second = eligibility_params_service.get_eligibility_config()

    assert first == PARAMS
    assert second == PARAMS
    assert len(calls) == 1


def test_stale_cache_is_used_when_reload_fails(monkeypatch):
    monkeypatch.setattr(eligibility_params_service, "_load_from_db", lambda: None)
    monkeypatch.setattr(eligibility_params_service.time, "time", lambda: 200.0)
    eligibility_params_service._cache.update({"params": PARAMS.copy(), "ts": 0.0})

    assert eligibility_params_service.get_eligibility_config() == PARAMS


def test_config_reloads_after_60_seconds(monkeypatch):
    calls = []

    def load():
        calls.append(True)
        return PARAMS.copy()

    monkeypatch.setattr(eligibility_params_service, "_load_from_db", load)
    monkeypatch.setattr(eligibility_params_service.time, "time", lambda: 161.0)
    eligibility_params_service._cache.update({"params": PARAMS.copy(), "ts": 100.0})

    eligibility_params_service.get_eligibility_config()

    assert len(calls) == 1
