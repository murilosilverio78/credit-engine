"""Carteira pública do cedente no PNCP e identificação do contrato cedido."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.workers.base import BaseComponentTask

_task = BaseComponentTask()
RETRIES = (2, 6)
DEDICACAO = re.compile(
    r"dedicacao exclusiva de mao de obra|cessao de mao de obra|regime de dedicacao exclusiva"
)


def _plain(value: Any) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(c)
    ).lower()


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    if isinstance(value, str):
        value = value.strip().replace("R$", "").replace(" ", "")
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    objeto = item.get("description") or item.get("objeto") or ""
    return {
        "orgao_cnpj": item.get("orgao_cnpj"),
        "orgao_nome": item.get("orgao_nome"),
        "unidade_codigo": str(item.get("unidade_codigo") or "") or None,
        "unidade_nome": item.get("unidade_nome"),
        "esfera_id": item.get("esfera_id"),
        "uf": item.get("uf"),
        "numero_contrato_empenho": item.get("numero_contrato_empenho"),
        "ano": item.get("ano"),
        "numero_controle_pncp": item.get("numero_controle_pncp"),
        "objeto": objeto,
        "valor_global": _number(item.get("valor_global")),
        "data_assinatura": item.get("data_assinatura"),
        "data_inicio_vigencia": item.get("data_inicio_vigencia"),
        "data_fim_vigencia": item.get("data_fim_vigencia"),
        "tipo_contrato_nome": item.get("tipo_contrato_nome"),
        "modalidade_licitacao_nome": item.get("modalidade_licitacao_nome"),
        "dedicacao_exclusiva": bool(DEDICACAO.search(_plain(objeto))),
    }


def _operation_contract_key(value: Any) -> tuple[int, int] | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 5:
        return None
    return int(digits[:-4] or 0), int(digits[-4:])


def _pncp_contract_key(value: Any, year: Any) -> tuple[int, int] | None:
    digits = re.sub(r"\D", "", str(value or ""))
    try:
        return int(digits), int(year)
    except (TypeError, ValueError):
        return None


def _cedido(
    contracts: list[dict[str, Any]], number: Any, reference: Any
) -> tuple[dict[str, Any] | None, str]:
    numbers = number if isinstance(number, list) else [number]
    keys = {_operation_contract_key(value) for value in numbers}
    keys.discard(None)
    matches = [
        contract
        for contract in contracts
        if _pncp_contract_key(
            contract.get("numero_contrato_empenho"), contract.get("ano")
        )
        in keys
    ]
    if not matches:
        return None, "NAO_ENCONTRADO"
    target = _number(reference)
    selected = min(matches, key=lambda c: abs(_number(c.get("valor_global")) - target))
    return selected, "EXATO" if len(matches) == 1 else "APROXIMADO"


def _operation_contract_numbers(operation: dict[str, Any]) -> list[str]:
    numbers = [str(operation.get("contrato_id") or "")]
    cotacao_id = operation.get("cotacao_id")
    if not cotacao_id:
        return [number for number in numbers if number]

    try:
        from app.integrations.broadfactor.client import BroadfactorClient

        for contract in BroadfactorClient().contratos_da_cotacao(str(cotacao_id)):
            number = str(getattr(contract, "numero_contrato", "") or "")
            if number and number not in numbers:
                numbers.append(number)
    except Exception:
        # A carteira PNCP continua válida mesmo que o número da cotação não
        # possa ser relido; nesse caso preserva a chave existente da operação.
        pass
    return [number for number in numbers if number]


def _aggregate(contracts: list[dict[str, Any]], today: date) -> dict[str, Any]:
    active = [
        c for c in contracts if (_date(c["data_fim_vigencia"]) or date.min) >= today
    ]
    annual = []
    for contract in active:
        start, end = (
            _date(contract["data_inicio_vigencia"]),
            _date(contract["data_fim_vigencia"]),
        )
        years = max(((end - start).days / 365.25) if start and end else 1 / 12, 1 / 12)
        annual.append((contract, _number(contract["valor_global"]) / years))
    total = sum(value for _, value in annual)
    by_org: dict[str, float] = {}
    for contract, value in annual:
        orgao = str(contract.get("orgao_cnpj") or "")
        if orgao:
            by_org[orgao] = by_org.get(orgao, 0.0) + value
    shares = [value / total for value in by_org.values()] if total else []
    cutoff = today - timedelta(days=365)
    recent = [
        c for c in contracts if (_date(c["data_assinatura"]) or date.min) >= cutoff
    ]
    return {
        "n_contratos": len(contracts),
        "n_vigentes": len(active),
        "valor_global_vigente": round(
            sum(_number(c["valor_global"]) for c in active), 2
        ),
        "valor_anualizado_vigente": round(total, 2),
        "n_orgaos": len(
            {c.get("orgao_cnpj") for c in contracts if c.get("orgao_cnpj")}
        ),
        "esferas": sorted(
            {c.get("esfera_id") for c in contracts if c.get("esfera_id")}
        ),
        "hhi": round(sum(share * share for share in shares) * 10000, 2),
        "n_assinados_12m": len(recent),
        "valor_assinado_12m": round(sum(_number(c["valor_global"]) for c in recent), 2),
        "data_primeiro_contrato": min(
            (c.get("data_assinatura") for c in contracts if c.get("data_assinatura")),
            default=None,
        ),
    }


def _page(client: httpx.Client, base: str, cnpj: str, page: int) -> dict[str, Any]:
    for attempt in range(len(RETRIES) + 1):
        try:
            response = client.get(
                base,
                params={
                    "q": cnpj,
                    "tipos_documento": "contrato",
                    "ordenacao": "-data",
                    "pagina": page,
                    "tam_pagina": 20,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("resposta PNCP nao e objeto")
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt == len(RETRIES):
                raise
            time.sleep(RETRIES[attempt])
        except httpx.TimeoutException:
            if attempt == len(RETRIES):
                raise
            time.sleep(RETRIES[attempt])
    raise RuntimeError("tentativas PNCP esgotadas")


def _fetch(cnpj: str) -> dict[str, Any]:
    cnpj = re.sub(r"\D", "", cnpj)
    base = settings.PNCP_SEARCH_BASE_URL.rstrip("/") + "/api/search/"
    items: list[dict[str, Any]] = []
    page = 1
    total = 0
    with httpx.Client(timeout=20, verify=settings.HTTPX_VERIFY_SSL) as client:
        while True:
            payload = _page(client, base, cnpj, page)
            batch = payload.get("items") or []
            total = int(payload.get("total") or len(batch))
            items.extend(
                item
                for item in batch
                if str(item.get("fornecedor_ni") or "") == cnpj
                and item.get("cancelado") is False
            )
            if page * 20 >= total or not batch:
                break
            page += 1
            time.sleep(1)
    contracts = [_normalize(item) for item in items]
    today = date.today()
    result = {
        **_aggregate(contracts, today),
        "contratos_detalhe": contracts,
        "fonte": "PNCP",
        "consultado_em": datetime.now(timezone.utc).isoformat(),
    }
    return result


def _associar_contrato_cedido(operation_id: str) -> dict[str, Any]:
    """Associa uma carteira em cache à operação que efetivamente a solicitou."""
    from app.core.database import supabase

    snapshot = (
        supabase.table("component_snapshots")
        .select("parsed_result")
        .eq("operation_id", operation_id)
        .eq("component", "contratos_pncp")
        .maybe_single()
        .execute()
        .data
        or {}
    )
    carteira = snapshot.get("parsed_result") or {}
    operation = (
        supabase.table("operations")
        .select("cotacao_id,contrato_id,valor_global_contrato,saldo_vincendo")
        .eq("id", operation_id)
        .maybe_single()
        .execute()
        .data
        or {}
    )
    cedido, match = _cedido(
        list(carteira.get("contratos_detalhe") or []),
        _operation_contract_numbers(operation),
        operation.get("valor_global_contrato") or operation.get("saldo_vincendo"),
    )
    parsed_result = {
        **carteira,
        "contrato_cedido": cedido,
        "contrato_cedido_match": match,
    }
    supabase.table("component_snapshots").update({"parsed_result": parsed_result}).eq(
        "operation_id", operation_id
    ).eq("component", "contratos_pncp").execute()

    if cedido:
        update = {
            "uasg": cedido.get("unidade_codigo"),
            "contrato_vigencia_inicio": cedido.get("data_inicio_vigencia"),
            "contrato_vigencia_fim": cedido.get("data_fim_vigencia"),
            "contrato_dedicacao_exclusiva": cedido.get("dedicacao_exclusiva"),
            "contrato_pncp_controle": cedido.get("numero_controle_pncp"),
        }
        if not operation.get("valor_global_contrato"):
            update["valor_global_contrato"] = cedido.get("valor_global")
        supabase.table("operations").update(update).eq("id", operation_id).execute()
    return parsed_result


def run_contratos_pncp(operation_id: str, *, use_cache: bool = True):
    result = _task.execute(
        operation_id,
        component="contratos_pncp",
        handler=_fetch,
        use_cache=use_cache,
    )
    _associar_contrato_cedido(operation_id)
    return result
