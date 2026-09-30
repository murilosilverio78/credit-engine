from app.services.findings.adapters.sacado_orgao import emit_sacado_orgao


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
