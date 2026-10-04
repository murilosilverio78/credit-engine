from datetime import date

from app.services.policy import shadow
from app.services.policy.engine import _limit
from app.services.policy.types import EntradaPolitica
from tests.fakes.postgrest import Postgrest


class _InsertQuery:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, payload):
        self.sink.append(payload)
        return self

    def execute(self):
        return type("Response", (), {"data": []})()


class _Db:
    def __init__(self):
        self.inserted = []

    def table(self, _name):
        return _InsertQuery(self.inserted)


def test_shadow_classifies_missing_runs_and_dry_run_does_not_persist(monkeypatch):
    params = {"score_bloqueio": 20}
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": params, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, {"cadastro_regularidade"}))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {"as_dict": lambda self: {field: None for field in shadow._FIELDS}, "efeitos_novos": []})())
    db = _Db()
    result = shadow.avaliar_sombra("op", database=db, aplicar=False)
    assert result["classe_geral"] == "SEM_DADOS"
    assert db.inserted == []
    shadow.avaliar_sombra("op", database=db, aplicar=True)
    assert len(db.inserted) == 1


def test_official_uses_score_completion_date_before_operation_creation():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-06T00:00:00Z",
        "parsed_result": {"score": 55},
    }]})

    official, reference = shadow._official(db, "op")

    assert official == {"score": 55}
    assert reference == date(2026, 1, 6)
    # This is deliberately past the strict one-year boundary: using the
    # operation creation date (Jan 1) would select the lower age band.
    opened = date(2025, 1, 5)
    assert (reference - opened).days / 365.25 > 1


def test_official_uses_creation_date_only_when_completion_is_null():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": None,
        "parsed_result": {"score": 55},
    }]})

    _official, reference = shadow._official(db, "op")

    assert reference == date(2026, 1, 1)


def test_official_reads_fator_potencial_from_real_regularidade_shape():
    db = Postgrest({"component_snapshots": [{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-02T00:00:00Z",
        "parsed_result": {"regularidade": {"fator_potencial": 0.91}},
    }]})

    official, _reference_date = shadow._official(db, "op")

    assert official["fator_potencial"] == 0.91


def test_shadow_uses_eligibility_cap_when_no_official_score(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {"pct_max_contrato": 0.35})
    operation = {}

    shadow._set_pct_max_contrato(operation, None)

    assert operation == {"pct_max_contrato": 0.35, "pct_max_contrato_origem": "eligibility"}


def test_shadow_preserves_official_cap_after_eligibility_changes(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {"pct_max_contrato": 0.10})
    operation = {}

    shadow._set_pct_max_contrato(operation, {"limite_sugerido_pct_contrato": 0.42})

    assert operation == {"pct_max_contrato": 0.42, "pct_max_contrato_origem": "oficial"}


def test_shadow_missing_eligibility_cap_keeps_limit_flag(monkeypatch):
    import app.services.eligibility_params_service as eligibility
    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    operation = {"contrato_saldo": 500_000}

    shadow._set_pct_max_contrato(operation, None)
    _value, flags = _limit(EntradaPolitica(operation, {}), {"pct_margem_sobre_saldo": 0.7}, {})

    assert operation["pct_max_contrato_origem"] == "indisponivel"
    assert flags == ["limite_sem_pct_max_contrato"]


def test_shadow_compares_veto_blocks_by_category(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"bloqueios": ["Situacao cadastral diferente de ATIVA: BAIXADA"]}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (["cadastro_inativo"] if field == "bloqueios" else None) for field in shadow._FIELDS},
        "efeitos_novos": [],
    })())

    payload = shadow.avaliar_sombra("op", database=_Db())

    assert not [item for item in payload["divergencias"] if item["campo"] == "bloqueios"]


def test_shadow_effects_without_parity_field_do_not_explain_divergence(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [{"codigo": "glosa_historica", "parametro_alvo": "pd_multiplicador"}],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "INESPERADA"


def test_shadow_effect_with_explicit_parity_field_explains_divergence(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [{"codigo": "efeito_futuro", "campos_paridade": ["score"]}],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "ESPERADA"


def test_shadow_partial_run_is_sem_dados(monkeypatch):
    monkeypatch.setattr(shadow, "load_policy", lambda **_kwargs: {"version": {"id": "policy"}, "parametros": {}, "regras": []})
    monkeypatch.setattr(shadow, "montar_entrada", lambda *_args, **_kwargs: EntradaPolitica({}, {}, {"sacado_orgao": {"run_id": "run", "status": "PARCIAL"}}, set()))
    monkeypatch.setattr(shadow, "_official", lambda *_args: ({"score": 70}, date(2026, 1, 1)))
    monkeypatch.setattr(shadow, "avaliar", lambda *_args: type("Result", (), {
        "as_dict": lambda self: {field: (60 if field == "score" else None) for field in shadow._FIELDS},
        "efeitos_novos": [],
    })())

    assert shadow.avaliar_sombra("op", database=_Db())["classe_geral"] == "SEM_DADOS"
