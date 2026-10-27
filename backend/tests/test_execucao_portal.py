from app.services.execucao_portal import faturas_equivalentes_portal
from app.services.findings.adapters.fluxo_caixa import emit_fluxo_caixa
from app.services.findings.schemas import Estado


def _orders():
    return [
        {"documento": "510678OB022812", "data": "2026-07-17", "valor": "74.942,98", "observacao": "CONTR. 27/2026 - COMP. 07/2026 - DOC. FISCAI(S): 148"},
        {"documento": "510678DR801319", "data": "2026-07-17", "valor": "1.626,54", "observacao": "ISSQN CONTR. 27/2026 - COMP. 07/2026 - DOC. FISCAI(S): 148"},
        {"documento": "510678OB027114", "data": "2026-08-25", "valor": "160.016,78", "observacao": "CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 151"},
        {"documento": "510678DR801565", "data": "2026-08-25", "valor": "3.472,96", "observacao": "ISSQN CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 151"},
        {"documento": "510678OB030626", "data": "2026-09-21", "valor": "115.555,63", "observacao": "CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 154"},
        {"documento": "510678DR801738", "data": "2026-09-21", "valor": "2.507,99", "observacao": "ISSQN CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 154"},
        {"documento": "510678DR801953", "data": "2026-10-19", "valor": "12.312,23", "observacao": "ISSQN CONTR. 27/2026 - COMP. 10/2026 - DOC. FISCAI(S): 158"},
        {"documento": "510678OB034388", "data": "2026-10-19", "valor": "219.527,12", "observacao": "CONTR. 27/2026 - COMP. 10/2026 - DOC. FISCAI(S): 158"},
    ]


def _payments(**extra):
    return {"status_consulta": "ENCONTRADO", "paginacao": {"parcial": False}, "ordens_bancarias": _orders(), **extra}


def _appropriation_orders():
    return [
        {"documento": "5106782026DR801319", "data": "2026-07-17", "valor": "1.626,54", "observacao": "AP/2315031/00525177/2026. RETENCAO DE ISSQN SOBRE SERVICOS - CONTR. 27/2026 - COMP. 07/2026 - DOC. FISCAI(S): 148"},
        {"documento": "5106782026OB022812", "data": "2026-07-17", "valor": "74.942,98", "observacao": "AP/2315031/00525177/2026. PAGAMENTO DE SERVICOS - CONTR. 27/2026 - COMP. 07/2026 - DOC. FISCAI(S): 148"},
        {"documento": "5106782026OB027114", "data": "2026-08-25", "valor": "160.016,78", "observacao": "AP/2315031/00525177/2026. PAGAMENTO DE SERVICOS - CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 151"},
        {"documento": "5106782026DR801565", "data": "2026-08-25", "valor": "3.472,96", "observacao": "AP/2315031/00525177/2026. RETENCAO DE ISSQN SOBRE SERVICOS - CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 151"},
        {"documento": "5106782026OB030626", "data": "2026-09-21", "valor": "115.555,63", "observacao": "AP/2315031/00525177/2026. PAGAMENTO DE SERVICOS - CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 154"},
        {"documento": "5106782026DR801738", "data": "2026-09-21", "valor": "2.507,99", "observacao": "AP/2315031/00525177/2026. RETENCAO DE ISSQN SOBRE SERVICOS - CONTR. 27/2026 - COMP. 08/2026 - DOC. FISCAI(S): 154"},
        {"documento": "5106782026DR801953", "data": "2026-10-19", "valor": "12.312,23", "observacao": "AP/2315031/00525177/2026. RETENCAO DE ISSQN SOBRE SERVICOS - CONTR. 27/2026 - COMP. 10/2026 - DOC. FISCAI(S): 158"},
        {"documento": "5106782026OB034388", "data": "2026-10-19", "valor": "219.527,12", "observacao": "AP/2315031/00525177/2026. PAGAMENTO DE SERVICOS - CONTR. 27/2026 - COMP. 10/2026 - DOC. FISCAI(S): 158"},
    ]


def test_reconstructs_execution_from_complete_portal_orders():
    result = faturas_equivalentes_portal(_payments(), "00027/2026", "510678")

    assert result["disponivel"] is True
    assert [invoice["numero"] for invoice in result["faturas"]] == ["148", "151", "154", "158"]
    assert sum(invoice["valor"] for invoice in result["faturas"]) == 589_962.23
    assert sum(invoice["valorliquido"] for invoice in result["faturas"]) == 570_042.51
    by_competence = {}
    for invoice in result["faturas"]:
        reference = invoice["dados_referencia"]
        key = f'{reference["mesref"]:02d}/{reference["anoref"]}'
        by_competence[key] = by_competence.get(key, 0) + invoice["valor"]
    assert by_competence == {"07/2026": 76_569.52, "08/2026": 281_553.36, "10/2026": 231_839.35}


