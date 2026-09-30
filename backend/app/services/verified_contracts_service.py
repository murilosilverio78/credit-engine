"""Contrato verificado no Comprasnet combinado ao piso do Portal.

O snapshot do Portal jamais e alterado: esta funcao retorna uma visao derivada
para consumo pelo detalhe, PDF e pela copia em memoria do score.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _number(value: Any) -> float:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text or 0)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _normalized_number(value: Any) -> str:
    digits = _digits(value)
    return digits.zfill(9) if 0 < len(digits) <= 9 else digits


def _supplier_cnpj(contract: dict[str, Any]) -> str:
    supplier = contract.get("fornecedor")
    if isinstance(supplier, dict):
        nested = supplier.get("cnpj") or supplier.get("cnpj_cpf_idgener")
        if nested:
            return _digits(nested)
    return _digits(
        contract.get("fornecedor_cnpj")
        or contract.get("fornecedor_cnpj_cpf_idgener")
        or contract.get("cnpj_fornecedor")
    )


def _portal_contracts(result: dict[str, Any]) -> list[dict[str, Any]]:
    details = result.get("contratos_detalhe")
    return [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []


def _is_same_contract(portal: dict[str, Any], additional: dict[str, Any], cnpj: str) -> bool:
    portal_supplier = _supplier_cnpj(portal)
    if portal_supplier and portal_supplier != cnpj:
        return False

    portal_number = _normalized_number(portal.get("numero"))
    portal_uasg = _digits(portal.get("unidade_codigo") or portal.get("uasg"))
    if portal_number and portal_uasg and (portal_number, portal_uasg) == (
        _normalized_number(additional.get("numero")),
        _digits(additional.get("uasg")),
    ):
        return True

    return (
        _number(portal.get("valor_global") or portal.get("valor_final") or portal.get("valor_inicial"))
        == _number(additional.get("valor_global"))
        and _date(portal.get("data_inicio") or portal.get("inicio_vigencia"))
        == _date(additional.get("vigencia_inicio"))
    )


def contratos_verificados(
    portal_result: dict[str, Any],
    comprasnet_result: dict[str, Any],
    cnpj_operacao: str,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Combina contratos ativos do Portal com um adicional Comprasnet elegivel."""
    today = today or date.today()
    portal = _portal_contracts(portal_result)
    cnpj = _digits(cnpj_operacao)
    contract = comprasnet_result.get("contrato_comprasnet")
    contract = contract if isinstance(contract, dict) else {}
    additional: list[dict[str, Any]] = []
    supplier_cnpj = _supplier_cnpj(contract)
    cnpj_reconferido = bool(supplier_cnpj)
    supplier_matches = (
        supplier_cnpj == cnpj
        if cnpj_reconferido
        else contract.get("match_confianca") == "CNPJ_CONFERIDO"
    )

    eligible = (
        comprasnet_result.get("status_consulta") == "ENCONTRADO"
        and contract.get("match_confianca") == "CNPJ_CONFERIDO"
        and supplier_matches
        and str(contract.get("situacao") or "").strip().casefold() == "ativo"
        and (_date(contract.get("vigencia_fim")) or date.min) >= today
    )
    if eligible and not any(_is_same_contract(item, contract, cnpj) for item in portal):
        additional.append(
            {
                "numero": contract.get("numero"),
                "uasg": contract.get("uasg"),
                "valor_global": _number(contract.get("valor_global")),
                "vigencia_inicio": contract.get("vigencia_inicio"),
                "vigencia_fim": contract.get("vigencia_fim"),
                "orgao": contract.get("orgao"),
                "origem": "Comprasnet",
                "cnpj_reconferido": cnpj_reconferido,
            }
        )

    portal_active = int(portal_result.get("contratos_ativos") or 0)
    portal_total = int(portal_result.get("total_contratos") or len(portal))
    portal_value = _number(portal_result.get("valor_total_ativo"))
    combined_contracts = [{**item, "origem": "Portal"} for item in portal] + [
        {
            **item,
            "ativo": True,
            "data_inicio": item.get("vigencia_inicio"),
            "data_fim": item.get("vigencia_fim"),
            "valor_final": item.get("valor_global"),
        }
        for item in additional
    ]
    agencies = {
        str(item) for item in (portal_result.get("orgaos_contratantes") or []) if item
    }
    agencies.update(str(item["orgao"]) for item in additional if item.get("orgao"))
    return {
        "adicionais": additional,
        "contratos": combined_contracts,
        "contratos_ativos_verificados": portal_active + len(additional),
        "valor_total_ativo_verificado": portal_value + sum(
            item["valor_global"] for item in additional
        ),
        "total_contratos_verificados": portal_total + len(additional),
        "contratos_encerrados_verificados": int(portal_result.get("contratos_encerrados") or 0),
        "orgaos_contratantes_verificados": sorted(agencies),
        "nota": (
            "inclui 1 contrato verificado no Comprasnet, não listado no Portal"
            if len(additional) == 1
            else f"inclui {len(additional)} contratos verificados no Comprasnet, não listados no Portal"
            if additional
            else None
        ),
    }
