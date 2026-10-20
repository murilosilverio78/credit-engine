import pytest

from app.integrations.comprasnet.parsing import parse_data, parse_number
from app.services.findings.adapters.fluxo_caixa import emit_fluxo_caixa
from app.services.findings.schemas import Estado


def _invoice(month, *, cancelled=False, glosa="0,00", liquidated=True):
    return {
        "dados_referencia": {"mesref": month, "anoref": 2026},
        "emissao": f"2026-{month:02d}-05", "ateste": f"2026-{month:02d}-08",
        "data_liquidacao": f"2026-{month:02d}-12" if liquidated else None, "valor": "25.760,13",
        "glosa": glosa, "valorliquido": "19.457,17", "situacao": "Pago",
        "nota_cancelada": cancelled,
    }


def _snapshots(invoices, *, commitments=None):
    return {"contratos_comprasnet": {
        "contrato_comprasnet": {"vigencia_inicio": "2026-04-01", "vigencia_fim": "2026-12-31", "valor_global": "231.841,17"},
        "faturas": invoices,
        "empenhos_detalhe": commitments if commitments is not None else [{"empenhado": "180.320,91", "liquidado": "128.800,65", "pago": "0,00", "data_emissao": "2026-01-05"}],
    }}


def _operation():
    return {
        "data_referencia": "19/10/2026", "valor_solicitado": 100_000,
        "capacidade_memoria": {"fator_liquido": 0.755},
        "taxa_breakdown": {"taxa_sugerida_am": 0.02, "detalhes": {"prazo_meses": 12}},
    }


def _by_code(items):
    return {item.codigo: item for item in items}


def test_comprasnet_parser_preserves_decimal_dot_and_accepts_brazilian_dates():
    assert parse_number("25760.13") == 25_760.13
    assert parse_number("25.760,13") == 25_760.13
    assert parse_number(float("inf")) is None
    assert parse_data("16/09/2026").isoformat() == "2026-09-16"


def test_funai_76_cash_flow_reports_invoices_and_unfunded_november_december():
    invoices = [_invoice(month, liquidated=month != 9) for month in range(4, 10)]
    findings = _by_code(emit_fluxo_caixa(_snapshots(invoices), fingerprint="f", operation=_operation()))

    assert findings["mensalidade_faturada_media"].valor == {"valor": 25_760.13, "desvio": 0.0, "cv": 0.0, "competencias": 6}
    assert findings["glosa_pct_12m"].valor == 0.0
    assert findings["prazo_ateste_dias"].valor == 3.0
    assert findings["prazo_liquidacao_dias"].valor == 7.0
    assert findings["meses_sem_fatura_12m"].valor == 0
    assert findings["saldo_empenho_ano"].valor == {
        "saldo": 51_520.26, "necessidade": 103_040.52, "meses_cobertos": 2.0,
        "meses_pendentes_ano": 3, "faturas_nao_liquidadas_valor": 25_760.13,
    }
    assert findings["empenho_insuficiente"].valor["valor_descoberto"] == 51_520.26
    assert findings["empenho_insuficiente"].valor["meses_descobertos"] == 2.0
    installment = 100_000 * 0.02 / (1 - (1.02) ** -12)
    assert findings["cobertura_parcela"].catalogo_versao == 2
    assert findings["cobertura_parcela"].valor == {
        "indice": pytest.approx(25_760.13 * .755 / installment),
        "fonte_fator": "CAPACIDADE_MEMORIA",
        "fonte_parcela": "PRECIFICADA",
        "parcela": pytest.approx(installment, abs=.01),
        "mensal_liquido": pytest.approx(25_760.13 * .755, abs=.01),
    }


def test_new_contract_without_invoices_is_unverified_not_negative():
    findings = emit_fluxo_caixa(_snapshots([]), fingerprint="f", operation=_operation())

    assert {item.estado for item in findings} == {Estado.NAO_VERIFICADO}
    assert "contrato sem faturamento ainda" in {item.pendencia["motivo"] for item in findings}
    assert findings[-1].pendencia["motivo"] == "pagamentos do Portal indisponíveis"


def test_cancelled_invoice_is_ignored_and_glosa_is_calculated():
    findings = _by_code(emit_fluxo_caixa(_snapshots([_invoice(4), _invoice(5, cancelled=True), _invoice(6, glosa="2.576,01")]), fingerprint="f", operation=_operation()))

    assert findings["mensalidade_faturada_media"].valor["competencias"] == 2
    assert findings["glosa_pct_12m"].valor == 0.05


def test_coverage_is_unverified_when_rate_is_missing():
    operation = _operation()
    operation["taxa_breakdown"] = {"detalhes": {"prazo_meses": 12}}

    findings = _by_code(emit_fluxo_caixa(_snapshots([_invoice(month) for month in range(4, 10)]), fingerprint="f", operation=operation))

    coverage = findings["cobertura_parcela"]
    assert coverage.estado == Estado.NAO_VERIFICADO
    assert coverage.pendencia == {"motivo": "parcela da operação indisponível"}


def test_coverage_uses_dedicated_default_factor_when_memory_is_absent(monkeypatch):
    import app.services.eligibility_params_service as eligibility

    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {
        "cap_fator_liquido_mao_obra": .645,
        "cap_fator_liquido_demais": .85,
        "cap_taxa_referencia_am": .035,
    })
    operation = _operation() | {
        "capacidade_memoria": {}, "contrato_dedicacao_exclusiva": True,
    }
    findings = _by_code(emit_fluxo_caixa(
        _snapshots([_invoice(month) for month in range(4, 10)]), fingerprint="f", operation=operation,
    ))

    coverage = findings["cobertura_parcela"].valor
    assert coverage["fonte_fator"] == "PARAMETRO_PADRAO"
    assert coverage["mensal_liquido"] == pytest.approx(25_760.13 * .645, abs=.01)


def test_coverage_estimates_installment_before_pricing(monkeypatch):
    import app.services.eligibility_params_service as eligibility

    monkeypatch.setattr(eligibility, "get_eligibility_config", lambda: {
        "cap_fator_liquido_mao_obra": .645,
        "cap_fator_liquido_demais": .85,
        "cap_taxa_referencia_am": .035,
    })
    operation = _operation() | {"taxa_breakdown": None, "prazo_final_meses": 12}
    findings = _by_code(emit_fluxo_caixa(
        _snapshots([_invoice(month) for month in range(4, 10)]), fingerprint="f", operation=operation,
    ))

    coverage = findings["cobertura_parcela"].valor
    estimated = 100_000 * .035 / (1 - 1.035 ** -12)
    assert coverage["fonte_parcela"] == "ESTIMADA"
    assert coverage["parcela"] == pytest.approx(estimated, abs=.01)


def test_commitments_without_issue_dates_use_all_rows():
    findings = _by_code(emit_fluxo_caixa(
        _snapshots([_invoice(month) for month in range(4, 10)], commitments=[
            {"empenhado": "51.520,26", "liquidado": "0", "pago": "0"},
        ]), fingerprint="f", operation=_operation(),
    ))

    assert findings["saldo_empenho_ano"].valor["criterio_ano"] == "SEM_DATA_EMISSAO"