def test_appropriation_prefix_keeps_issqn_out_of_supplier_execution_and_payments():
    from app.services.findings.adapters.fluxo_caixa import _is_tax_or_withholding

    orders = _appropriation_orders()
    assert [_is_tax_or_withholding(order) for order in orders] == [True, False, False, True, False, True, True, False]
    payments = _payments(ordens_bancarias=orders)
    result = faturas_equivalentes_portal(payments, "00027/2026", "510678")
    assert len(result["faturas"]) == 4
    assert sum(item["valorliquido"] for item in result["faturas"]) == 570_042.51
    assert sum(item["valor"] for item in result["faturas"]) == 589_962.23

    findings = {item.codigo: item for item in emit_fluxo_caixa({
        "contratos_comprasnet": {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"numero": "00027/2026", "uasg": "510678"}, "faturas": []},
        "pagamentos_portal": payments,
    }, fingerprint="f", operation={"data_referencia": "2026-10-20"})}
    assert findings["pagamentos_contrato_12m"].valor["valor"] == 570_042.51
    assert findings["pagamentos_contrato_12m"].valor["quantidade"] == 4


def test_portal_partial_or_capped_is_not_execution_evidence():
    assert not faturas_equivalentes_portal(_payments(paginacao={"parcial": True}), "27/2026", "510678")["disponivel"]
    assert not faturas_equivalentes_portal(_payments(atingiu_cap=True), "27/2026", "510678")["disponivel"]


def test_excludes_other_contract_ug_cancelled_orders_and_groups_missing_nf_by_competence_date():
    orders = _orders() + [
        {"documento": "510678OBOTHER", "data": "2026-10-20", "valor": "100,00", "observacao": "CONTR. 28/2026 NF 999"},
        {"documento": "999999OBOTHER", "data": "2026-10-20", "valor": "100,00", "observacao": "CONTR. 27/2026 NF 999"},
        {"documento": "510678OBNOF", "data": "2026-10-21", "valor": "50,00", "observacao": "CONTR. 27/2026 COMP. 10/2026"},
        {"documento": "510678OBCANCEL", "data": "2026-10-21", "valor": "0,00", "observacao": "CANCELAMENTO DA 510678OBNOF"},
        {"documento": "510678OBNOF2", "data": "2026-10-22", "valor": "70,00", "observacao": "CONTR. 27/2026 COMP. 10/2026"},
    ]
    result = faturas_equivalentes_portal(_payments(ordens_bancarias=orders), "27/2026", "510678")

    fallback = [invoice for invoice in result["faturas"] if invoice["numero"] is None]
    assert len(fallback) == 1
    assert fallback[0]["valor"] == 70
    assert fallback[0]["competencia_inferida"] is False


def test_cash_findings_use_portal_execution_without_inventing_invoice_dates_or_commitment_balance():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00027/2026", "uasg": "510678", "valor_global": 1_000_000, "vigencia_inicio": "2026-01-01", "vigencia_fim": "2027-01-01"},
            "faturas": [],
            "empenhos_detalhe": [{"empenhado": "0,00", "liquidado": "0,00", "pago": "246.244,67", "a_liquidar": "-246.244,67"}],
        },
        "pagamentos_portal": _payments(),
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(
        snapshots, fingerprint="f", operation={
            "data_referencia": "2026-10-20", "capacidade_memoria": {"fator_liquido": 0.85},
            "taxa_breakdown": {"taxa_sugerida_am": 0.03, "detalhes": {"prazo_meses": 12}},
            "valor_solicitado": 100_000,
        },
    )}

    assert findings["mensalidade_faturada_media"].evidencia[0]["componente"] == "pagamentos_portal"
    assert findings["mensalidade_faturada_media"].valor["fonte"] == "PORTAL_OB"
    assert findings["meses_sem_fatura_12m"].evidencia[0]["componente"] == "pagamentos_portal"
    assert findings["glosa_pct_12m"].estado == Estado.NAO_VERIFICADO
    assert findings["prazo_pagamento_dias"].pendencia == {"motivo": "sem datas de ateste/liquidação (execução reconstruída pelo Portal)"}
    assert findings["saldo_empenho_ano"].estado == Estado.NAO_VERIFICADO
    assert "empenho_insuficiente" not in findings
