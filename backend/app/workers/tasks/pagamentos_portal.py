"""Fetch effective payment orders for a verified Comprasnet contract."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx

from app.integrations.comprasnet.parsing import parse_data, parse_number
from app.workers.base import BaseComponentTask
from app.workers.base import _execute_snapshot_write as _execute_with_retry
from app.workers.http_utils import fetch_json_with_retry, portal_api_url, portal_headers


MAX_PAGES = 300
MAX_SECONDS = 180


def _digits(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _contract_token(value: Any) -> str:
    return _digits(value).lstrip("0")


def _db(operation_id: str, action: str, request):
    return _execute_with_retry(operation_id, "pagamentos_portal", action, request)


def _context(operation_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from app.core.database import supabase

    operation = _db(
        operation_id, "load_payment_operation",
        lambda: supabase.table("operations").select("cnpj").eq("id", operation_id).single().execute(),
    ).data or {}
    snapshots = _db(
        operation_id, "load_payment_snapshots",
        lambda: supabase.table("component_snapshots").select("component,parsed_result")
        .eq("operation_id", operation_id).in_("component", ["contratos_comprasnet", "pagamentos_portal"]).execute(),
    ).data or []
    by_component = {
        row.get("component"): row.get("parsed_result") or {}
        for row in snapshots if isinstance(row, dict)
    }
    return operation, by_component.get("contratos_comprasnet", {}), by_component.get("pagamentos_portal", {})


def _pages(client: httpx.Client, cnpj: str, year: int, uasg: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    started = time.monotonic()
    page = 1
    while True:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError(f"pagamentos_portal excedeu {MAX_SECONDS}s na pagina {page}")
        if page > MAX_PAGES:
            break
        query = f"?codigoPessoa={cnpj}&fase=3&ano={year}&pagina={page}"
        if uasg:
            query += f"&unidadeGestora={uasg}"
        payload = fetch_json_with_retry(
            client,
            portal_api_url("/despesas/documentos-por-favorecido") + query,
            headers=portal_headers(),
        )
        if not payload:
            break
        if not isinstance(payload, list):
            raise ValueError("Resposta inesperada do Portal da Transparência")
        records.extend(item for item in payload if isinstance(item, dict))
        page += 1
    return records, {"paginas_lidas": page - 1, "registros": len(records), "atingiu_cap": page > MAX_PAGES}


def _order(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "documento": payload.get("documento") or payload.get("numeroDocumento") or payload.get("codigoDocumento"),
        "data": (parse_data(payload.get("data")) or parse_data(payload.get("dataDocumento")) or parse_data(payload.get("dataPagamento"))).isoformat() if (parse_data(payload.get("data")) or parse_data(payload.get("dataDocumento")) or parse_data(payload.get("dataPagamento"))) else None,
        "valor": parse_number(payload.get("valor")) or parse_number(payload.get("valorDocumento")) or 0.0,
        "ug": payload.get("unidadeGestora") or payload.get("codigoUnidadeGestora"),
        "observacao": payload.get("observacao") or payload.get("observacaoDocumento") or "",
    }


def _maintain(previous: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        **previous,
        "status_consulta": "ENCONTRADO",
        "status_atualizacao": "MANTIDO",
        "erros_enriquecimento": [
            *(previous.get("erros_enriquecimento") or []),
            {"endpoint": "documentos-por-favorecido", "erro": str(error)[:300]},
        ],
    }


def _fetch(cnpj: str, *, operation_id: str | None = None, today: date | None = None) -> dict[str, Any]:
    if not operation_id:
        raise ValueError("operation_id_ausente")
    today = today or date.today()
    operation, comprasnet, previous = _context(operation_id)
    if comprasnet.get("status_consulta") != "ENCONTRADO":
        return {"status_consulta": "NAO_APLICAVEL", "motivo": "contrato_comprasnet_nao_encontrado", "ordens_bancarias": [], "anos": [today.year, today.year - 1], "paginacao": {}}
    contract = comprasnet.get("contrato_comprasnet") or {}
    uasg, number = _digits(contract.get("uasg")), _contract_token(contract.get("numero"))
    if len(uasg) != 6 or not number:
        return {"status_consulta": "NAO_APLICAVEL", "motivo": "identificacao_contrato_indisponivel", "ordens_bancarias": [], "anos": [today.year, today.year - 1], "paginacao": {}}
    years = [today.year, today.year - 1]
    try:
        from app.core.config import settings
        with httpx.Client(timeout=20, verify=settings.HTTPX_VERIFY_SSL) as client:
            batches = [_pages(client, cnpj or operation.get("cnpj") or "", year, uasg) for year in years]
            records = [item for batch, _ in batches for item in batch]
            pagination = {str(year): page for year, (_, page) in zip(years, batches)}
            filter_used = "UASG"
            if not records:
                batches = [_pages(client, cnpj or operation.get("cnpj") or "", year, None) for year in years]
                records = [item for batch, _ in batches for item in batch]
                records = [item for item in records if number in _contract_token(item.get("observacao") or item.get("observacaoDocumento"))]
                pagination = {str(year): page for year, (_, page) in zip(years, batches)}
                filter_used = "OBSERVACAO"
        return {"status_consulta": "ENCONTRADO", "status_atualizacao": "ATUALIZADO", "ordens_bancarias": [_order(item) for item in records], "filtro_usado": filter_used, "anos": years, "paginacao": pagination}
    except Exception as exc:
        if previous.get("ordens_bancarias"):
            return _maintain(previous, exc)
        raise


_task = BaseComponentTask()


def _ensure_snapshot(operation_id: str) -> None:
    from app.core.database import supabase

    supabase.table("component_snapshots").upsert(
        {"operation_id": operation_id, "component": "pagamentos_portal", "status": "pending"},
        on_conflict="operation_id,component",
        ignore_duplicates=True,
    ).execute()


def run_pagamentos_portal(operation_id: str, *, use_cache: bool = False):
    _ensure_snapshot(operation_id)
    return _task.execute(operation_id, component="pagamentos_portal", handler=_fetch, use_cache=use_cache)
