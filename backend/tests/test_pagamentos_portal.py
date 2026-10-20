from datetime import date

from app.services.findings.adapters.fluxo_caixa import emit_fluxo_caixa, reconcile_payments
from app.services.findings.schemas import Estado
from app.workers.tasks import pagamentos_portal


def _invoice(number, liquidated="2026-09-12", value="19.457,17"):
    return {
        "numero": number, "emissao": "2026-09-05", "data_liquidacao": liquidated,
        "valor": "25.760,13", "valorliquido": value, "nota_cancelada": False,
    }


def test_reconciliation_prefers_observation_then_value_and_is_one_to_one():
    invoices = [_invoice("NF 0001"), _invoice("NF 0002", "2026-09-13")]
    orders = [
        {"data": "15/09/2026", "valor": "19.457,17", "observacao": "Contrato 76/2026 NF 0001"},
        {"data": "16/09/2026", "valor": "19.457,17", "observacao": "sem referência"},
    ]

    matches = reconcile_payments(invoices, orders, "00076/2026")

    assert [(invoice["numero"], order["data"]) for invoice, order in matches] == [
        ("NF 0001", "15/09/2026"), ("NF 0002", "16/09/2026"),
    ]


def test_observation_matches_numeric_tokens_not_substrings():
    invoice = _invoice("12")
    orders = [
        {"data": "15/09/2026", "valor": "1,00", "observacao": "competência 2026-12"},
        {"data": "16/09/2026", "valor": "2,00", "observacao": "referência 112"},
    ]

    assert reconcile_payments([invoice], orders, "00076/2026") == []


def test_contract_observations_require_value_and_choose_oldest_matching_order():
    invoices = [
        _invoice("NF-A", value="100,00"),
        _invoice("NF-B", "2026-09-13", "200,00"),
        _invoice("NF-C", "2026-09-14", "300,00"),
    ]
    orders = [
        {"data": "20/09/2026", "valor": "300,00", "observacao": "Contrato 76/2026"},
        {"data": "18/09/2026", "valor": "200,00", "observacao": "Contrato 76/2026"},
        {"data": "17/09/2026", "valor": "100,00", "observacao": "Contrato 76/2026"},
        {"data": "15/09/2026", "valor": "999,00", "observacao": "Contrato 76/2026"},
    ]

    matches = reconcile_payments(invoices, orders, "000762026")

    assert [(invoice["valorliquido"], order["valor"]) for invoice, order in matches] == [
        ("100,00", "100,00"), ("200,00", "200,00"), ("300,00", "300,00"),
    ]


def test_cancelled_order_is_removed_and_reissued_order_matches_black_rock_invoice_29():
    invoice = _invoice("29", value="59.058,41")
    orders = [
        {"documento": "2026OB000123", "data": "15/09/2026", "valor": "58.644,69", "observacao": "CT 17/2026 NF 29"},
        {"documento": "2026OB000124", "data": "16/09/2026", "valor": "0,00", "observacao": "CANCELAMENTO DA 2026OB000123"},
        {"documento": "2026OB000125", "data": "17/09/2026", "valor": "59.058,41", "observacao": "CT 17/2026 NF 29"},
    ]

    matches = reconcile_payments([invoice], orders, "00017/2026")

    assert [(invoice["numero"], order["documento"]) for invoice, order in matches] == [("29", "2026OB000125")]


def test_cancellation_citing_short_code_removes_order_with_full_portal_code():
    invoice = _invoice("29", liquidated="2026-07-06", value="68.953,20")
    orders = [
        {"documento": "154069152762026OB003452", "data": "14/07/2026", "valor": "58644.69",
         "observacao": "PGTO NF/FATURA  2026/29 BLACK ROCK - CT 2026/018 - 1A MEDICAO"},
        {"documento": "154069152762026OB003484", "data": "15/07/2026", "valor": "59058.41",
         "observacao": "PGTO NF/FATURA  2026/29 BLACK ROCK - CT 2026/018 - 1A MEDICAO"},
        {"documento": "154069152762026OB003480", "data": "15/07/2026", "valor": "-58644.69",
         "observacao": "10 - CANCELAMENTO DA 2026OB003452 - POR COMANDO DO GESTOR"},
    ]

    matches = reconcile_payments([invoice], orders, "00018/2026")

    assert [order["documento"] for _, order in matches] == ["154069152762026OB003484"]


