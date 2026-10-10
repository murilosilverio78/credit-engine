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


def calcular_capacidade_contrato(
    valor_global: Any,
    vigencia_inicio: Any,
    vigencia_fim: Any,
    dedicacao_exclusiva: Any,
    parametros: dict[str, Any],
    data_referencia: date | None = None,
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
    monthly_gross = value / months
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
        "fator_liquido": factor,
        "mensal_liquido": round(monthly_net, 2),
        "horizonte_firme": horizon.isoformat(),
        "n": installments,
        "parcela_max": round(installment_max, 2),
        "i": rate,
        "data_referencia": reference.isoformat(),
    }
    return {"capacidade": round(capacity, 2), "memoria": memory}
