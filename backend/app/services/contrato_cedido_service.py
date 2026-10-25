"""Read-only projection of the assigned contract for operation screens and PDFs."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.integrations.comprasnet.parsing import parse_data, parse_number
from app.services.capacidade_contrato_service import explicar_capacidade
from app.services.findings.adapters.fluxo_caixa import _month, reconcile_payments
from app.services.findings.version import EMITTER_VERSION


def _rows(query):
    response = query.execute()
    return list((response.data or []) if response else [])


def _number(value: Any) -> float:
    return float(parse_number(value) or 0.0)


def _cancelled(invoice: dict[str, Any]) -> bool:
    return invoice.get("nota_cancelada") is True or "cancel" in str(invoice.get("situacao") or "").lower()


def _finding_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = {key: row.get(key) for key in ("valor", "estado", "confianca")}
    if row.get("estado") == "NAO_VERIFICADO":
        pending = row.get("pendencia") or {}
        result["motivo"] = pending.get("motivo") or "não verificado"
    return result


def contrato_cedido_view(operation_id: str, *, database=None) -> dict[str, Any]:
    if database is None:
        from app.core.database import supabase
        database = supabase
    operations = _rows(database.table("operations").select(
        "id,capacidade_memoria,valor_enquadrado,valor_solicitado"
    ).eq("id", operation_id).limit(1))
    if not operations:
        raise LookupError("operacao nao encontrada")
    operation = operations[0]
    snapshots = _rows(database.table("component_snapshots").select(
        "component,status,parsed_result,completed_at"
    ).eq("operation_id", operation_id).in_("component", ["contratos_comprasnet", "pagamentos_portal"]))
    by_component = {row.get("component"): row for row in snapshots}
    compras_snap = by_component.get("contratos_comprasnet") or {}
    pagamentos_snap = by_component.get("pagamentos_portal") or {}
    compras = compras_snap.get("parsed_result") or {}
    pagamentos = pagamentos_snap.get("parsed_result") or {}
    contract = compras.get("contrato_comprasnet") or {}
    invoices = [item for item in (compras.get("faturas") or []) if isinstance(item, dict) and not _cancelled(item)]
    orders = [item for item in (pagamentos.get("ordens_bancarias") or []) if isinstance(item, dict)]
    matches = reconcile_payments(invoices, orders, contract.get("numero"), contract.get("uasg")) if contract else []
    payments_by_invoice = {id(invoice): order for invoice, order in matches}
    paid_orders = {id(order): order for _, order in matches}
    gross = round(sum(_number(item.get("valor")) for item in invoices), 2)
    glosa = round(sum(_number(item.get("glosa")) for item in invoices), 2)
    liquid = round(sum(_number(item.get("valorliquido")) or max(_number(item.get("valor")) - _number(item.get("glosa")), 0) for item in invoices), 2)
    received = round(sum(_number(order.get("valor")) for order in paid_orders.values()), 2)
    total = _number(contract.get("valor_global"))
    first = parse_data(contract.get("vigencia_inicio"))
    latest_invoice = max((parse_data(item.get("emissao")) or parse_data(item.get("data_liquidacao")) for item in invoices), default=None)
    alert = bool(first and first <= date.today() - timedelta(days=180) and (not latest_invoice or latest_invoice <= date.today() - timedelta(days=180)))

    monthly: dict[tuple[int, int], dict[str, Any]] = {}
    for invoice in invoices:
        competence = _month(invoice)
        if not competence:
            continue
        year, month = competence
        key = competence
        item = monthly.setdefault(key, {"competencia": f"{month:02d}/{year}", "bruto": 0.0, "glosa": 0.0, "liquido": 0.0, "data_liquidacao": None, "data_pagamento": None, "valor_pago": 0.0, "dias_ate_pagamento": None, "deposito_vinculado": False})
        item["bruto"] += _number(invoice.get("valor")); item["glosa"] += _number(invoice.get("glosa")); item["liquido"] += _number(invoice.get("valorliquido")) or max(_number(invoice.get("valor")) - _number(invoice.get("glosa")), 0)
        item["data_liquidacao"] = invoice.get("data_liquidacao") or item["data_liquidacao"]
        order = payments_by_invoice.get(id(invoice))
        if order:
            item["data_pagamento"] = order.get("data")
            item["valor_pago"] += min(_number(order.get("valor")), _number(invoice.get("valorliquido")) or _number(invoice.get("valor")))
            paid, settled = parse_data(order.get("data")), parse_data(invoice.get("data_liquidacao"))
            item["dias_ate_pagamento"] = (paid - settled).days if paid and settled else None
    flow_runs = _rows(database.table("finding_runs").select("id,created_at").eq("operation_id", operation_id).eq("especialista", "fluxo_caixa").eq("versao_emissor", EMITTER_VERSION).order("created_at", desc=True).limit(1))
    finding_rows = _rows(database.table("findings").select("codigo,valor,estado,confianca,pendencia").eq("run_id", flow_runs[0]["id"])) if flow_runs else []
    findings = {str(row.get("codigo")): _finding_view(row) for row in finding_rows}
    deposits = ((findings.get("pago_portal_vs_comprasnet") or {}).get("valor") or {}).get("deposito_conta_vinculada") or {}
    risk_codes = ("saldo_empenho_ano", "empenho_insuficiente", "prazo_ateste_dias", "prazo_liquidacao_dias", "prazo_pagamento_dias", "faturas_liquidadas_sem_pagamento", "antecipacao_concorrente", "cobertura_parcela")
    return {
        "status": compras.get("status_consulta") or "NAO_VERIFICADO",
        "identificacao": {"objeto": contract.get("objeto"), "orgao": contract.get("orgao"), "uasg": contract.get("uasg"), "numero": contract.get("numero"), "modalidade_execucao": contract.get("modalidade_execucao"), "vigencia": {"inicio": contract.get("vigencia_inicio"), "fim": contract.get("vigencia_fim")}, "valor_original": compras.get("valor_original") or contract.get("valor_inicial"), "valor_vigente": compras.get("valor_vigente") or contract.get("valor_global"), "prorrogavel": contract.get("prorrogavel"), "n_prorrogacoes": compras.get("n_prorrogacoes", 0), "historico": compras.get("historico") or []},
        "execucao": {"contratado": contract.get("valor_global"), "faturado_bruto": gross, "glosa": glosa, "faturado_liquido": liquid, "recebido": received, "depositos_conta_vinculada": deposits, "a_receber": round(max(total - gross, 0), 2), "pct_executado": round(gross / total, 6) if total else None, "alerta_sem_execucao": alert},
        "fluxo_mensal": [monthly[key] for key in sorted(monthly)],
        "risco": {
            **{code: findings.get(code) for code in risk_codes},
            "capacidade_memoria": operation.get("capacidade_memoria"),
            "capacidade_explicacao": explicar_capacidade(operation.get("capacidade_memoria")),
        },
        "qualidade": {"coletas": {name: row.get("completed_at") for name, row in by_component.items()}, "filtro_usado": pagamentos.get("filtro_usado"), "parcial_portal": bool((pagamentos.get("paginacao") or {}).get("parcial")), "nao_verificados": {code: value.get("motivo") for code, value in findings.items() if value and value.get("estado") == "NAO_VERIFICADO"}},
    }
