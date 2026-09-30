from app.services.findings import emitter
from app.services.findings.adapters.documentos import emit_documentos
from app.services.findings.adapters.porte import emit_porte
from app.services.findings.adapters.reputacional import emit_reputacional
from app.services.findings.hashing import entrada_hash
from app.services.findings.schemas import Escopo, Estado
from tests.fakes.postgrest import Postgrest


def test_hash_is_stable_regardless_of_key_order():
    assert entrada_hash({"b": [1, 2], "a": {"x": True}}) == entrada_hash({"a": {"x": True}, "b": [1, 2]})


def test_document_adapter_does_not_make_truncated_absence_negative():
    result = emit_documentos({"contrato_extracao": {"regime_conta_vinculada": "NAO_IDENTIFICADO", "flags": ["texto_truncado_para_llm"]}}, fingerprint="x")
    assert result[0].estado == Estado.NAO_VERIFICADO
    assert result[0].escopo == Escopo.CONTRATO


def test_reputation_and_porte_adapter_confidence_rules():
    reputation = emit_reputacional({"web_research": {"nivel": "Atencao", "flags_reputacao": ["reputacao_nao_isolada"]}}, fingerprint="x")
    assert reputation[0].confianca == "BAIXA"
    porte = emit_porte({"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Adequado"}}}}, fingerprint="x")
    assert porte[0].confianca == "MEDIA"


def test_emitter_calls_rpc_once_and_swallows_rpc_failure(monkeypatch):
    db = Postgrest({"operations": [{"id": "op", "ambiente": "TESTE", "valor_enquadrado": 100}]})
    catalog = {"conta_vinculada_regime:1": {"codigo": "conta_vinculada_regime", "versao": 1, "escopo": "CONTRATO", "tipo_valor": "ENUM", "ativo": True}}
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: catalog)
    emitter.emit_findings("op", "documentos", {"contrato_extracao": {"regime_conta_vinculada": "CONTA_DEPOSITO_VINCULADA", "flags": []}}, database=db)
    assert len(db.rpc_calls) == 1
    db.rpc = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
    assert emitter.emit_findings("op", "documentos", {"contrato_extracao": {}}, database=db) is None


def test_emitter_skips_unknown_code_and_unavailable_catalog(monkeypatch):
    db = Postgrest({"operations": [{"id": "op", "ambiente": "TESTE", "valor_enquadrado": 100}]})
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: {})
    emitter.emit_findings("op", "documentos", {"contrato_extracao": {"regime_conta_vinculada": "CONTA_DEPOSITO_VINCULADA", "flags": []}}, database=db)
    assert db.rpc_calls[0][1]["p_achados"] == []
    db.rpc_calls.clear()
    monkeypatch.setattr(emitter, "get_catalog", lambda **_kwargs: None)
    emitter.emit_findings("op", "documentos", {}, database=db)
    assert db.rpc_calls == []
