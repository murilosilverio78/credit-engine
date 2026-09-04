"""Federal receipts component with Broadfactor/Portal reconciliation."""

from __future__ import annotations

import json
import statistics
import time
from datetime import date
from typing import Any

import httpx
import structlog

from app.integrations.broadfactor.client import (
    BroadfactorClient,
    ConcentracaoSacado,
    Recebimento,
)
from app.workers.base import BaseComponentTask
from app.workers.base import _execute_snapshot_write as _execute_with_retry
from app.workers.http_utils import fetch_json_with_retry


logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"
MAX_PAGES = 300
MAX_SECONDS = 180
BROADFACTOR_PAGE_SIZE = 50
RECONCILIATION_TOLERANCE_PCT = 10.0


def _month_key(value: str | int | None) -> tuple[int, int] | None:
    if value is None:
        return None
    raw = str(value).strip()
    try:
        if len(raw) == 7 and raw[2] == "/":
            month, year = int(raw[:2]), int(raw[3:])
        elif len(raw) == 6 and raw.isdigit():
            year, month = int(raw[:4]), int(raw[4:])
        else:
            return None
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return year, month


def _format_month(key: tuple[int, int] | None) -> str | None:
    if key is None:
        return None
    year, month = key
    return f"{month:02d}/{year:04d}"


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero_based = divmod(month_index, 12)
    return date(year, month_zero_based + 1, 1)


def _mature_window(today: date) -> tuple[date, date]:
    year = today.year - 1
    return date(year, 1, 1), date(year, 12, 31)


def _portal_receipt(payload: dict[str, Any]) -> Recebimento:
    return Recebimento(
        valor=float(payload.get("valor") or 0),
        orgao=payload.get("nomeOrgao") or "",
        codigo_orgao=(
            str(payload.get("codigoOrgao"))
            if payload.get("codigoOrgao") is not None
            else None
        ),
        orgao_superior=payload.get("nomeOrgaoSuperior"),
        unidade_gestora=payload.get("nomeUnidadeGestora"),
        competencia=_format_month(_month_key(payload.get("anoMes"))),
        acao=payload.get("nomeAcao"),
    )


def _receipt_detail(receipt: Recebimento) -> dict[str, Any]:
    key = _month_key(receipt.competencia)
    return {
        "mes": key[0] * 100 + key[1] if key is not None else None,
        "competencia": receipt.competencia,
        "valor": receipt.valor,
        "orgao": receipt.orgao,
        "codigo_orgao": receipt.codigo_orgao,
        "orgao_superior": receipt.orgao_superior,
        "unidade_gestora": receipt.unidade_gestora,
        "acao": receipt.acao,
    }


def _receipt_sort_key(receipt: Recebimento) -> tuple[bool, tuple[int, int]]:
    key = _month_key(receipt.competencia)
    return key is None, key or (9999, 12)


