from app.services.findings import emitter
from app.services.findings.backfill import reemitir_operacao, select_operations
from tests.test_findings_flow import FlowDb, _catalog, _cadastro_rows


def _ready_db(with_score=False):
    rows = _cadastro_rows()
    rows += [
        {"operation_id": "op", "component": "contratos", "status": "completed", "parsed_result": {}},
        {"operation_id": "op", "component": "recursos_recebidos", "status": "completed", "parsed_result": {}},
        {"operation_id": "op", "component": "contratos_comprasnet", "status": "completed", "parsed_result": {}},
        {"operation_id": "op", "component": "contrato_extracao", "status": "completed", "parsed_result": {}},
        {"operation_id": "op", "component": "web_research", "status": "completed", "parsed_result": {}},
    ]
    if with_score:
        rows.append({"operation_id": "op", "component": "score_engine", "status": "completed", "parsed_result": {"dimensoes": {"porte_operacionalidade": {"nivel": "Adequado"}}}})
    return FlowDb(rows)


def test_dry_run_does_not_call_rpc_and_waits_for_missing_score(monkeypatch):
    db = _ready_db()
    result = reemitir_operacao("op", aplicar=False, database=db)

    assert db.rpc_calls == []
    assert result["especialistas"]["cadastro_regularidade"] == "pronto"
    assert result["especialistas"]["porte"] == "aguardando"


def test_pending_and_failed_inputs_are_reported_or_emitted_partial(monkeypatch):
    db = _ready_db()
    db.tables["component_snapshots"][0]["status"] = "pending"
    assert reemitir_operacao("op", aplicar=False, database=db)["especialistas"]["cadastro_regularidade"] == "aguardando"
    db.tables["component_snapshots"][0]["status"] = "failed"
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())
    result = reemitir_operacao("op", aplicar=True, database=db)
    assert result["especialistas"]["cadastro_regularidade"] == "emitido"
    assert db.rpc_calls[0][1]["p_run"]["status"] == "PARCIAL"


def test_porte_uses_completed_snapshot_override(monkeypatch):
    db = _ready_db(with_score=True)
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())
    result = reemitir_operacao("op", aplicar=True, database=db)
    assert result["especialistas"]["porte"] == "emitido"
    porte = next(call for call in db.rpc_calls if call[1]["p_run"]["especialista"] == "porte")
    assert porte[1]["p_achados"][0]["valor"] == "Adequado"


def test_emitter_all_outcomes(monkeypatch):
    db = _ready_db()
    pending = _ready_db(); pending.tables["component_snapshots"][0]["status"] = "pending"
    assert emitter.emit_findings("op", "cadastro_regularidade", database=pending).desfecho == "aguardando"
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: None)
    assert emitter.emit_findings("op", "cadastro_regularidade", database=db).desfecho == "sem_catalogo"
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: _catalog())
    assert emitter.emit_findings("op", "cadastro_regularidade", database=db).desfecho == "emitido"
    assert emitter.emit_findings("op", "cadastro_regularidade", database=db).desfecho == "deduplicado"
    db.rpc = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    assert emitter.emit_findings("op", "cadastro_regularidade", database=db).desfecho == "erro"