def test_orders_for_another_contract_are_not_candidates_even_in_same_uasg():
    invoice = _invoice("29", value="59.058,41")
    orders = [
        {"documento": "OB-other", "data": "15/09/2026", "valor": "59.058,41", "observacao": "CT 2026/017 NF 29"},
        {"documento": "OB-own", "data": "16/09/2026", "valor": "59.000,00", "observacao": "CONTRATO Nº 00016/2026"},
    ]

    matches = reconcile_payments([invoice], orders, "00016/2026")

    assert [order["documento"] for _, order in matches] == ["OB-own"]


def test_retained_value_matches_only_without_other_contract_and_keeps_invoice_priority():
    invoices = [_invoice("29", value="100,00"), _invoice("30", "2026-09-13", "200,00")]
    orders = [
        {"documento": "OB-30", "data": "16/09/2026", "valor": "140,00", "observacao": "CT 17/2026 NF 30"},
        {"documento": "OB-29", "data": "15/09/2026", "valor": "70,00", "observacao": "sem contrato"},
        {"documento": "OB-other", "data": "14/09/2026", "valor": "100,00", "observacao": "CT 18/2026 NF 29"},
    ]

    matches = reconcile_payments(invoices, orders, "00017/2026")

    assert [(invoice["numero"], order["documento"]) for invoice, order in matches] == [
        ("29", "OB-29"), ("30", "OB-30"),
    ]


def test_one_order_settles_all_fourteen_explicit_invoices_and_ignores_tax_other_ug_and_contract():
    invoices = [_invoice(str(number), "2026-09-10", "100,00") for number in range(1, 15)]
    orders = [
        {
            "documento": "12345600000001", "data": "2026-09-15", "valor": "1.400,00",
            "observacao": "CT 04/2025 " + " ".join(f"NF {number}" for number in range(1, 15)),
        },
        {"documento": "12345600000002", "data": "2026-09-14", "valor": "100,00", "observacao": "INSS NF 1"},
        {"documento": "12345600000003", "data": "2026-09-14", "valor": "100,00", "observacao": "IRPJ NF 2"},
        {"documento": "99999900000001", "data": "2026-09-11", "valor": "100,00", "observacao": "CT 04/2025 NF 3"},
        {"documento": "12345600000004", "data": "2026-09-11", "valor": "100,00", "observacao": "CONTRATO: 027/2025 NF 4"},
    ]

    matches = reconcile_payments(invoices, orders, "00004/2025", "123456")

    assert [invoice["numero"] for invoice, _ in matches] == [str(number) for number in range(1, 15)]
    assert {order["documento"] for _, order in matches} == {"12345600000001"}


def test_contract_reference_variants_exclude_another_contract():
    invoice = _invoice("1", value="100,00")
    variants = [
        "CONTRATO: 027/2025", "CONTRATO Nº: 027/2025", "CONT Nº: 027/2025",
        "CONT. Nº: 027/2025", "CONT N : 027/2025", "CONT 027/2025",
    ]
    orders = [
        {"documento": f"1234560000000{index}", "data": "2026-09-11", "valor": "100,00", "observacao": f"{text} NF 1"}
        for index, text in enumerate(variants, start=1)
    ]

    assert reconcile_payments([invoice], orders, "00004/2025", "123456") == []


