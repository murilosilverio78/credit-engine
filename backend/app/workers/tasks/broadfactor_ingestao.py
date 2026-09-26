"""Broadfactor quote ingestion job.

There is no scheduler in the current asyncio deployment. Trigger the internal
HTTP endpoint at 08:05 and 14:15 America/Sao_Paulo. The module never calls
Broadfactor during import.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import structlog

from app.integrations.broadfactor.client import (
    BroadfactorClient,
    Cotacao,
    DocumentoAnexo,
    QuotationInactiveError,
)
from app.workers.base import _execute_snapshot_write as _execute_with_retry


logger = structlog.get_logger()
SCHEDULE_BRT = ("08:05", "14:15")
INGESTION_STAGE = "S0_INGESTAO"
MAX_ANALYSIS_ATTEMPTS = 3
STAGE_ORDER = {
    "LISTA_ESPERA": 1,
    "ENQUADRADA": 2,
    "DOCUMENTADA": 3,
    "QUALIFICADA": 4,
}


def _get_existing_operation(supabase: Any, cotacao_id: str) -> dict[str, Any] | None:
    result = _execute_with_retry(
        cotacao_id,
        "broadfactor_ingestao",
        "load_existing_operation",
        lambda: supabase.table("operations")
        .select("id,status,analysis_attempts")
        .eq("cotacao_id", cotacao_id)
        .limit(1)
        .execute(),
    )
    return result.data[0] if result.data else None


def _claim_failed_operation(
    supabase: Any,
    operation: dict[str, Any],
) -> dict[str, Any] | None:
    operation_id = str(operation["id"])
    attempts = int(operation.get("analysis_attempts") or 1)
    if attempts >= MAX_ANALYSIS_ATTEMPTS:
        return None

    next_attempt = attempts + 1
    result = _execute_with_retry(
        operation_id,
        "broadfactor_ingestao",
        "claim_failed_operation",
        lambda: supabase.table("operations")
        .update({
            "status": "processing",
            "analysis_attempts": next_attempt,
            "error_message": None,
            "completed_at": None,
        })
        .eq("id", operation_id)
        .eq("status", "failed")
        .eq("analysis_attempts", attempts)
        .execute(),
    )
    if not result.data:
        return None
    return {**result.data[0], "analysis_attempts": next_attempt}


def _persist_quote(
    supabase: Any,
    cotacao: Cotacao,
    valor_enquadrado: float,
) -> None:
    data = {
        "cotacao_id": cotacao.id,
        "cnpj": cotacao.documento.numero,
        "nome_fornecedor": cotacao.nome_fornecedor,
        "valor_solicitado": cotacao.valor,
        "margem_disponivel": cotacao.margem_disponivel,
        "saldo_vincendo": cotacao.saldo_vincendo,
        "valor_enquadrado": valor_enquadrado,
        "tipo": cotacao.tipo,
        "data_cotacao": cotacao.data.isoformat() if cotacao.data else None,
        "data_expiracao": (
            cotacao.data_expiracao.isoformat() if cotacao.data_expiracao else None
        ),
        "ambiente": "PRODUCAO",
        "status_ingestao": "PROCESSANDO",
        "payload_bruto": cotacao.bruto,
    }
    (
        supabase.table("cotacoes_broadfactor")
        .upsert(data, on_conflict="cotacao_id")
        .execute()
    )


def _update_quote_status(
    supabase: Any,
    cotacao_id: str,
    status: str,
    operation_id: str | None = None,
) -> None:
    data = {"status_ingestao": status}
    if operation_id is not None:
        data["operation_id"] = operation_id
    _execute_with_retry(
        operation_id or cotacao_id,
        "broadfactor_ingestao",
        "update_quote_status",
        lambda: supabase.table("cotacoes_broadfactor")
        .update(data)
        .eq("cotacao_id", cotacao_id)
        .execute(),
    )


async def _start_analysis(operation_id: str):
    from app.workers.tasks.orchestrator import start_analysis

    return await start_analysis(operation_id, ate_fase=2)


def _triage_reason(
    cotacao: Cotacao,
    *,
    ticket_minimo: float,
    ticket_maximo: float,
    pct_max_contrato: float,
    dias_minimos_expiracao: int,
) -> str | None:
    from datetime import date, timedelta

    limite_data = date.today() + timedelta(days=dias_minimos_expiracao)
    if not cotacao.contrato_like:
        return f"tipo_nao_elegivel:{cotacao.tipo or 'vazio'}"
    if not cotacao.documento.e_pj:
        return f"cedente_nao_pj:{cotacao.documento.tipo}"
    if not cotacao.data_expiracao or cotacao.data_expiracao < limite_data:
        return "janela_expiracao_insuficiente"
    enquadrado = cotacao.enquadrar(pct_max_contrato)
    if enquadrado < ticket_minimo:
        return "abaixo_ticket_minimo"
    if enquadrado > ticket_maximo:
        return "acima_ticket_maximo"
    return None


def _load_quote_state(supabase: Any, cotacao_id: str) -> dict[str, Any]:
    try:
        result = _execute_with_retry(
            cotacao_id,
            "broadfactor_ingestao",
            "load_quote_state",
            lambda: supabase.table("cotacoes_broadfactor")
            .select("cotacao_id,estagio,estagio_max,operation_id")
            .eq("cotacao_id", cotacao_id)
            .maybe_single()
            .execute(),
        )
        return result.data or {}
    except Exception as exc:
        logger.warning(
            "broadfactor_ingestao.quote_state_unavailable",
            cotacao_id=cotacao_id,
            error=str(exc),
        )
        return {}


def _stage_max(current: str | None, candidate: str) -> str:
    current = current or "LISTA_ESPERA"
    return current if STAGE_ORDER.get(current, 0) >= STAGE_ORDER[candidate] else candidate


def _update_quote_stage(
    supabase: Any,
    cotacao_id: str,
    estagio: str,
    *,
    motivo: str | None = None,
    operation_id: str | None = None,
    n_documentos: int | None = None,
    tipos_documento: list[str] | None = None,
    estagio_max: str | None = None,
) -> None:
    data: dict[str, Any] = {
        "estagio": estagio,
        "estagio_motivo": motivo,
        "estagio_atualizado_em": datetime.now(timezone.utc).isoformat(),
        "status_ingestao": estagio,
    }
    if estagio == "ENCERRADA":
        if estagio_max is not None:
            data["estagio_max"] = estagio_max
    else:
        data["estagio_max"] = _stage_max(estagio_max, estagio)
    if operation_id is not None:
        data["operation_id"] = operation_id
    if n_documentos is not None:
        data["n_documentos"] = n_documentos
    if tipos_documento is not None:
        data["tipos_documento"] = tipos_documento
    try:
        _execute_with_retry(
            operation_id or cotacao_id,
            "broadfactor_ingestao",
            "update_quote_stage",
            lambda: supabase.table("cotacoes_broadfactor")
            .update(data)
            .eq("cotacao_id", cotacao_id)
            .execute(),
        )
    except Exception as exc:
        logger.error(
            "broadfactor_ingestao.stage_update_failed",
            cotacao_id=cotacao_id,
            estagio=estagio,
            error=str(exc),
        )


def _documentos_info(documentos: list[DocumentoAnexo]) -> tuple[int, list[str]]:
    tipos = sorted({doc.tipo for doc in documentos if doc.tipo})
    return len(documentos), tipos


def _mark_missing_quotes_closed(supabase: Any, seen_ids: set[str]) -> int:
    result = _execute_with_retry(
        "broadfactor_ingestao",
        "broadfactor_ingestao",
        "load_active_quotes_for_closure",
        lambda: supabase.table("cotacoes_broadfactor")
        .select("cotacao_id,estagio,estagio_max")
        .eq("ambiente", "PRODUCAO")
        .neq("estagio", "ENCERRADA")
        .execute(),
    )
    closed = 0
    for row in result.data or []:
        cotacao_id = row.get("cotacao_id")
        if not cotacao_id or cotacao_id in seen_ids:
            continue
        estagio_max = _stage_max(row.get("estagio_max"), row.get("estagio") or "LISTA_ESPERA")
        _update_quote_stage(
            supabase,
            cotacao_id,
            "ENCERRADA",
            motivo="cotacao_ausente_na_listagem",
            estagio_max=estagio_max,
        )
        closed += 1
    return closed


def _count_stages(supabase: Any) -> dict[str, int]:
    result = _execute_with_retry(
        "broadfactor_ingestao",
        "broadfactor_ingestao",
        "count_quote_stages",
        lambda: supabase.table("cotacoes_broadfactor")
        .select("estagio")
        .eq("ambiente", "PRODUCAO")
        .execute(),
    )
    return dict(Counter(row.get("estagio") for row in result.data or [] if row.get("estagio")))


def _listar_cotacoes(
    client: Any,
    *,
    ticket_minimo: float,
    ticket_maximo: float,
    pct_max_contrato: float,
    dias_minimos_expiracao: int,
) -> list[Cotacao]:
    if hasattr(client, "listar_cotacoes"):
        return client.listar_cotacoes()
    aprovadas, descartadas = client.triar(
        ticket_minimo=ticket_minimo,
        ticket_maximo=ticket_maximo,
        pct_max_contrato=pct_max_contrato,
        dias_minimos_expiracao=dias_minimos_expiracao,
    )
    return [*aprovadas, *(cotacao for cotacao, _motivo in descartadas)]


async def run_broadfactor_ingestao(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the Broadfactor quote funnel without paid report components."""
    from app.services.eligibility_params_service import get_eligibility_config

    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than zero")

    try:
        from app.services.operation_watchdog_service import run_operation_watchdog

        watchdog_summary = await asyncio.to_thread(run_operation_watchdog)
        logger.info(
            "broadfactor_ingestao.watchdog_completed",
            watchdog=watchdog_summary,
        )
    except Exception as exc:
        logger.error("broadfactor_ingestao.watchdog_failed", error=str(exc))

    params = get_eligibility_config()
    pct_max_contrato = float(params["pct_max_contrato"])

    try:
        client = BroadfactorClient()
        cotacoes = await asyncio.to_thread(
            _listar_cotacoes,
            client,
            ticket_minimo=float(params["ticket_minimo"]),
            ticket_maximo=float(params["ticket_maximo"]),
            pct_max_contrato=pct_max_contrato,
            dias_minimos_expiracao=int(params["dias_minimos_expiracao"]),
        )
    except Exception as exc:
        logger.error("broadfactor_ingestao.fetch_failed", error=str(exc))
        return {
            "status": "failed",
            "total": 0,
            "aprovadas": 0,
            "descartadas": 0,
            "descartadas_por_motivo": {},
            "criadas": 0,
            "reprocessadas": 0,
            "duplicadas": 0,
            "tentativas_esgotadas": 0,
            "falhas": 1,
        }

    descartadas: list[tuple[Cotacao, str]] = []
    aprovadas: list[Cotacao] = []
    for cotacao in cotacoes:
        motivo = _triage_reason(
            cotacao,
            ticket_minimo=float(params["ticket_minimo"]),
            ticket_maximo=float(params["ticket_maximo"]),
            pct_max_contrato=pct_max_contrato,
            dias_minimos_expiracao=int(params["dias_minimos_expiracao"]),
        )
        if motivo:
            descartadas.append((cotacao, motivo))
        else:
            aprovadas.append(cotacao)
    motivos = Counter(motivo for _, motivo in descartadas)
    falhas = 0

    if dry_run:
        summary = {
            "status": "dry_run",
            "total": len(cotacoes),
            "aprovadas": len(aprovadas),
            "descartadas": len(descartadas),
            "descartadas_por_motivo": dict(sorted(motivos.items())),
            "criadas": 0,
            "reprocessadas": 0,
            "duplicadas": 0,
            "tentativas_esgotadas": 0,
            "falhas": 0,
        }
        logger.info("broadfactor_ingestao.dry_run_completed", **summary)
        return summary

    from app.core.database import supabase
    from app.services.operation_service import OperationService

    operation_service = OperationService()
    analysis_jobs: list[tuple[str, str, int, asyncio.Task]] = []
    criadas = 0
    reprocessadas = 0
    duplicadas = 0
    tentativas_esgotadas = 0
    processadas = 0

    seen_ids = {cotacao.id for cotacao in cotacoes}
    try:
        encerradas = _mark_missing_quotes_closed(supabase, seen_ids)
    except Exception as exc:
        encerradas = 0
        falhas += 1
        logger.error("broadfactor_ingestao.close_missing_failed", error=str(exc))

    for cotacao in cotacoes:
        try:
            valor_enquadrado = cotacao.enquadrar(pct_max_contrato)
            _persist_quote(supabase, cotacao, valor_enquadrado)
            state = _load_quote_state(supabase, cotacao.id)
            estagio = state.get("estagio") or "LISTA_ESPERA"
            estagio_max = state.get("estagio_max")
            operation_id = str(state["operation_id"]) if state.get("operation_id") else None

            motivo_triagem = _triage_reason(
                cotacao,
                ticket_minimo=float(params["ticket_minimo"]),
                ticket_maximo=float(params["ticket_maximo"]),
                pct_max_contrato=pct_max_contrato,
                dias_minimos_expiracao=int(params["dias_minimos_expiracao"]),
            )
            if estagio in {"LISTA_ESPERA", "ENCERRADA"}:
                if motivo_triagem:
                    _update_quote_stage(
                        supabase,
                        cotacao.id,
                        "LISTA_ESPERA",
                        motivo=motivo_triagem,
                        estagio_max=estagio_max,
                    )
                    continue
                estagio = "ENQUADRADA"
                _update_quote_stage(
                    supabase,
                    cotacao.id,
                    estagio,
                    estagio_max=estagio_max,
                )
            elif estagio == "ENQUADRADA" and motivo_triagem:
                _update_quote_stage(
                    supabase,
                    cotacao.id,
                    "ENQUADRADA",
                    motivo=motivo_triagem,
                    estagio_max=estagio_max,
                )
                continue

            if estagio in {"ENQUADRADA", "DOCUMENTADA", "QUALIFICADA"}:
                try:
                    documentos = await asyncio.to_thread(
                        client.documentos_da_cotacao,
                        cotacao.id,
                    )
                except AttributeError:
                    documentos = []
                except QuotationInactiveError:
                    _update_quote_stage(
                        supabase,
                        cotacao.id,
                        "ENCERRADA",
                        motivo="QUOTATION_INACTIVE",
                        estagio_max=_stage_max(estagio_max, estagio),
                    )
                    encerradas += 1
                    continue
                except Exception as exc:
                    if estagio == "ENQUADRADA":
                        _update_quote_stage(
                            supabase,
                            cotacao.id,
                            "ENQUADRADA",
                            motivo=f"erro_documentos_broadfactor:{exc}",
                            estagio_max=estagio_max,
                        )
                        continue
                    documentos = []
                if estagio == "ENQUADRADA":
                    n_documentos, tipos_documento = _documentos_info(documentos)
                    if n_documentos < 1:
                        _update_quote_stage(
                            supabase,
                            cotacao.id,
                            "ENQUADRADA",
                            motivo="sem_documentos_broadfactor",
                            n_documentos=0,
                            tipos_documento=[],
                            estagio_max=estagio_max,
                        )
                        continue
                    estagio = "DOCUMENTADA"
                    _update_quote_stage(
                        supabase,
                        cotacao.id,
                        estagio,
                        n_documentos=n_documentos,
                        tipos_documento=tipos_documento,
                        estagio_max=estagio_max,
                    )

            if estagio not in {"DOCUMENTADA", "QUALIFICADA"}:
                continue
            if estagio == "QUALIFICADA":
                duplicadas += 1
                continue
            existing = _get_existing_operation(supabase, cotacao.id)
            attempt = 1
            status_ingestao = "OPERACAO_CRIADA"
            if existing:
                operation_id = str(existing["id"])
                if existing.get("status") == "failed":
                    attempt = int(existing.get("analysis_attempts") or 1)
                    if attempt >= MAX_ANALYSIS_ATTEMPTS:
                        tentativas_esgotadas += 1
                        _update_quote_status(
                            supabase,
                            cotacao.id,
                            "ERRO_ANALISE_FINAL",
                            operation_id,
                        )
                        continue
                    claimed = _claim_failed_operation(supabase, existing)
                    if not claimed:
                        duplicadas += 1
                        continue
                    attempt = int(claimed.get("analysis_attempts") or attempt + 1)
                    reprocessadas += 1
                    status_ingestao = "REPROCESSANDO"
                else:
                    duplicadas += 1
            else:
                if limit is not None and processadas >= limit:
                    continue
                operation = await operation_service.create(
                    cnpj=cotacao.documento.numero,
                    origem_dados="API_BROADFACTOR",
                    cotacao_id=cotacao.id,
                    valor_solicitado=cotacao.valor,
                    valor_enquadrado=valor_enquadrado,
                    saldo_vincendo=cotacao.saldo_vincendo,
                    margem_disponivel=cotacao.margem_disponivel,
                    prazo_final_meses=int(params["prazo_padrao_meses"]),
                    prazo_vincendo_indisponivel=True,
                    source="broadfactor_ingestao",
                )
                operation_id = str(operation["id"])
                criadas += 1
                processadas += 1

            if not operation_id:
                continue
            analysis_jobs.append(
                (
                    cotacao.id,
                    operation_id,
                    attempt,
                    asyncio.create_task(_start_analysis(operation_id)),
                )
            )
            try:
                _update_quote_status(
                    supabase,
                    cotacao.id,
                    status_ingestao,
                    operation_id,
                )
            except Exception as status_exc:
                falhas += 1
                logger.error(
                    "broadfactor_ingestao.status_update_failed",
                    cotacao_id=cotacao.id,
                    error=str(status_exc),
                )
        except Exception as exc:
            falhas += 1
            logger.error(
                "broadfactor_ingestao.quote_failed",
                cotacao_id=cotacao.id,
                error=str(exc),
            )
            try:
                _update_quote_status(supabase, cotacao.id, "ERRO")
            except Exception as status_exc:
                logger.error(
                    "broadfactor_ingestao.status_update_failed",
                    cotacao_id=cotacao.id,
                    error=str(status_exc),
                )

    if analysis_jobs:
        results = await asyncio.gather(
            *(task for _, _, _, task in analysis_jobs),
            return_exceptions=True,
        )
        for (cotacao_id, operation_id, attempt, _), result in zip(
            analysis_jobs,
            results,
        ):
            analysis_failed = isinstance(result, Exception) or (
                isinstance(result, dict) and result.get("status") == "failed"
            )
            if analysis_failed:
                falhas += 1
                error = (
                    str(result)
                    if isinstance(result, Exception)
                    else str(result.get("error") or "analysis returned failed")
                )
                logger.error(
                    "broadfactor_ingestao.analysis_failed",
                    cotacao_id=cotacao_id,
                    operation_id=operation_id,
                    error=error,
                )
                try:
                    exhausted = attempt >= MAX_ANALYSIS_ATTEMPTS
                    if exhausted:
                        tentativas_esgotadas += 1
                    _update_quote_status(
                        supabase,
                        cotacao_id,
                        "ERRO_ANALISE_FINAL" if exhausted else "ERRO_ANALISE",
                        operation_id,
                    )
                except Exception as status_exc:
                    logger.error(
                        "broadfactor_ingestao.analysis_status_update_failed",
                        cotacao_id=cotacao_id,
                        operation_id=operation_id,
                        error=str(status_exc),
                    )
            else:
                try:
                    _update_quote_status(
                        supabase,
                        cotacao_id,
                        "ANALISE_CONCLUIDA",
                        operation_id,
                    )
                except Exception as status_exc:
                    logger.error(
                        "broadfactor_ingestao.analysis_status_update_failed",
                        cotacao_id=cotacao_id,
                        operation_id=operation_id,
                        error=str(status_exc),
                    )

    try:
        estagios = _count_stages(supabase)
    except Exception as exc:
        estagios = {}
        logger.error("broadfactor_ingestao.stage_count_failed", error=str(exc))

    summary = {
        "status": "completed",
        "total": len(cotacoes),
        "aprovadas": len(aprovadas),
        "descartadas": len(descartadas),
        "descartadas_por_motivo": dict(sorted(motivos.items())),
        "estagios": estagios,
        "encerradas": encerradas,
        "criadas": criadas,
        "reprocessadas": reprocessadas,
        "duplicadas": duplicadas,
        "tentativas_esgotadas": tentativas_esgotadas,
        "falhas": falhas,
    }
    logger.info("broadfactor_ingestao.completed", **summary)
    return summary


__all__ = ["MAX_ANALYSIS_ATTEMPTS", "SCHEDULE_BRT", "run_broadfactor_ingestao"]
