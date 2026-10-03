from datetime import date

from app.services.policy import shadow
from app.services.policy.types import EntradaPolitica


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