def test_antecipacao_concorrente_includes_other_ug_and_extracts_operation_and_bank():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00004/2025", "uasg": "123456"},
            "faturas": [_invoice("1")],
        },
        "pagamentos_portal": {
            "status_consulta": "ENCONTRADO",
            "ordens_bancarias": [{
                "documento": "99999900000001", "codigoUg": "999999", "data": "2026-09-20",
                "valor": "1.200,00", "observacao": "ANTECIPAGOV Nº OP. 5043 BANCO 274",
            }],
        },
    }

    findings = {item.codigo: item for item in emit_fluxo_caixa(
        snapshots, fingerprint="f", operation={"data_referencia": "19/10/2026"},
    )}

    assert findings["antecipacao_concorrente"].valor == {
        "quantidade": 1, "valor": 1200.0, "ugs": ["999999"],
        "numeros_operacao": ["5043"], "bancos": ["274"], "ultima_data": "2026-09-20",
        "operacoes": [{
            "numero_op": "5043", "data_op": None, "contrato_vinculado": None,
            "mesmo_contrato": False, "ultima_data": "2026-09-20", "ativa": True,
        }],
    }


def test_anticipations_detail_contract_link_and_active_status_and_exclude_guarantee_deposit():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00004/2025", "uasg": "123456"},
            "faturas": [_invoice("1", "2026-09-10", "900,00")],
        },
        "pagamentos_portal": {
            "status_consulta": "ENCONTRADO",
            "ordens_bancarias": [
                {"documento": "99999900000001", "data": "2026-09-20", "valor": "100,00", "observacao": "VINCULADO A OPERACAO DE CREDITO ANTECIPAGOV Nº 4701, DE 09/02/2026, ATIVA PARA O CONTRATO Nº 00015/2025"},
                {"documento": "12345600000001", "data": "2026-10-01", "valor": "100,00", "observacao": "ANTECIPAGOV Nº 4903, DE 01/10/2026, ATIVA PARA O CONTRATO Nº 00004/2025"},
                {"documento": "12345600000002", "data": "2026-07-01", "valor": "100,00", "observacao": "ANTECIPAGOV Nº 4223, DE 01/01/2026, ATIVA PARA O CONTRATO Nº 00004/2025"},
                {"documento": "12345600000003", "data": "2026-08-01", "valor": "100,00", "observacao": "CONTRATO Nº 00004/2025 pagamento normal"},
                {"documento": "12345600000004", "data": "2026-09-15", "valor": "900,00", "observacao": "CT 04/2025 NF 1"},
                {"documento": "12345600000005", "data": "2026-09-16", "valor": "100,00", "observacao": "DEPOSITO GARANTIA - CONTA VINCULADA CT 04/2025"},
            ],
        },
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(
        snapshots, fingerprint="f", operation={"data_referencia": "19/10/2026"},
    )}

    anticipation = findings["antecipacao_concorrente"].valor["operacoes"]
    assert anticipation == [
        {"numero_op": "4223", "data_op": "2026-01-01", "contrato_vinculado": "00004/2025", "mesmo_contrato": True, "ultima_data": "2026-07-01", "ativa": False},
        {"numero_op": "4701", "data_op": "2026-02-09", "contrato_vinculado": "00015/2025", "mesmo_contrato": False, "ultima_data": "2026-09-20", "ativa": True},
        {"numero_op": "4903", "data_op": "2026-10-01", "contrato_vinculado": "00004/2025", "mesmo_contrato": True, "ultima_data": "2026-10-01", "ativa": True},
    ]
    paid = findings["pago_portal_vs_comprasnet"].valor
    assert paid["pago_portal_ano"] == 900.0
    assert paid["deposito_conta_vinculada"] == {"valor": 100.0, "pct_sobre_pago": 0.1}


def test_payment_rate_is_unverified_without_settled_invoices_and_reports_contract_payments_12m():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00004/2025", "uasg": "123456"},
            "faturas": [],
        },
        "pagamentos_portal": {
            "status_consulta": "ENCONTRADO",
            "ordens_bancarias": [
                {"documento": "12345600000001", "data": "2026-01-10", "valor": "100,00", "observacao": "CT 04/2025"},
                {"documento": "12345600000002", "data": "2026-03-11", "valor": "200,00", "observacao": "CT 04/2025"},
            ],
        },
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(
        snapshots, fingerprint="f", operation={"data_referencia": "19/10/2026"},
    )}

    assert findings["taxa_conciliacao_pagamentos"].estado == Estado.NAO_VERIFICADO
    assert findings["taxa_conciliacao_pagamentos"].pendencia == {"motivo": "sem faturas liquidadas"}
    assert findings["pagamentos_contrato_12m"].valor == {
        "valor": 300.0, "quantidade": 2, "ultima_data": "2026-03-11", "intervalo_medio_dias": 60.0,
    }


