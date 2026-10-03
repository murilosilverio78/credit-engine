from app.services.findings.adapters.sacado_orgao import emit_sacado_orgao
from tests.fakes.postgrest import Postgrest


def test_sacado_adapter_reads_persisted_relationship_and_glosa_values():
    findings = emit_sacado_orgao({
        "contratos": {"contratos_ativos": 2, "total_contratos": 3, "orgaos_contratantes": ["A", "B"], "contratos_detalhe": [{"data_inicio": "2024-01-01", "data_fim": "2026-01-01"}]},
        "recursos_recebidos": {"faturamento_verificado_12m": 1000, "concentracao": {"hhi": 1200}, "meses_com_recebimento": 8, "volatilidade": {"cv": 0.2, "anos_completos": 2}},
        "contratos_comprasnet": {"performance_contratual": {"glosa_total": 10, "taxa_glosa": 0.01, "faturado_total": 1000}},
    }, fingerprint="x", operation={"valor_enquadrado": 500})
    by_code = {item.codigo: item for item in findings}
    assert by_code["hhi_recebimentos"].valor == 1200
    assert by_code["glosa_historica"].valor["glosa_total"] == 10
    assert by_code["cobertura_exposicao"].valor == 0.5


def test_sacado_adapter_reuses_verified_comprasnet_merge_before_derivations():
    database = Postgrest({
        "component_snapshots": [{
            "operation_id": "op", "component": "contratos_comprasnet",
            "status": "completed", "parsed_result": {
                "status_consulta": "ENCONTRADO",
                "contrato_comprasnet": {
                    "match_confianca": "CNPJ_CONFERIDO", "situacao": "ativo",
                    "vigencia_fim": "2099-01-01", "numero": "123", "uasg": "1",
                    "valor_global": 50, "orgao": "Novo orgao",
                },
            },
        }],
    })
    findings = emit_sacado_orgao({
        "contratos": {"contratos_ativos": 1, "total_contratos": 1, "contratos_detalhe": []},
        "recursos_recebidos": {"concentracao": {"hhi": 100}, "meses_com_recebimento": 12},
        "contratos_comprasnet": {},
    }, fingerprint="x", operation={"id": "op", "cnpj": "12345678000199"}, database=database)
    by_code = {item.codigo: item for item in findings}

    assert by_code["contratos_ativos_qtd"].valor == 2
    assert by_code["contratos_total_qtd"].valor == 2
    assert by_code["contratos_comprasnet_incluidos_qtd"].valor == 1
