"""Capacidade financeira do fluxo futuro do contrato cedido."""

from __future__ import annotations

import math
from calendar import monthrange
from datetime import date, datetime
from typing import Any


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            value = float(text.replace(".", "").replace(",", "."))
        except ValueError:
            return None
    return value if math.isfinite(value) else None


def _add_years(value: date, years: int) -> date:
    year = value.year + years
    return date(year, value.month, min(value.day, monthrange(year, value.month)[1]))


def _rounded_months(start: date, end: date) -> int:
    """Months of contract life, rounded to the closest whole monthly cycle."""
    return max(round((end - start).days / 30.4375), 1)


def _next_anniversary(start: date, reference: date) -> date:
    anniversary = _add_years(start, max(reference.year - start.year, 0))
    if anniversary <= reference:
        anniversary = _add_years(anniversary, 1)
    return anniversary


def _subtract_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _cancelled(invoice: dict[str, Any]) -> bool:
    value = invoice.get("nota_cancelada")
    if value is True or str(value).strip().lower() in {"true", "sim", "s", "1"}:
        return True
    return "cancel" in str(invoice.get("situacao") or "").lower()


def _integer_parameter(parametros: dict[str, Any], key: str, default: int) -> int:
    value = _number(parametros.get(key, default))
    return default if value is None else int(value)


def _money(value: Any) -> str:
    amount = _number(value)
    amount = 0.0 if amount is None else amount
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _whole(value: Any) -> str:
    amount = _number(value)
    return str(int(amount)) if amount is not None else "—"


