"""Deterministic cash-flow findings from the persisted Comprasnet enrichment."""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from app.integrations.comprasnet.parsing import parse_data, parse_number
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


def _unverified(code: str, path: str, fingerprint: str, motivo: str):
    return finding(
        code, Escopo.OPERACAO, None, component="contratos_comprasnet", path=path,
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
    if not invoices:
        return [_unverified(code, "faturas", fingerprint, "contrato sem faturamento ainda") for code in _CASH_CODES]

    contract = comprasnet.get("contrato_comprasnet") or {}
    reference = _date(operation.get("data_referencia")) or date.today()
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
    return result


_CASH_CODES = (
    "mensalidade_faturada_media", "meses_sem_fatura_12m", "glosa_pct_12m",
    "prazo_ateste_dias", "prazo_liquidacao_dias", "saldo_empenho_ano",
    "empenho_insuficiente", "cobertura_parcela", "execucao_vs_contratado",
)
