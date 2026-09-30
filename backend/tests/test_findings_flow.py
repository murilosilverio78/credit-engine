"""Real shadow-emission flow through the component hooks and PostgREST fake."""
from app.services.findings import emitter
from app.workers import base
from tests.fakes.postgrest import Postgrest, Rpc


class FlowDb(Postgrest):
    def __init__(self, snapshots):
        super().__init__({
            "operations": [{"id": "op", "ambiente": "TESTE", "valor_enquadrado": 100}],
            "component_snapshots": snapshots,
            "cotacoes_broadfactor": [],
        })
        self.runs: set[tuple[str, str]] = set()
        self.persisted_findings = []

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params))
        key = (params["p_run"]["especialista"], params["p_run"]["entrada_hash"])
        inserted = key not in self.runs
        self.runs.add(key)
        if inserted:
            self.persisted_findings.extend(params["p_achados"])
        return Rpc([{"run_id": "run", "inserido": inserted}])


def _catalog():
    keys = {
        "cadastro_inativo": ("CEDENTE", "BOOLEANO"),
        "sancao_ativa": ("CEDENTE", "OBJETO"),
        "acordo_leniencia_ativo": ("CEDENTE", "BOOLEANO"),
        "balanco_ausente": ("CEDENTE", "BOOLEANO"),
        "capacidade_operacional": ("CEDENTE", "ENUM"),
    }
    return {
        f"{code}:1": {"codigo": code, "versao": 1, "escopo": scope, "tipo_valor": typ}
        for code, (scope, typ) in keys.items()
    }


def _cadastro_rows(status="completed"):
    return [
        {"operation_id": "op", "component": component, "status": status,
         "parsed_result": {} if status == "completed" else None}
        for component in ("brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim", "acordos_leniencia")
    ]


def _enable_real_hook(monkeypatch, db):
    import app.core.config as config
    import app.core.database as database

    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", True)
    monkeypatch.setattr(database, "supabase", db)
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())


def test_cadastro_hooks_wait_for_all_terminal_then_deduplicate(monkeypatch):
    rows = _cadastro_rows("pending")
    db = FlowDb(rows)
    _enable_real_hook(monkeypatch, db)

    for row in rows:
        row["status"] = "completed"
        row["parsed_result"] = {}
        base._dual_write_findings("op", row["component"], row["parsed_result"])
        if row is not rows[-1]:
            assert db.rpc_calls == []

    assert len(db.rpc_calls) == 1
    codes = [finding["codigo"] for finding in db.persisted_findings]
    assert len(codes) == len(set(codes))
    before = list(db.persisted_findings)
    base._dual_write_findings("op", rows[-1]["component"], rows[-1]["parsed_result"])
    assert len(db.rpc_calls) == 2
    assert db.persisted_findings == before
    assert db.rpc_calls[-1][1]["p_run"]["entrada_hash"] == db.rpc_calls[0][1]["p_run"]["entrada_hash"]


def test_failed_source_emits_partial_with_unverified_finding(monkeypatch):
    rows = _cadastro_rows()
    rows[2].update(status="failed", parsed_result=None)
    db = FlowDb(rows)
    _enable_real_hook(monkeypatch, db)

    base._dual_write_findings("op", "ceis", None)

    assert db.rpc_calls[0][1]["p_run"]["status"] == "PARCIAL"
    sancao = next(item for item in db.persisted_findings if item["codigo"] == "sancao_ativa")
    assert sancao["estado"] == "NAO_VERIFICADO"


def test_porte_reprocessing_uses_new_override_not_old_snapshot(monkeypatch):
    db = FlowDb([{
        "operation_id": "op", "component": "score_engine", "status": "completed",
        "parsed_result": {"dimensoes": {"porte_operacionalidade": {"nivel": "Fraco"}}},
    }])
    _enable_real_hook(monkeypatch, db)

    emitter.emit_findings(
        "op", "porte", database=db,
        overrides={"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Forte"}}}},
    )

    assert db.persisted_findings[0]["valor"] == "Forte"


def test_disabled_flag_makes_no_rpc_call(monkeypatch):
    import app.core.config as config
    import app.core.database as database

    db = FlowDb(_cadastro_rows())
    monkeypatch.setattr(config.settings, "FINDINGS_EMIT_ENABLED", False)
    monkeypatch.setattr(database, "supabase", db)
    snapshot = {"value": 1}
    base._dual_write_findings("op", "brasil_api", snapshot)
    assert db.rpc_calls == []
    assert snapshot == {"value": 1}


def test_real_emitter_failure_leaves_component_result_unchanged(monkeypatch):
    db = FlowDb(_cadastro_rows())
    _enable_real_hook(monkeypatch, db)
    db.rpc = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    snapshot = {"status": "completed", "parsed": {"score": 70}}

    base._dual_write_findings("op", "brasil_api", snapshot)

    assert snapshot == {"status": "completed", "parsed": {"score": 70}}
