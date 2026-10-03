from app.services.findings.adapters.cadastro_regularidade import emit_cadastro_regularidade
import json


def test_cadastro_adapter_emits_official_gate_and_balance_catalog():
    findings = emit_cadastro_regularidade({
        "brasil_api": {"situacao_cadastral": "BAIXADA", "capital_social": 1000, "porte": "ME"},
        "pessoa_juridica": {}, "acordos_leniencia": {"possui_acordo": False},
        "catalogo_broadfactor": {"documentos_broadfactor": [{"tipo": "DRE"}]},
    }, fingerprint="x")
    by_code = {item.codigo: item for item in findings}
    assert by_code["cadastro_inativo"].valor is True
    assert by_code["balanco_ausente"].valor is False
    assert by_code["balanco_catalogado_broadfactor"].valor["tipos"] == ["DRE"]


def test_balance_union_matches_score_document_sources_and_keeps_provenance():
    from app.services.document_types import BALANCO_DOCUMENT_TYPES
    from app.workers.tasks.score_engine import _document_types

    patterns = [
        ({}, True),
        ({"contrato_extracao": {"documentos_broadfactor": [{"tipo": "DRE"}]}, "catalogo_broadfactor": {"documentos_broadfactor": [{"tipo": "DRE"}]}}, False),
        ({"catalogo_broadfactor": {"documentos_broadfactor": [{"tipo": "PENULTIMO_BALANCO"}]}}, False),
        ({"contrato_extracao": {"resultado": {"tipo_documento": "ULTIMO_BALANCO"}}}, False),
        ({"documentos_operacao": {"documentos": [{"document_type": "BALANCO"}]}}, False),
    ]
    for extra, absent in patterns:
        findings = {item.codigo: item for item in emit_cadastro_regularidade(extra, fingerprint="x")}
        assert findings["balanco_ausente"].valor is absent
        assert findings["balanco_ausente"].valor is (not bool(_document_types(extra) & BALANCO_DOCUMENT_TYPES))
    both = {item.codigo: item for item in emit_cadastro_regularidade(patterns[1][0], fingerprint="x")}
    assert both["balanco_catalogado_broadfactor"].valor["fontes"]["DRE"] == ["cotacao", "extracao"]


def test_qsa_finding_exposes_only_stability_indicators():
    findings = emit_cadastro_regularidade({
        "brasil_api": {"qsa": [
            {"nome": "SOCIA FICTICIA", "cpf_cnpj_socio": "12345678900", "qualificacao": "Sócio-Administrador", "data_entrada": "2020-02-01"},
            {"nome": "OUTRO SOCIO", "qualificacao": "Sócio", "data_inicio": "2023-04-05"},
        ]},
    }, fingerprint="x")
    qsa = next(item for item in findings if item.codigo == "qsa_estabilidade")

    assert qsa.valor == {"socios_qtd": 2, "entrada_mais_recente": "2023-04-05", "entrada_mais_antiga": "2020-02-01", "tem_administrador": True}
    assert "SOCIA FICTICIA" not in json.dumps([item.model_dump(mode="json") for item in findings], ensure_ascii=False)


def test_cadastro_adapter_emits_score_equivalent_nature_and_activity_facts():
    findings = {
        item.codigo: item
        for item in emit_cadastro_regularidade({
            "brasil_api": {
                "natureza_juridica": "Associacao privada",
                "atividade_principal": "Vigilancia patrimonial",
            },
        }, fingerprint="x")
    }

    assert findings["natureza_juridica_empresarial"].valor is False
    assert findings["atividade_restrita"].valor is True
