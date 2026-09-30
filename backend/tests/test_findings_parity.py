from app.services.findings.adapters.cadastro_regularidade import emit_cadastro_regularidade
from app.services.findings.schemas import Estado


def test_expired_sanction_is_not_confirmed_and_missing_source_is_unverified():
    complete = {
        "__statuses__": {name: "completed" for name in ("brasil_api", "pessoa_juridica", "ceis", "cnep", "cepim", "acordos_leniencia")},
        "brasil_api": {}, "pessoa_juridica": {}, "cnep": {}, "cepim": {}, "acordos_leniencia": {"total_acordos": 0},
        "ceis": {"total_registros": 1, "registros": [{"situacao": "ENCERRADO"}]},
    }
    findings = {item.codigo: item for item in emit_cadastro_regularidade(complete, fingerprint="x")}
    assert findings["sancao_ativa"].estado == Estado.NEGATIVO_CONFIRMADO
    complete["__statuses__"]["ceis"] = "failed"
    findings = {item.codigo: item for item in emit_cadastro_regularidade(complete, fingerprint="x")}
    assert findings["sancao_ativa"].estado == Estado.NAO_VERIFICADO


def test_certificate_value_uses_score_engine_state_and_factor():
    snapshots = {"cnd_federal": {"resultado": "negativa", "valida": True}}
    finding = next(item for item in emit_cadastro_regularidade(snapshots, fingerprint="x") if item.codigo == "certidao_cnd_federal_pendente")
    assert finding.valor["estado"] == "negativa"
    assert finding.valor["fator"] == 0.0
