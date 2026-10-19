"""Deterministic cash-flow findings from the persisted Comprasnet enrichment."""

from __future__ import annotations

import re
import statistics
from datetime import date
from typing import Any
from unicodedata import combining, normalize

from app.integrations.comprasnet.parsing import number_tokens, parse_data, parse_number
from app.services.findings.adapters.common import finding
from app.services.findings.schemas import Confianca, Escopo, Estado


def _number(value: Any) -> float | None:
    return parse_number(value)


def _date(value: Any) -> date | None:
    return parse_data(value)


def _month(value: dict[str, Any]) -> tuple[int, int] | None:
    reference = value.get("dados_referencia") or value.get("referencia") or {}
    if isinstance(reference, dict):
        try:
            month = int(reference.get("mesref") or reference.get("mes") or 0)
            year = int(reference.get("anoref") or reference.get("ano") or 0)
            if 1 <= month <= 12 and year > 1900:
                return year, month
        except (TypeError, ValueError):
            pass
    issued = _date(value.get("emissao"))
    return (issued.year, issued.month) if issued else None


def _cancelled(invoice: dict[str, Any]) -> bool:
    value = invoice.get("nota_cancelada")
    if value is True or str(value).strip().lower() in {"true", "sim", "s", "1"}:
        return True
    return "cancel" in str(invoice.get("situacao") or "").lower()


def _median_days(invoices: list[dict[str, Any]], end_key: str) -> float | None:
    values = []
    for invoice in invoices:
        start, end = _date(invoice.get("emissao")), _date(invoice.get(end_key))
        if start and end and end >= start:
            values.append((end - start).days)
    return round(float(statistics.median(values)), 2) if values else None


def _unverified(
    code: str, path: str, fingerprint: str, motivo: str,
    component: str = "contratos_comprasnet",
):
    return finding(
        code, Escopo.OPERACAO, None, component=component, path=path,
        fingerprint=fingerprint, state=Estado.NAO_VERIFICADO,
        confidence=Confianca.BAIXA,
        pendencia={"motivo": motivo},
    )


def _months_between(start: date, end: date) -> int:
    return max((end.year - start.year) * 12 + end.month - start.month + 1, 1)


def _installment(operation: dict[str, Any]) -> float | None:
    pricing = operation.get("taxa_breakdown")
    if not isinstance(pricing, dict):
        return None
    rate = _number(pricing.get("taxa_sugerida_am"))
    details = pricing.get("detalhes")
    term = _number(details.get("prazo_meses")) if isinstance(details, dict) else None
    value = next(
        (
            parsed
            for field in (
                "valor_solicitado", "limite_aprovado", "valor_operacao_relatorio",
                "valor_enquadrado",
            )
            if (parsed := _number(operation.get(field))) is not None and parsed > 0
        ),
        None,
    )
    if rate is None or rate < 0 or term is None or term <= 0 or value is None:
        return None
    months = int(term)
    if months != term:
        return None
    return value / months if rate == 0 else value * rate / (1 - (1 + rate) ** -months)


def _reference_tokens(value: Any) -> set[str]:
    return number_tokens(value)


_CANCELS_ORDER = re.compile(r"\bCANCELAMENTO\s+DA\s+([A-Z0-9]+)\b", re.IGNORECASE)
_CITED_CONTRACT = re.compile(
    r"\b(?:CT|CONTRATO|CONT\.?)\s*(?:N\s*[º°O.]?\s*)?[:.]?\s*"
    r"(?:(\d{4})\s*/\s*(\d+)|(\d+)\s*/\s*(\d{4}))\b",
    re.IGNORECASE,
)
# Only orders whose observation STARTS with a tax keyword are tax payments.
# Supplier payments routinely mention "RETENCAO DE INSS" further in the text.
_TRIBUTARY_ORDER = re.compile(r"^\W*(?:\d+\s*-\s*)?(?:INSS|IRPJ|IRRF|CSLL|PIS|COFINS|ISS|DARF|RETENCAO)\b")
_OPERATION_NUMBER = re.compile(r"\b(?:N\s*[º°O.]?\s*)?OP\.?\s*(\d+)\b")
_BANK_NUMBER = re.compile(r"\b(?:BANCO|BCO)\s*(?:N\s*[º°O.]?\s*)?(\d+)\b")


