import builtins

from app.services.findings import catalog, emitter
from tests.fakes.postgrest import Postgrest


def _reject_global_database_import(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "app.core.database":
            raise AssertionError("database global nao deve ser importado")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_catalog_uses_injected_database_without_global_import(monkeypatch):
    db = Postgrest({"finding_catalog": [{
        "codigo": "conta_vinculada_regime", "versao": 1, "escopo": "CONTRATO",
        "tipo_valor": "ENUM", "ativo": True,
    }]})
    _reject_global_database_import(monkeypatch)

    result = catalog.get_catalog(database=db, force_reload=True)

    assert "conta_vinculada_regime:1" in result


def test_emitter_uses_injected_database_without_global_import(monkeypatch):
    db = Postgrest({
        "operations": [{"id": "op", "ambiente": "TESTE", "valor_enquadrado": 100}],
        "component_snapshots": [{
            "operation_id": "op", "component": "contrato_extracao", "status": "completed",
            "parsed_result": {"regime_conta_vinculada": "CONTA_DEPOSITO_VINCULADA", "flags": []},
        }],
        "cotacoes_broadfactor": [],
    })
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: {
        "conta_vinculada_regime:1": {
            "codigo": "conta_vinculada_regime", "versao": 1, "escopo": "CONTRATO", "tipo_valor": "ENUM",
        }
    })
    _reject_global_database_import(monkeypatch)

    emitter.emit_findings("op", "documentos", database=db)

    assert len(db.rpc_calls) == 1