def _annual_series(receipts: list[Recebimento]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for receipt in receipts:
        key = _month_key(receipt.competencia)
        if key is None:
            continue
        year = str(key[0])
        totals[year] = totals.get(year, 0.0) + receipt.valor
    return {
        year: round(total, 2)
        for year, total in sorted(totals.items(), key=lambda item: int(item[0]))
    }


def _volatility(series: dict[str, float], current_year: int) -> dict[str, float]:
    complete_years = sorted(
        (int(year), float(total))
        for year, total in series.items()
        if int(year) < current_year
    )
    totals = [total for _, total in complete_years]
    mean = statistics.fmean(totals) if totals else 0.0
    cv = statistics.pstdev(totals) / mean if len(totals) > 1 and mean else 0.0

    drops = []
    for (_, previous), (_, current) in zip(complete_years, complete_years[1:]):
        if previous > 0:
            drops.append(max((previous - current) / previous * 100, 0.0))

    return {
        "cv": round(cv, 4),
        "maior_queda_anual_pct": round(max(drops, default=0.0), 2),
    }


def _derived_metrics(receipts: list[Recebimento], today: date) -> dict[str, Any]:
    dated = [
        (key, receipt)
        for receipt in receipts
        if (key := _month_key(receipt.competencia)) is not None
    ]
    dated.sort(key=lambda item: item[0])
    rolling_start_date = _shift_month(date(today.year, today.month, 1), -11)
    rolling_start = (rolling_start_date.year, rolling_start_date.month)
    current_month = (today.year, today.month)
    faturamento_12m = sum(
        receipt.valor
        for key, receipt in dated
        if rolling_start <= key <= current_month
    )

    series = _annual_series(receipts)
    concentration = ConcentracaoSacado.calcular(receipts)
    concentration_data = None
    if concentration is not None:
        concentration_data = {
            "hhi": concentration.hhi,
            "n_orgaos": concentration.n_orgaos,
            "top_orgao": concentration.top_orgao,
            "top_participacao": concentration.top_participacao,
            "faixa": concentration.faixa,
        }

    competencies = sorted({key for key, _ in dated})
    return {
        "faturamento_verificado_12m": round(faturamento_12m, 2),
        "serie_anual": series,
        "meses_com_recebimento": len(competencies),
        "primeira_competencia": _format_month(competencies[0]) if competencies else None,
        "ultima_competencia": _format_month(competencies[-1]) if competencies else None,
        "concentracao": concentration_data,
        "volatilidade": _volatility(series, today.year),
    }


def _window_total(
    receipts: list[Recebimento],
    start: date,
    end: date,
) -> tuple[float, int]:
    start_key = (start.year, start.month)
    end_key = (end.year, end.month)
    selected = [
        receipt.valor
        for receipt in receipts
        if (key := _month_key(receipt.competencia)) is not None
        and start_key <= key <= end_key
    ]
    return round(sum(selected), 2), len(selected)


def _reconcile(
    broadfactor: list[Recebimento],
    portal: list[Recebimento],
    today: date,
    portal_error: str | None = None,
) -> dict[str, Any]:
    start, end = _mature_window(today)
    total_broadfactor, count_broadfactor = _window_total(broadfactor, start, end)
    total_portal, count_portal = _window_total(portal, start, end)

    divergence_pct: float | None = None
    if portal_error is not None:
        status = "ERRO_PORTAL"
    elif count_broadfactor == 0:
        status = "SEM_DADO_BROADFACTOR"
    elif count_portal == 0:
        status = "SEM_DADO_PORTAL"
    else:
        if total_broadfactor == 0:
            divergence_pct = 0.0 if total_portal == 0 else 100.0
        else:
            divergence_pct = round(
                abs(total_broadfactor - total_portal) / total_broadfactor * 100,
                2,
            )
        status = (
            "DIVERGENTE"
            if divergence_pct > RECONCILIATION_TOLERANCE_PCT
            else "CONVERGENTE"
        )

    return {
        "janela_inicio": start.isoformat(),
        "janela_fim": end.isoformat(),
        "total_broadfactor": total_broadfactor,
        "total_portal": total_portal,
        "divergencia_pct": divergence_pct,
        "status": status,
        "erro_portal": portal_error,
    }


def _fetch_portal(
    cnpj: str,
    token: str | None = None,
    today: date | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> tuple[list[Recebimento], dict[str, Any]]:
    from app.core.config import settings

    today = today or date.today()
    query_start = period_start or _shift_month(
        date(today.year, today.month, 1), -11
    )
    query_end = period_end or today
    headers = {"chave-api-dados": token or settings.PORTAL_TRANSPARENCIA_TOKEN}

    resources: list[dict[str, Any]] = []
    started = time.monotonic()
    page = 1
    with httpx.Client(timeout=20, verify=settings.HTTPX_VERIFY_SSL) as client:
        while True:
            if time.monotonic() - started > MAX_SECONDS:
                raise TimeoutError(
                    f"recursos_recebidos excedeu {MAX_SECONDS}s na pagina {page}"
                )
            if page > MAX_PAGES:
                logger.warning(
                    "recursos_recebidos.portal_pagination_cap_reached",
                    cnpj=cnpj,
                    max_pages=MAX_PAGES,
                    registros=len(resources),
                )
                break

            url = (
                f"{BASE_URL}/despesas/recursos-recebidos"
                f"?codigoFavorecido={cnpj}"
                f"&mesAnoInicio={query_start.strftime('%m/%Y')}"
                f"&mesAnoFim={query_end.strftime('%m/%Y')}"
                f"&pagina={page}"
            )
            data = fetch_json_with_retry(client, url, headers=headers)
            if not data:
                break
            if not isinstance(data, list):
                raise ValueError("Resposta inesperada do Portal da Transparencia")
            resources.extend(item for item in data if isinstance(item, dict))
            page += 1

    pagination = {
        "paginas_lidas": page,
        "registros": len(resources),
        "atingiu_cap": page > MAX_PAGES,
        "motivo_fim": "limite_paginas" if page > MAX_PAGES else "pagina_vazia",
    }
    return [_portal_receipt(item) for item in resources], pagination


def _get_cotacao_id(operation_id: str | None) -> str | None:
    if not operation_id:
        return None
    from app.core.database import supabase

    result = _execute_with_retry(
        operation_id,
        "recursos_recebidos",
        "load_operation_quote_id",
        lambda: supabase.table("operations")
        .select("cotacao_id")
        .eq("id", operation_id)
        .single()
        .execute(),
    )
    return (result.data or {}).get("cotacao_id")


def _fetch_broadfactor(cotacao_id: str) -> list[Recebimento]:
    client = BroadfactorClient()
    return client.recebimentos(
        cotacao_id,
        paginas=MAX_PAGES,
        tamanho=BROADFACTOR_PAGE_SIZE,
        exigir_completo=True,
    )


def _build_snapshot(
    primary: list[Recebimento],
    source: str,
    reconciliation: dict[str, Any] | None,
    today: date,
    pagination: dict[str, Any],
) -> dict[str, Any]:
    metrics = _derived_metrics(primary, today)
    details = [
        _receipt_detail(receipt)
        for receipt in sorted(primary, key=_receipt_sort_key)
    ]
    result = {
        "fonte_primaria": source,
        "reconciliacao": reconciliation,
        "total_registros": len(primary),
        "valor_total_recebido": round(sum(item.valor for item in primary), 2),
        "periodo_inicio": metrics["primeira_competencia"],
        "periodo_fim": metrics["ultima_competencia"],
        "orgaos_pagadores": sorted({item.orgao for item in primary if item.orgao}),
        "valor_por_ano": metrics["serie_anual"],
        "recursos_detalhe": details,
        "_pagination": pagination,
    }
    result.update(metrics)
    return result


def _log_snapshot_size(snapshot: dict[str, Any], operation_id: str | None) -> None:
    serialized_bytes = len(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )
    logger.info(
        "recursos_recebidos.snapshot_built",
        operation_id=operation_id,
        fonte_primaria=snapshot.get("fonte_primaria"),
        total_registros=snapshot.get("total_registros"),
        detalhes=len(snapshot.get("recursos_detalhe") or []),
        parsed_result_bytes=serialized_bytes,
        estimated_snapshot_write_bytes=serialized_bytes * 2,
    )


def _fetch(
    cnpj: str,
    token: str | None = None,
    operation_id: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    cotacao_id = _get_cotacao_id(operation_id)
    broadfactor: list[Recebimento] = []
    portal_error: str | None = None

    if cotacao_id:
        try:
            broadfactor = _fetch_broadfactor(cotacao_id)
        except Exception as exc:
            logger.warning(
                "recursos_recebidos.broadfactor_failed",
                operation_id=operation_id,
                cotacao_id=cotacao_id,
                error=str(exc),
            )

    try:
        portal_period: dict[str, date] = {}
        if cotacao_id and broadfactor:
            period_start, period_end = _mature_window(today)
            portal_period = {
                "period_start": period_start,
                "period_end": period_end,
            }
        portal, portal_pagination = _fetch_portal(
            cnpj,
            token=token,
            today=today,
            **portal_period,
        )
    except Exception as exc:
        if not broadfactor:
            raise
        portal = []
        portal_error = str(exc)
        portal_pagination = {
            "paginas_lidas": 0,
            "registros": 0,
            "atingiu_cap": False,
            "motivo_fim": "erro",
        }
        logger.warning(
            "recursos_recebidos.portal_reconciliation_failed",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            error=str(exc),
        )

    if not cotacao_id:
        snapshot = _build_snapshot(
            portal,
            "PORTAL_TRANSPARENCIA",
            None,
            today,
            portal_pagination,
        )
        _log_snapshot_size(snapshot, operation_id)
        return snapshot

    reconciliation = _reconcile(
        broadfactor,
        portal,
        today,
        portal_error=portal_error,
    )
    if reconciliation["status"] == "DIVERGENTE":
        logger.warning(
            "recursos_recebidos.reconciliation_divergent",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            total_broadfactor=reconciliation["total_broadfactor"],
            total_portal=reconciliation["total_portal"],
            divergencia_pct=reconciliation["divergencia_pct"],
        )

    if broadfactor:
        snapshot = _build_snapshot(
            broadfactor,
            "BROADFACTOR",
            reconciliation,
            today,
            {
                "paginas_solicitadas": MAX_PAGES,
                "tamanho_pagina": BROADFACTOR_PAGE_SIZE,
            },
        )
    else:
        snapshot = _build_snapshot(
            portal,
            "PORTAL_TRANSPARENCIA",
            reconciliation,
            today,
            portal_pagination,
        )
    _log_snapshot_size(snapshot, operation_id)
    return snapshot


_task = BaseComponentTask()


def run_recursos_recebidos(operation_id: str):
    return _task.execute(
        operation_id,
        component="recursos_recebidos",
        handler=_fetch,
        use_cache=False,
    )
