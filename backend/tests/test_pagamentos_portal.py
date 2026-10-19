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
    assert findings["faturas_liquidadas_sem_pagamento"].valor["quantidade"] == 0


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
    monkeypatch.setattr(pagamentos_portal, "_context", lambda _op: ({"cnpj": "02955426000124"}, _comprasnet_found(), {}))
    monkeypatch.setattr(pagamentos_portal.httpx, "Client", lambda **_kwargs: _Client())

    def pages(_client, _cnpj, year, uasg):
        calls.append((year, uasg))
        if uasg:
            return [], {"registros": 0}
        return ([{"documento": "OB", "data": "2026-09-20", "valor": "19.457,17", "observacao": "Pagamento contrato 76/2026"}], {"registros": 1})

    monkeypatch.setattr(pagamentos_portal, "_pages", pages)
    result = pagamentos_portal._fetch("02955426000124", operation_id="op", today=date(2026, 10, 19))

    assert result["filtro_usado"] == "OBSERVACAO"
    assert len(result["ordens_bancarias"]) == 2
    assert calls == [(2026, "194004"), (2025, "194004"), (2026, None), (2025, None)]


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
