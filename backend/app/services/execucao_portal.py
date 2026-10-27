"""Reconstruct contract execution from complete Portal bank-order listings."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from app.integrations.comprasnet.parsing import parse_data, parse_number


_COMPETENCE = re.compile(r"\bCOMP\.?\s*(\d{2})\s*/\s*(\d{4})\b", re.IGNORECASE)
_FISCAL_DOCUMENT = re.compile(
    r"\b(?:DOC\.?\s*FISCAI\(?S?\)?|NF|NOTA\s+FISCAL)\s*"
    r"(?:N\s*[º°O.]?\s*)?[:#-]?\s*(\d+)\b",
    re.IGNORECASE,
)


def _number(value: Any) -> float | None:
    return parse_number(value)


def _date(value: Any) -> date | None:
    return parse_data(value)


def _competence(order: dict[str, Any]) -> tuple[int, int, bool] | None:
    observation = str(order.get("observacao") or "")
    match = _COMPETENCE.search(observation)
    if match:
        return int(match.group(2)), int(match.group(1)), False
    paid = _date(order.get("data"))
    return (paid.year, paid.month, True) if paid else None


def _fiscal_document(order: dict[str, Any]) -> str | None:
    match = _FISCAL_DOCUMENT.search(str(order.get("observacao") or ""))
    return (match.group(1).lstrip("0") or "0") if match else None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"disponivel": False, "faturas": [], "motivo_indisponivel": reason}


def faturas_equivalentes_portal(
    pagamentos: dict | None, contrato_numero: Any, contrato_uasg: Any,
) -> dict[str, Any]:
    """Return paid, invoice-shaped execution evidence from Portal orders.

    Imports of the established order filters are deliberately local: the cash
    findings adapter also consumes this helper, while its historic private
    imports remain available to callers and tests.
    """
    if not isinstance(pagamentos, dict):
        return _unavailable("pagamentos do Portal indisponíveis")
    if pagamentos.get("status_consulta") != "ENCONTRADO":
        return _unavailable("consulta de pagamentos do Portal indisponível")
    pagination = pagamentos.get("paginacao")
    pagination = pagination if isinstance(pagination, dict) else {}
    if pagination.get("parcial") is True:
        return _unavailable("leitura parcial do Portal")
    if pagamentos.get("atingiu_cap") is True or pagination.get("atingiu_cap") is True:
        return _unavailable("limite de paginação atingido no Portal")

    orders = [item for item in pagamentos.get("ordens_bancarias") or [] if isinstance(item, dict)]
    # Keep the canonical Portal filters in one place.  The tax rows are fetched
    # separately below because `_active_payment_orders` intentionally excludes
    # them from ordinary payment reconciliation.
    from app.services.findings.adapters.fluxo_caixa import (
        _CANCELS_ORDER, _active_payment_orders, _contract_scoped_orders,
        _document_key, _is_deposit_guarantee, _is_tax_or_withholding,
    )

    service_orders = _active_payment_orders(orders, contrato_numero, contrato_uasg)
    cancelled = {
        _document_key(match.group(1))
        for order in orders
        for match in _CANCELS_ORDER.finditer(str(order.get("observacao") or ""))
    }
    withholding_candidates = [
        order for order in orders
        if (_number(order.get("valor")) or 0) > 0
        and _is_tax_or_withholding(order)
        and not _is_deposit_guarantee(order)
        and not _CANCELS_ORDER.search(str(order.get("observacao") or ""))
        and not any(_document_key(order.get("documento")).endswith(item) for item in cancelled if item)
    ]
    withholding_orders = _contract_scoped_orders(
        withholding_candidates, contrato_numero, contrato_uasg,
    )

    groups: dict[tuple[Any, ...], dict[str, Any]] = {}

    def group_for(order: dict[str, Any]) -> dict[str, Any] | None:
        competence = _competence(order)
        if not competence:
            return None
        year, month, inferred = competence
        fiscal_document = _fiscal_document(order)
        paid = _date(order.get("data"))
        key = ("nf", fiscal_document) if fiscal_document else ("fallback", year, month, paid.isoformat() if paid else None)
        if key not in groups:
            groups[key] = {
                "numero": fiscal_document,
                "dados_referencia": {"mesref": month, "anoref": year},
                "competencia_inferida": inferred,
                "emissao": None,
                "emissao_aproximada": paid.isoformat() if paid else None,
                "data_liquidacao": None,
                "data_pagamento": paid.isoformat() if paid else None,
                "origem": "PORTAL_OB",
                "valor": 0.0,
                "valorliquido": 0.0,
                "glosa": None,
                "_service": 0.0,
                "_withholding": 0.0,
            }
        group = groups[key]
        group["competencia_inferida"] = group["competencia_inferida"] or inferred
        if paid and (not group["emissao_aproximada"] or paid.isoformat() > group["emissao_aproximada"]):
            group["emissao_aproximada"] = paid.isoformat()
            group["data_pagamento"] = paid.isoformat()
        return group

    for order in service_orders:
        if group := group_for(order):
            group["_service"] += _number(order.get("valor")) or 0.0
    for order in withholding_orders:
        if group := group_for(order):
            group["_withholding"] += _number(order.get("valor")) or 0.0

    invoices = []
    for group in groups.values():
        if group["_service"] <= 0:
            continue
        group["valorliquido"] = round(group["_service"], 2)
        group["valor"] = round(group["_service"] + group["_withholding"], 2)
        del group["_service"]
        del group["_withholding"]
        invoices.append(group)
    invoices.sort(key=lambda item: (item["dados_referencia"]["anoref"], item["dados_referencia"]["mesref"], str(item.get("numero") or "")))
    return {"disponivel": True, "faturas": invoices, "motivo_indisponivel": None}
