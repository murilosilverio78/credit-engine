import pytest

from app.services.findings.adapters.fluxo_caixa import emit_fluxo_caixa
from app.services.findings.schemas import Estado


def _invoice(month, *, cancelled=False, glosa="0,00"):
    return {
        "dados_referencia": {"mesref": month, "anoref": 2026},
        "emissao": f"2026-{month:02d}-05", "ateste": f"2026-{month:02d}-08",
        "data_liquidacao": f"2026-{month:02d}-12", "valor": "25.760,13",
        "glosa": glosa, "valorliquido": "19.457,17", "situacao": "Pago",
        "nota_cancelada": cancelled,
    }


def _snapshots(invoices):
    return {"contratos_comprasnet": {
        "contrato_comprasnet": {"vigencia_inicio": "2026-04-01", "vigencia_fim": "2026-12-31", "valor_global": "231.841,17"},
        "faturas": invoices,
        "empenhos_detalhe": [{"empenhado": "180.320,91", "liquidado": "180.320,91", "pago": "0,00", "data_emissao": "2026-01-05"}],
    }}


def _operation():
    return {"data_referencia": "2026-10-19", "capacidade_memoria": {"fator_liquido": 0.755, "parcela_max": 19_457.17}}


def _by_code(items):
    return {item.codigo: item for item in items}


def test_funai_76_cash_flow_reports_invoices_and_unfunded_november_december():
    findings = _by_code(emit_fluxo_caixa(_snapshots([_invoice(month) for month in range(4, 10)]), fingerprint="f", operation=_operation()))

    assert findings["mensalidade_faturada_media"].valor == {"valor": 25_760.13, "desvio": 0.0, "cv": 0.0, "competencias": 6}
    assert findings["glosa_pct_12m"].valor == 0.0
    assert findings["prazo_ateste_dias"].valor == 3.0
    assert findings["prazo_liquidacao_dias"].valor == 7.0
    assert findings["meses_sem_fatura_12m"].valor == 0
    assert findings["saldo_empenho_ano"].valor["meses_restantes_ano"] == 2
    assert findings["empenho_insuficiente"].valor["valor_descoberto"] == 51_520.26
    assert findings["cobertura_parcela"].valor == pytest.approx(1.0, abs=0.001)


def test_new_contract_without_invoices_is_unverified_not_negative():
    findings = emit_fluxo_caixa(_snapshots([]), fingerprint="f", operation=_operation())

    assert {item.estado for item in findings} == {Estado.NAO_VERIFICADO}
    assert {item.pendencia["motivo"] for item in findings} == {"contrato sem faturamento ainda"}


def test_cancelled_invoice_is_ignored_and_glosa_is_calculated():
    findings = _by_code(emit_fluxo_caixa(_snapshots([_invoice(4), _invoice(5, cancelled=True), _invoice(6, glosa="2.576,01")]), fingerprint="f", operation=_operation()))

    assert findings["mensalidade_faturada_media"].valor["competencias"] == 2
    assert findings["glosa_pct_12m"].valor == 0.05
