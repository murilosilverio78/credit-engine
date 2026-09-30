"""End-to-end local behavior of the real emitter with PostgREST-shaped queries."""
from types import SimpleNamespace

from app.services.findings import emitter


class Query:
    def __init__(self, rows): self.rows, self.filters, self.limit_value = rows, [], None
    def select(self, *_args): return self
    def eq(self, field, value): self.filters.append((field, value)); return self
    def limit(self, value): self.limit_value = value; return self
    def execute(self):
        rows = [row.copy() for row in self.rows if all(row.get(k) == v for k, v in self.filters)]
        return SimpleNamespace(data=rows[:self.limit_value] if self.limit_value else rows)


class FlowDb:
    def __init__(self, snapshots):
        self.tables = {"operations": [{"id": "op", "ambiente": "TESTE", "valor_enquadrado": 100}], "component_snapshots": snapshots, "cotacoes_broadfactor": []}
        self.runs, self.calls = set(), []
    def table(self, name): return Query(self.tables.get(name, []))
    def rpc(self, _name, params):
        key = (params["p_run"]["especialista"], params["p_run"]["entrada_hash"])
        inserted = key not in self.runs
        self.runs.add(key)
        self.calls.append((params, inserted))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[{"run_id": "run", "inserido": inserted}]))


def _catalog():
    keys = {
        "cadastro_inativo": ("CEDENTE", "BOOLEANO"), "sancao_ativa": ("CEDENTE", "OBJETO"), "acordo_leniencia_ativo": ("CEDENTE", "BOOLEANO"),
        "balanco_ausente": ("CEDENTE", "BOOLEANO"), "capacidade_operacional": ("CEDENTE", "ENUM"),
    }
    return {f"{code}:1": {"codigo": code, "versao": 1, "escopo": scope, "tipo_valor": typ} for code, (scope, typ) in keys.items()}


def _cadastro_rows(status="completed"):
    return [{"operation_id": "op", "component": component, "status": status, "parsed_result": {} if status == "completed" else None} for component in ("brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim", "acordos_leniencia")]


def test_cadastro_waits_for_all_terminal_then_deduplicates(monkeypatch):
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())
    rows = _cadastro_rows("pending")
    db = FlowDb(rows)
    for row in rows:
        emitter.emit_findings("op", "cadastro_regularidade", database=db)
        assert db.calls == []
        row["status"] = "completed"; row["parsed_result"] = {}
    emitter.emit_findings("op", "cadastro_regularidade", database=db)
    emitter.emit_findings("op", "cadastro_regularidade", database=db)
    assert len(db.calls) == 2
    assert db.calls[0][1] is True and db.calls[1][1] is False


def test_failed_source_marks_partial_and_porte_override_wins(monkeypatch):
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())
    rows = _cadastro_rows(); rows[2].update(status="failed", parsed_result=None)
    db = FlowDb(rows)
    emitter.emit_findings("op", "cadastro_regularidade", database=db)
    assert db.calls[0][0]["p_run"]["status"] == "PARCIAL"
    emitter.emit_findings("op", "porte", database=db, overrides={"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Forte"}}}})
    assert db.calls[-1][0]["p_achados"][0]["valor"] == "Forte"
