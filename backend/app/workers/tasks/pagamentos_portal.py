"""Fetch effective payment orders for a verified Comprasnet contract."""

from __future__ import annotations

import time
import threading
from datetime import date
from typing import Any

import httpx
import structlog

from app.integrations.comprasnet.parsing import number_tokens, parse_data, parse_number
from app.workers.base import BaseComponentTask
from app.workers.base import _execute_snapshot_write as _execute_with_retry
from app.workers.http_utils import fetch_json_with_retry, portal_api_url, portal_headers


MAX_PAGES = 300
FALLBACK_MAX_PAGES = 20
MAX_SECONDS = 120
PARTIAL_RETRY_DELAY_SECONDS = 5 * 60

logger = structlog.get_logger()
_PARTIAL_RETRY_LOCK = threading.Lock()
_PARTIAL_RETRY_OPERATIONS: set[str] = set()


def _digits(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _contract_token(value: Any) -> str:
    return max(number_tokens(value), key=len, default="")


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


def _pages(
    client: httpx.Client,
    cnpj: str,
    year: int,
    uasg: str | None,
    *,
    deadline: float,
    max_pages: int = MAX_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page = 1
    partial = False
    while True:
        if time.monotonic() >= deadline:
            partial = True
            break
        if page > max_pages:
            break
        query = f"?codigoPessoa={cnpj}&fase=3&ano={year}&pagina={page}"
        if uasg:
            # The documented filter for documentos-por-favorecido is ``ug``.
            query += f"&ug={uasg}"
        try:
            payload = fetch_json_with_retry(
                client,
                portal_api_url("/despesas/documentos-por-favorecido") + query,
                headers=portal_headers(),
                total_timeout_seconds=deadline - time.monotonic(),
            )
        except httpx.TimeoutException:
            if time.monotonic() >= deadline:
                partial = True
                break
            raise
        if not payload:
            break
        if not isinstance(payload, list):
            raise ValueError("Resposta inesperada do Portal da Transparência")
        records.extend(item for item in payload if isinstance(item, dict))
        page += 1
    return records, {
        "paginas_lidas": page - 1,
        "registros": len(records),
        "atingiu_cap": page > max_pages,
        "parcial": partial,
    }


def _order(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "documento": payload.get("documento") or payload.get("numeroDocumento") or payload.get("codigoDocumento"),
        "data": (parse_data(payload.get("data")) or parse_data(payload.get("dataDocumento")) or parse_data(payload.get("dataPagamento"))).isoformat() if (parse_data(payload.get("data")) or parse_data(payload.get("dataDocumento")) or parse_data(payload.get("dataPagamento"))) else None,
        "valor": parse_number(payload.get("valor")) or parse_number(payload.get("valorDocumento")) or 0.0,
        "codigoUg": payload.get("codigoUg") or payload.get("codigoUnidadeGestora"),
        "ug": payload.get("ug") or payload.get("unidadeGestora"),
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


def _fetch(
    cnpj: str, *, operation_id: str | None = None, today: date | None = None,
    retry_attempt: bool = False,
) -> dict[str, Any]:
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
            deadline = time.monotonic() + MAX_SECONDS
            ug_batches = []
            for year in years:
                batch = _pages(client, cnpj or operation.get("cnpj") or "", year, uasg, deadline=deadline)
                ug_batches.append(batch)
                if batch[1].get("parcial"):
                    break
            records = [item for batch, _ in ug_batches for item in batch]
            pagination = {str(year): page for year, (_, page) in zip(years, ug_batches)}
            # ``parcial`` describes only the authoritative UG listing.  A
            # page cap means that listing did not finish and therefore must
            # not be upgraded by the complementary observation fallback.
            partial = any(
                page.get("parcial") or page.get("atingiu_cap")
                for _, page in ug_batches
            )
            filter_used = "UASG"
            fallback_observacao = None
            if not records and not partial:
                fallback_batches = []
                for year in years:
                    batch = _pages(
                        client,
                        cnpj or operation.get("cnpj") or "",
                        year,
                        None,
                        deadline=deadline,
                        max_pages=FALLBACK_MAX_PAGES,
                    )
                    fallback_batches.append(batch)
                    if batch[1].get("parcial"):
                        break
                fallback_records = [item for batch, _ in fallback_batches for item in batch]
                matched_records = [
                    item for item in fallback_records
                    if number in number_tokens(item.get("observacao") or item.get("observacaoDocumento"))
                ]
                if matched_records:
                    records = matched_records
                    filter_used = "UASG+OBSERVACAO"
                incomplete = any(
                    page.get("parcial") or page.get("atingiu_cap")
                    for _, page in fallback_batches
                )
                if incomplete:
                    fallback_observacao = {
                        "status": "INCOMPLETO",
                        "paginas": sum(
                            int(page.get("paginas_lidas") or 0)
                            for _, page in fallback_batches
                        ),
                    }
        pagination["parcial"] = partial
        result = {"status_consulta": "ENCONTRADO", "status_atualizacao": "ATUALIZADO", "ordens_bancarias": [_order(item) for item in records], "filtro_usado": filter_used, "anos": years, "paginacao": pagination}
        if fallback_observacao:
            result["fallback_observacao"] = fallback_observacao
        if partial and not retry_attempt:
            _schedule_partial_retry(operation_id)
        return result
    except Exception as exc:
        if previous.get("ordens_bancarias"):
            return _maintain(previous, exc)
        raise


_task = BaseComponentTask()


def _run_partial_retry(operation_id: str) -> None:
    try:
        _ensure_snapshot(operation_id)
        _task.execute(
            operation_id,
            component="pagamentos_portal",
            handler=lambda cnpj, operation_id=None: _fetch(
                cnpj, operation_id=operation_id, retry_attempt=True,
            ),
            use_cache=False,
        )
    except Exception as exc:
        logger.warning(
            "pagamentos_portal.partial_retry_failed",
            operation_id=operation_id,
            error=str(exc),
        )
    finally:
        with _PARTIAL_RETRY_LOCK:
            _PARTIAL_RETRY_OPERATIONS.discard(operation_id)


def _schedule_partial_retry(operation_id: str) -> None:
    """Schedule exactly one non-blocking retry for a partial Portal listing."""
    if not operation_id:
        return
    with _PARTIAL_RETRY_LOCK:
        if operation_id in _PARTIAL_RETRY_OPERATIONS:
            return
        _PARTIAL_RETRY_OPERATIONS.add(operation_id)
    timer = threading.Timer(PARTIAL_RETRY_DELAY_SECONDS, _run_partial_retry, args=(operation_id,))
    timer.daemon = True
    timer.start()
    logger.warning(
        "pagamentos_portal.partial_retry_scheduled",
        operation_id=operation_id,
        delay_seconds=PARTIAL_RETRY_DELAY_SECONDS,
    )


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