def _document_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _normalized_observation(value: Any) -> str:
    return "".join(
        char for char in normalize("NFD", str(value or "").upper()) if not combining(char)
    )


def _is_tax_or_withholding(order: dict[str, Any]) -> bool:
    return bool(_TRIBUTARY_ORDER.search(_normalized_observation(order.get("observacao"))))


def _document_ug(order: dict[str, Any]) -> str:
    document = str(order.get("documento") or "").strip()
    return document[:6] if document[:6].isdigit() else ""


def _digits(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _cited_contracts(observation: Any) -> set[str]:
    """Return contract references explicitly introduced by CT or CONTRATO."""
    cited: set[str] = set()
    for match in _CITED_CONTRACT.finditer(str(observation or "")):
        if match.group(1):  # YYYY/NNN
            year, number = match.group(1), match.group(2)
        else:  # NNN/YYYY
            number, year = match.group(3), match.group(4)
        cited.add((f"{number}{year}").lstrip("0") or "0")
    return cited


def _valid_payment_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude cancelled, non-positive and tributary payment orders."""
    cancelled_documents = {
        _document_key(match.group(1))
        for order in orders
        for match in _CANCELS_ORDER.finditer(str(order.get("observacao") or ""))
    }
    active: list[dict[str, Any]] = []
    for order in orders:
        value = _number(order.get("valor"))
        if value is None or value <= 0:
            continue
        # The Portal returns the full code (UG + gestão + 2026OB003452) while the
        # cancellation text cites only the short form (2026OB003452).
        document = _document_key(order.get("documento"))
        if document and any(document.endswith(cancelled) for cancelled in cancelled_documents if cancelled):
            continue
        if _CANCELS_ORDER.search(str(order.get("observacao") or "")):
            continue
        if _is_tax_or_withholding(order):
            continue
        active.append(order)
    return active


def _active_payment_orders(
    orders: list[dict[str, Any]], contract_number: Any, contract_uasg: Any = None,
) -> list[dict[str, Any]]:
    """Return valid orders for the operation's contract and issuing UG."""
    contract_refs = _reference_tokens(contract_number)
    uasg = _digits(contract_uasg)
    active: list[dict[str, Any]] = []
    for order in _valid_payment_orders(orders):
        cited = _cited_contracts(order.get("observacao"))
        if cited and any(reference not in contract_refs for reference in cited):
            continue
        if len(uasg) == 6 and _document_ug(order) != uasg:
            continue
        active.append(order)
    return active


def _within_liquid_value(order: dict[str, Any], liquid_value: float | None) -> bool:
    value = _number(order.get("valor"))
    return (
        liquid_value is not None
        and value is not None
        and liquid_value * 0.70 <= value <= liquid_value * 1.005
    )


def reconcile_payments(
    invoices: list[dict[str, Any]], orders: list[dict[str, Any]], contract_number: Any,
    contract_uasg: Any = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Match invoice references 1:N first, then remaining values 1:1."""
    settled = [
        invoice for invoice in invoices
        if not _cancelled(invoice) and _date(invoice.get("data_liquidacao"))
    ]
    settled.sort(key=lambda invoice: _date(invoice.get("data_liquidacao")) or date.max)
    eligible_orders = _active_payment_orders(orders, contract_number, contract_uasg)
    used_orders: set[int] = set()
    matched_invoices: set[int] = set()
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    contract_refs = _reference_tokens(contract_number)

    # A single OB can explicitly settle several invoices. Process it by date so
    # each invoice receives the first payment order that names its NF.
    for order_index, order in sorted(
        enumerate(eligible_orders), key=lambda item: _date(item[1].get("data")) or date.max,
    ):
        order_date = _date(order.get("data"))
        if order_date is None:
            continue
        observation_tokens = _reference_tokens(order.get("observacao"))
        referenced = [
            invoice for invoice in settled
            if id(invoice) not in matched_invoices
            and (liquidated := _date(invoice.get("data_liquidacao")))
            and liquidated <= order_date
            and _reference_tokens(invoice.get("numero") or invoice.get("numero_fatura") or invoice.get("nota"))
            & observation_tokens
        ]
        if referenced:
            used_orders.add(order_index)
            for invoice in referenced:
                matched_invoices.add(id(invoice))
                matched.append((invoice, order))

    for invoice in settled:
        if id(invoice) in matched_invoices:
            continue
        liquidated = _date(invoice.get("data_liquidacao"))
        value = _number(invoice.get("valorliquido"))
        candidates = sorted([
            (index, order) for index, order in enumerate(eligible_orders)
            if index not in used_orders and _date(order.get("data")) and _date(order.get("data")) >= liquidated
        ], key=lambda item: _date(item[1].get("data")) or date.max)
        by_contract_and_value = [
            item for item in candidates
            if contract_refs & _cited_contracts(item[1].get("observacao"))
            and _within_liquid_value(item[1], value)
        ]
        by_value = [
            item for item in candidates
            if _within_liquid_value(item[1], value)
            and not _cited_contracts(item[1].get("observacao"))
        ]
        chosen = next(iter(by_contract_and_value or by_value), None)
        if chosen:
            used_orders.add(chosen[0])
            matched_invoices.add(id(invoice))
            matched.append((invoice, chosen[1]))
    return sorted(
        matched,
        key=lambda item: _date(item[0].get("data_liquidacao")) or date.max,
    )


def _payment_findings(
    comprasnet: dict[str, Any], payments: dict[str, Any] | None,
    reference: date, fingerprint: str,
) -> list:
    codes = (
        "prazo_pagamento_dias", "ciclo_caixa_dias", "faturas_liquidadas_sem_pagamento",
        "taxa_conciliacao_pagamentos", "pago_portal_vs_comprasnet", "antecipacao_concorrente",
    )
    if not isinstance(payments, dict):
        return [_unverified(code, "ordens_bancarias", fingerprint, "pagamentos do Portal indisponíveis", "pagamentos_portal") for code in codes]
    if payments.get("status_consulta") == "NAO_APLICAVEL":
        return [_unverified(code, "ordens_bancarias", fingerprint, "pagamentos do Portal não aplicáveis", "pagamentos_portal") for code in codes]
    if payments.get("status_consulta") != "ENCONTRADO":
        return [_unverified(code, "ordens_bancarias", fingerprint, "consulta de pagamentos do Portal indisponível", "pagamentos_portal") for code in codes]
    invoices = [item for item in comprasnet.get("faturas") or [] if isinstance(item, dict) and not _cancelled(item)]
    settled = [item for item in invoices if _date(item.get("data_liquidacao"))]
    orders = [item for item in payments.get("ordens_bancarias") or [] if isinstance(item, dict)]
    contract = comprasnet.get("contrato_comprasnet") or {}
    eligible_orders = _active_payment_orders(orders, contract.get("numero"), contract.get("uasg"))
    matched = reconcile_payments(settled, eligible_orders, contract.get("numero"), contract.get("uasg"))
    payment_days = [
        ((_date(order.get("data")) - _date(invoice.get("data_liquidacao"))).days)
        for invoice, order in matched
    ]
    cycle_days = [
        ((_date(order.get("data")) - _date(invoice.get("emissao"))).days)
        for invoice, order in matched
        if _date(invoice.get("emissao"))
    ]
    matched_ids = {id(invoice) for invoice, _ in matched}
    overdue = [
        invoice for invoice in settled
        if id(invoice) not in matched_ids and (reference - _date(invoice.get("data_liquidacao"))).days > 30
    ]
    matched_order_ids = {id(order) for _, order in matched}
    matched_orders_year = [
        order for order in eligible_orders
        if id(order) in matched_order_ids and (_date(order.get("data")) or date.min).year == reference.year
    ]
    unmatched_orders_year = [
        order for order in eligible_orders
        if id(order) not in matched_order_ids and (_date(order.get("data")) or date.min).year == reference.year
    ]
    portal_year = sum(_number(order.get("valor")) or 0.0 for order in matched_orders_year)
    comprasnet_year = sum(
        _number(invoice.get("valorliquido")) or 0.0 for invoice in settled
        if (_date(invoice.get("data_liquidacao")) or date.min).year == reference.year
    )
    anticipation_orders = [
        order for order in _valid_payment_orders(orders)
        if any(marker in _normalized_observation(order.get("observacao")) for marker in ("ANTECIPAGOV", "DOMICILIO BANCARIO"))
    ]
    operation_numbers = sorted({
        match.group(1).lstrip("0") or "0"
        for order in anticipation_orders
        for match in _OPERATION_NUMBER.finditer(str(order.get("observacao") or ""))
    }, key=int)
    banks = sorted({
        match.group(1).lstrip("0") or "0"
        for order in anticipation_orders
        for match in _BANK_NUMBER.finditer(str(order.get("observacao") or ""))
    }, key=int)
    anticipation_dates = [_date(order.get("data")) for order in anticipation_orders if _date(order.get("data"))]
    base = dict(component="pagamentos_portal", path="ordens_bancarias", fingerprint=fingerprint)
    output = [
        finding("prazo_pagamento_dias", Escopo.OPERACAO, round(float(statistics.median(payment_days)), 2), **base)
        if payment_days else _unverified("prazo_pagamento_dias", "ordens_bancarias", fingerprint, "nenhuma fatura conciliada", "pagamentos_portal"),
        finding("ciclo_caixa_dias", Escopo.OPERACAO, round(float(statistics.median(cycle_days)), 2), **base)
        if cycle_days else _unverified("ciclo_caixa_dias", "ordens_bancarias", fingerprint, "nenhuma fatura conciliada", "pagamentos_portal"),
        finding("faturas_liquidadas_sem_pagamento", Escopo.OPERACAO, {
            "quantidade": len(overdue), "valor": round(sum(_number(item.get("valorliquido")) or 0.0 for item in overdue), 2),
            "mais_antiga_dias": max(((reference - _date(item.get("data_liquidacao"))).days for item in overdue), default=None),
        }, **base),
        finding("taxa_conciliacao_pagamentos", Escopo.OPERACAO, round(len(matched) / len(settled), 6) if settled else 0.0, **base),
        finding("pago_portal_vs_comprasnet", Escopo.OPERACAO, {
            "pago_portal_ano": round(portal_year, 2), "liquidado_comprasnet_ano": round(comprasnet_year, 2),
            "razao": round(portal_year / comprasnet_year, 6) if comprasnet_year else None,
            "ordens_nao_conciliadas": {
                "quantidade": len(unmatched_orders_year),
                "valor": round(sum(_number(order.get("valor")) or 0.0 for order in unmatched_orders_year), 2),
            },
        }, **base),
        finding("antecipacao_concorrente", Escopo.OPERACAO, {
            "quantidade": len(anticipation_orders),
            "valor": round(sum(_number(order.get("valor")) or 0.0 for order in anticipation_orders), 2),
            "ugs": sorted({_document_ug(order) or _digits(order.get("codigoUg")) for order in anticipation_orders} - {""}),
            "numeros_operacao": operation_numbers,
            "bancos": banks,
            "ultima_data": max(anticipation_dates).isoformat() if anticipation_dates else None,
        }, **base),
    ]
    return output


def emit_fluxo_caixa(
    snapshots: dict[str, Any], *, fingerprint: str,
    operation: dict[str, Any] | None = None,
):
    """Projects contract-cash facts only; it never affects score or pricing."""
    operation = operation or {}
    comprasnet = snapshots.get("contratos_comprasnet")
    if not isinstance(comprasnet, dict):
        return [_unverified(code, "faturas", fingerprint, "snapshot Comprasnet indisponível") for code in _CASH_CODES]
    raw_invoices = comprasnet.get("faturas")
    invoices = [item for item in raw_invoices if isinstance(item, dict) and not _cancelled(item)] if isinstance(raw_invoices, list) else []
    reference = _date(operation.get("data_referencia")) or date.today()
    if not invoices:
        return [
            *[_unverified(code, "faturas", fingerprint, "contrato sem faturamento ainda") for code in _CASH_CODES if code not in _PAYMENT_CODES],
            *_payment_findings(comprasnet, snapshots.get("pagamentos_portal"), reference, fingerprint),
        ]

    contract = comprasnet.get("contrato_comprasnet") or {}
    by_month: dict[tuple[int, int], float] = {}
    for invoice in invoices:
        if (competence := _month(invoice)) is not None:
            by_month[competence] = by_month.get(competence, 0.0) + (_number(invoice.get("valor")) or 0.0)
    recent = [by_month[key] for key in sorted(by_month, reverse=True)[:6]]
    monthly_average = sum(recent) / len(recent) if recent else None
    deviation = statistics.pstdev(recent) if len(recent) > 1 else 0.0 if recent else None
    cv = deviation / monthly_average if monthly_average else None
    gross_total = sum(_number(invoice.get("valor")) or 0.0 for invoice in invoices)
    glosa_total = sum(_number(invoice.get("glosa")) or 0.0 for invoice in invoices)

    result = [
        finding("mensalidade_faturada_media", Escopo.OPERACAO, {
            "valor": round(monthly_average or 0.0, 2), "desvio": round(deviation or 0.0, 2),
            "cv": round(cv, 6) if cv is not None else None, "competencias": len(recent),
        }, component="contratos_comprasnet", path="faturas", fingerprint=fingerprint),
        finding("glosa_pct_12m", Escopo.OPERACAO, round(glosa_total / gross_total, 6) if gross_total else 0.0, component="contratos_comprasnet", path="faturas.glosa", fingerprint=fingerprint),
    ]
    for code, key in (("prazo_ateste_dias", "ateste"), ("prazo_liquidacao_dias", "data_liquidacao")):
        median = _median_days(invoices, key)
        result.append(
            finding(code, Escopo.OPERACAO, median, component="contratos_comprasnet", path=f"faturas.{key}", fingerprint=fingerprint)
            if median is not None else _unverified(
                code, f"faturas.{key}", fingerprint,
                "data de ateste indisponível" if key == "ateste" else "data de liquidação indisponível",
            )
        )

    start = _date(contract.get("vigencia_inicio"))
    last_closed = date(reference.year, reference.month, 1)
    last_closed = date(last_closed.year - 1, 12, 1) if last_closed.month == 1 else date(last_closed.year, last_closed.month - 1, 1)
    window_start = max(start, date(reference.year - 1, reference.month, 1)) if start else date(reference.year - 1, reference.month, 1)
    missing = 0
    cursor = date(window_start.year, window_start.month, 1)
    while cursor <= last_closed:
        if (cursor.year, cursor.month) not in by_month:
            missing += 1
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    result.append(finding("meses_sem_fatura_12m", Escopo.OPERACAO, missing, component="contratos_comprasnet", path="faturas.dados_referencia", fingerprint=fingerprint))

    raw_commitments = comprasnet.get("empenhos_detalhe")
    all_commitments = [item for item in raw_commitments if isinstance(item, dict)] if isinstance(raw_commitments, list) else []
    dated_commitments = [(item, _date(item.get("data_emissao"))) for item in all_commitments]
    has_commitment_dates = any(issued is not None for _, issued in dated_commitments)
    commitments = (
        [item for item, issued in dated_commitments if issued and issued.year == reference.year]
        if has_commitment_dates else all_commitments
    )
    saldo = sum(
        (_number(item.get("empenhado")) or 0.0)
        - ((_number(item.get("liquidado")) or 0.0) + (_number(item.get("pago")) or 0.0))
        for item in commitments
    )
    covered = saldo / monthly_average if monthly_average else None
    pending_months = sum((reference.year, month) not in by_month for month in range(reference.month, 13))
    unpaid_invoices = sum(
        _number(invoice.get("valor")) or 0.0
        for invoice in invoices
        if (competence := _month(invoice)) is not None
        and competence[0] == reference.year
        and invoice.get("data_liquidacao") in (None, "")
    )
    need = unpaid_invoices + (monthly_average or 0.0) * pending_months
    balance_value = {
        "saldo": round(saldo, 2),
        "necessidade": round(need, 2),
        "meses_cobertos": round(covered, 4) if covered is not None else None,
        "meses_pendentes_ano": pending_months,
        "faturas_nao_liquidadas_valor": round(unpaid_invoices, 2),
    }
    if not has_commitment_dates:
        balance_value["criterio_ano"] = "SEM_DATA_EMISSAO"
    result.append(finding("saldo_empenho_ano", Escopo.OPERACAO, balance_value, component="contratos_comprasnet", path="empenhos_detalhe", fingerprint=fingerprint))
    if monthly_average and saldo < need:
        uncovered = need - saldo
        result.append(finding("empenho_insuficiente", Escopo.OPERACAO, {
            "valor_descoberto": round(uncovered, 2), "meses_descobertos": round(uncovered / monthly_average, 4),
            "saldo": round(saldo, 2), "necessidade": round(need, 2),
            "meses_pendentes_ano": pending_months,
            "faturas_nao_liquidadas_valor": round(unpaid_invoices, 2),
        }, component="contratos_comprasnet", path="empenhos_detalhe", fingerprint=fingerprint))

    memory = operation.get("capacidade_memoria") if isinstance(operation.get("capacidade_memoria"), dict) else {}
    factor = _number(memory.get("fator_liquido"))
    installment = _installment(operation)
    coverage = monthly_average * factor / installment if monthly_average and factor is not None and installment else None
    result.append(finding("cobertura_parcela", Escopo.OPERACAO, round(coverage, 6), component="contratos_comprasnet", path="faturas,capacidade_memoria,taxa_breakdown", fingerprint=fingerprint) if coverage is not None else _unverified("cobertura_parcela", "faturas,capacidade_memoria,taxa_breakdown", fingerprint, "parcela da operação indisponível" if installment is None else "fator líquido indisponível"))

    end, global_value = _date(contract.get("vigencia_fim")), _number(contract.get("valor_global"))
    if start and end and global_value and end > start:
        elapsed = min(_months_between(start, reference), _months_between(start, end))
        expected = global_value * elapsed / _months_between(start, end)
        execution = gross_total / expected if expected else None
        result.append(finding("execucao_vs_contratado", Escopo.OPERACAO, round(execution or 0.0, 6), component="contratos_comprasnet", path="faturas,contrato_comprasnet", fingerprint=fingerprint))
    else:
        result.append(_unverified("execucao_vs_contratado", "faturas,contrato_comprasnet", fingerprint, "dados do contrato indisponíveis"))
    result.extend(_payment_findings(comprasnet, snapshots.get("pagamentos_portal"), reference, fingerprint))
    return result


_CASH_CODES = (
    "mensalidade_faturada_media", "meses_sem_fatura_12m", "glosa_pct_12m",
    "prazo_ateste_dias", "prazo_liquidacao_dias", "saldo_empenho_ano",
    "empenho_insuficiente", "cobertura_parcela", "execucao_vs_contratado",
    "prazo_pagamento_dias", "ciclo_caixa_dias", "faturas_liquidadas_sem_pagamento",
    "taxa_conciliacao_pagamentos", "pago_portal_vs_comprasnet", "antecipacao_concorrente",
)

_PAYMENT_CODES = frozenset({
    "prazo_pagamento_dias", "ciclo_caixa_dias", "faturas_liquidadas_sem_pagamento",
    "taxa_conciliacao_pagamentos", "pago_portal_vs_comprasnet", "antecipacao_concorrente",
})