def test_funai_payment_findings_are_informative_and_use_liquid_value():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00076/2026"},
            "faturas": [_invoice("NF 76")],
        },
        "pagamentos_portal": {
            "status_consulta": "ENCONTRADO",
            "ordens_bancarias": [{
                "documento": "OB-1", "data": "20/09/2026", "valor": "19.457,17",
                "observacao": "NF 76 contrato 76/2026",
            }],
        },
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(snapshots, fingerprint="f", operation={"data_referencia": "19/10/2026"})}

    assert findings["prazo_pagamento_dias"].valor == 8.0
    assert findings["ciclo_caixa_dias"].valor == 15.0
    assert findings["taxa_conciliacao_pagamentos"].valor == 1.0
    assert findings["pago_portal_vs_comprasnet"].valor["pago_portal_ano"] == 19_457.17
    assert findings["pago_portal_vs_comprasnet"].valor["ordens_nao_conciliadas"] == {"quantidade": 0, "valor": 0}
    assert findings["faturas_liquidadas_sem_pagamento"].valor["quantidade"] == 0


def test_paid_portal_total_uses_only_reconciled_orders_and_reports_the_rest():
    snapshots = {
        "contratos_comprasnet": {
            "status_consulta": "ENCONTRADO",
            "contrato_comprasnet": {"numero": "00017/2026"},
            "faturas": [_invoice("29", value="100,00")],
        },
        "pagamentos_portal": {
            "status_consulta": "ENCONTRADO",
            "ordens_bancarias": [
                {"documento": "OB-match", "data": "20/09/2026", "valor": "100,00", "observacao": "CT 17/2026 NF 29"},
                {"documento": "OB-open", "data": "21/09/2026", "valor": "50,00", "observacao": "CT 17/2026"},
                {"documento": "OB-other", "data": "21/09/2026", "valor": "900,00", "observacao": "CT 18/2026"},
            ],
        },
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(
        snapshots, fingerprint="f", operation={"data_referencia": "19/10/2026"},
    )}

    value = findings["pago_portal_vs_comprasnet"].valor
    assert value["pago_portal_ano"] == 100.0
    assert value["ordens_nao_conciliadas"] == {"quantidade": 1, "valor": 50.0}


def test_payment_findings_are_unverified_when_not_applicable():
    snapshots = {
        "contratos_comprasnet": {"status_consulta": "ENCONTRADO", "faturas": [_invoice("1")]},
        "pagamentos_portal": {"status_consulta": "NAO_APLICAVEL"},
    }
    findings = {item.codigo: item for item in emit_fluxo_caixa(snapshots, fingerprint="f", operation={})}

    assert findings["prazo_pagamento_dias"].estado == Estado.NAO_VERIFICADO
    assert findings["prazo_pagamento_dias"].pendencia["motivo"] == "pagamentos do Portal não aplicáveis"


class _Client:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _comprasnet_found():
    return {"status_consulta": "ENCONTRADO", "contrato_comprasnet": {"numero": "00076/2026", "uasg": "194004"}}