def explicar_capacidade(
    memoria: Any,
    parametros_opcionais: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Translate persisted capacity memory into an analyst-facing explanation."""
    if not isinstance(memoria, dict):
        return None
    fonte = memoria.get("fonte_mensal")
    if fonte in (None, "", "NOMINAL"):
        return None

    params = parametros_opcionais if isinstance(parametros_opcionais, dict) else {}
    if fonte == "SEM_EXECUCAO":
        minimo = _number(params.get("cap_sob_demanda_min_faturas"))
        minimo_texto = (
            f" (mínimo exigido: {int(minimo)})" if minimo is not None else ""
        )
        return {
            "codigo": "SEM_EXECUCAO",
            "titulo": "Contrato sob demanda sem execução comprovada",
            "detalhe": (
                f"{_whole(memoria.get('faturas_na_janela'))} fatura(s) nos últimos "
                f"{_whole(memoria.get('janela_meses'))} mês(es){minimo_texto}. "
                "O valor global é um teto de compras, não uma receita garantida; "
                "por isso a capacidade considerada é R$ 0,00. Valor global ÷ meses "
                f"seria {_money(memoria.get('mensal_nominal'))}/mês."
            ),
            "nivel": "ALERTA",
        }
    if fonte == "EXECUCAO_REAL":
        executado = _number(memoria.get("mensal_execucao"))
        nominal = _number(memoria.get("mensal_nominal"))
        usados = [value for value in (executado, nominal) if value is not None]
        menor = min(usados) if usados else _number(memoria.get("mensal_bruto"))
        return {
            "codigo": "EXECUCAO_REAL",
            "titulo": "Capacidade limitada pela execução real do contrato",
            "detalhe": (
                f"Média mensal executada de {_money(executado)} nos últimos "
                f"{_whole(memoria.get('janela_meses'))} mês(es) versus nominal de "
                f"{_money(nominal)}; foi usada a menor das duas ({_money(menor)}/mês)."
            ),
            "nivel": "INFO",
        }
    if fonte == "NOMINAL_EXECUCAO_NAO_VERIFICADA":
        return {
            "codigo": "NOMINAL_EXECUCAO_NAO_VERIFICADA",
            "titulo": "Execução do contrato não verificada",
            "detalhe": (
                "A leitura das faturas no Comprasnet não foi confirmada; usado valor "
                "global ÷ meses. Reverifique antes de decidir."
            ),
            "nivel": "ALERTA",
        }
    return None


def _monthly_execution(
    execution: dict[str, Any], start: date, reference: date, parametros: dict[str, Any],
) -> tuple[str, float | None, int, int]:
    """Return source, monthly execution, invoice count and effective window."""
    configured_window = max(
        _integer_parameter(parametros, "cap_sob_demanda_janela_meses", 6), 1
    )
    window_months = min(configured_window, _rounded_months(start, reference))
    minimum_invoices = max(
        _integer_parameter(parametros, "cap_sob_demanda_min_faturas", 2), 0
    )
    if minimum_invoices == 0:
        return "NOMINAL", None, 0, window_months
    if execution.get("leitura_faturas_verificada") is not True:
        return "NOMINAL_EXECUCAO_NAO_VERIFICADA", None, 0, window_months

    window_start = max(start, _subtract_months(reference, window_months))
    invoices = [
        invoice for invoice in execution.get("faturas") or []
        if isinstance(invoice, dict)
        and not _cancelled(invoice)
        and (issued := _date(invoice.get("emissao"))) is not None
        and window_start <= issued <= reference
    ]
    execution_value = sum(
        (_number(invoice.get("valorliquido")) or 0.0)
        if invoice.get("valorliquido") not in (None, "")
        else (_number(invoice.get("valor")) or 0.0) - (_number(invoice.get("glosa")) or 0.0)
        for invoice in invoices
    )
    monthly = execution_value / window_months
    if len(invoices) < minimum_invoices:
        return "SEM_EXECUCAO", monthly, len(invoices), window_months
    return "EXECUCAO_REAL", monthly, len(invoices), window_months


def calcular_capacidade_contrato(
    valor_global: Any,
    vigencia_inicio: Any,
    vigencia_fim: Any,
    dedicacao_exclusiva: Any,
    parametros: dict[str, Any],
    data_referencia: date | None = None,
    execucao: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calcula o PV das parcelas líquidas no horizonte firme do contrato.

    Raises ``ValueError`` when the ceded contract cannot support a meaningful
    calculation.  Callers map that condition to the technical funnel reason.
    """
    value = _number(valor_global)
    start, end = _date(vigencia_inicio), _date(vigencia_fim)
    if value is None or value <= 0 or not start or not end or end <= start:
        raise ValueError("contrato cedido sem valor ou vigencia valida")

    reference = data_referencia or date.today()
    factor_key = (
        "cap_fator_liquido_mao_obra"
        if bool(dedicacao_exclusiva)
        else "cap_fator_liquido_demais"
    )
    try:
        factor = float(parametros[factor_key])
        coverage = float(parametros["cap_cobertura_parcela"])
        rate = float(parametros["cap_taxa_referencia_am"])
        slack = int(float(parametros["cap_folga_meses"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("parametros de capacidade invalidos") from exc
    if factor <= 0 or coverage <= 0 or rate < 0 or slack < 0:
        raise ValueError("parametros de capacidade invalidos")

    months = _rounded_months(start, end)
    monthly_nominal = value / months
    monthly_gross = monthly_nominal
    source = "NOMINAL"
    monthly_execution: float | None = None
    invoices_in_window = 0
    window_months: int | None = None
    execution = execucao if isinstance(execucao, dict) else {}
    if str(execution.get("modalidade") or "").strip().upper() == "SOB_DEMANDA":
        source, monthly_execution, invoices_in_window, window_months = _monthly_execution(
            execution, start, reference, parametros,
        )
        if source == "EXECUCAO_REAL":
            monthly_gross = min(monthly_nominal, monthly_execution or 0.0)
        elif source == "SEM_EXECUCAO":
            monthly_gross = 0.0
    monthly_net = monthly_gross * factor
    horizon = min(end, _next_anniversary(start, reference))

    # A primeira parcela disponível é a do mês seguinte.  O mês parcial da
    # referência já é a folga operacional implícita; ``slack=1`` mantém os
    # nove fluxos dos casos aprovados de outubro/2026.
    months_until_horizon = (horizon.year - reference.year) * 12 + horizon.month - reference.month
    installments = max(months_until_horizon - max(slack - 1, 0), 0)
    installment_max = monthly_net / coverage
    if installments <= 0:
        capacity = 0.0
    elif rate == 0:
        capacity = installment_max * installments
    else:
        capacity = installment_max * (1 - (1 + rate) ** (-installments)) / rate

    memory = {
        "valor_global": round(value, 2),
        "meses_vigencia": months,
        "mensal_bruto": round(monthly_gross, 2),
        "fonte_mensal": source,
        "mensal_nominal": round(monthly_nominal, 2),
        "mensal_execucao": round(monthly_execution, 2) if monthly_execution is not None else None,
        "faturas_na_janela": invoices_in_window,
        "janela_meses": window_months,
        "fator_liquido": factor,
        "mensal_liquido": round(monthly_net, 2),
        "horizonte_firme": horizon.isoformat(),
        "n": installments,
        "parcela_max": round(installment_max, 2),
        "i": rate,
        "data_referencia": reference.isoformat(),
    }
    return {"capacidade": round(capacity, 2), "memoria": memory}