def test_portal_falls_back_without_uasg_and_filters_contract_observation(monkeypatch):
    calls = []
    comprasnet = _comprasnet_found()
    comprasnet["contrato_comprasnet"]["numero"] = "000762026"
    monkeypatch.setattr(pagamentos_portal, "_context", lambda _op: ({"cnpj": "02955426000124"}, comprasnet, {}))
    monkeypatch.setattr(pagamentos_portal.httpx, "Client", lambda **_kwargs: _Client())

    def pages(_client, _cnpj, year, uasg, **_kwargs):
        calls.append((year, uasg))
        if uasg:
            return [], {"registros": 0}
        return ([{"documento": "OB", "data": "2026-09-20", "valor": "19.457,17", "observacao": "Pagamento contrato 76/2026"}], {"registros": 1})

    monkeypatch.setattr(pagamentos_portal, "_pages", pages)
    result = pagamentos_portal._fetch("02955426000124", operation_id="op", today=date(2026, 10, 19))

    assert result["filtro_usado"] == "OBSERVACAO"
    assert len(result["ordens_bancarias"]) == 2
    assert calls == [(2026, "194004"), (2025, "194004"), (2026, None), (2025, None)]


def test_portal_uses_documented_ug_filter_and_persists_ug_payload_fields(monkeypatch):
    urls = []
    monkeypatch.setattr(
        pagamentos_portal,
        "fetch_json_with_retry",
        lambda _client, url, **_kwargs: urls.append(url) or [],
    )

    records, _pagination = pagamentos_portal._pages(
        object(), "02955426000124", 2026, "194004", deadline=pagamentos_portal.time.monotonic() + 10,
    )
    order = pagamentos_portal._order({"codigoUg": "194004", "ug": "FUNAI", "documento": "19400400000001"})

    assert records == []
    assert "&ug=194004" in urls[0]
    assert order["codigoUg"] == "194004"
    assert order["ug"] == "FUNAI"


def test_portal_marks_partial_result_instead_of_raising_at_total_timeout(monkeypatch):
    moments = iter((0.0, 0.0, 121.0))
    monkeypatch.setattr(pagamentos_portal.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(
        pagamentos_portal,
        "fetch_json_with_retry",
        lambda *_args, **_kwargs: [{"documento": "OB-1"}],
    )

    records, pagination = pagamentos_portal._pages(
        object(), "02955426000124", 2026, "194004", deadline=120.0,
    )

    assert records == [{"documento": "OB-1"}]
    assert pagination["parcial"] is True
    assert pagination["paginas_lidas"] == 1


def test_portal_failure_preserves_previous_snapshot(monkeypatch):
    previous = {"status_consulta": "ENCONTRADO", "ordens_bancarias": [{"documento": "antiga"}]}
    monkeypatch.setattr(pagamentos_portal, "_context", lambda _op: ({}, _comprasnet_found(), previous))
    monkeypatch.setattr(pagamentos_portal.httpx, "Client", lambda **_kwargs: _Client())
    monkeypatch.setattr(pagamentos_portal, "_pages", lambda *_args: (_ for _ in ()).throw(RuntimeError("Portal indisponível")))

    result = pagamentos_portal._fetch("02955426000124", operation_id="op", today=date(2026, 10, 19))

    assert result["status_atualizacao"] == "MANTIDO"
    assert result["ordens_bancarias"] == previous["ordens_bancarias"]


def test_portal_is_not_applicable_without_found_comprasnet(monkeypatch):
    monkeypatch.setattr(pagamentos_portal, "_context", lambda _op: ({}, {"status_consulta": "NAO_VERIFICADO"}, {}))

    result = pagamentos_portal._fetch("02955426000124", operation_id="op", today=date(2026, 10, 19))

    assert result["status_consulta"] == "NAO_APLICAVEL"


def test_supplier_payment_mentioning_withholding_is_excluded_anywhere_in_observation():
    from app.services.findings.adapters.fluxo_caixa import _is_tax_or_withholding

    payment = {"observacao": "LIQUIDACAO DE NF(S): 38, 42, 43, CONTRATO Nº: 00004/2025 - DIRETIVA PATRIMONIAL LTDA - RETENCAO DE INSS CONFORME IN 971"}
    assert _is_tax_or_withholding(payment)
    for text in ("INSS NF(S): 105, 108", "IRPJ DE NF.(S): 105", "INSS-NFS(S) 3906", "RETENCAO DO ISS, CONFORME LC 116/2003", "ISS S/ SERVICO DE RECEPCAO"):
        assert _is_tax_or_withholding({"observacao": text}), text
