"""Broadfactor quote ingestion job.

There is no scheduler in the current asyncio deployment. Trigger the internal
HTTP endpoint at 08:05 and 14:15 America/Sao_Paulo. The module never calls
Broadfactor during import.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import structlog

from app.integrations.broadfactor.client import BroadfactorClient, Cotacao
from app.workers.base import _execute_snapshot_write as _execute_with_retry


logger = structlog.get_logger()
SCHEDULE_BRT = ("08:05", "14:15")
INGESTION_STAGE = "S0_INGESTAO"
MAX_ANALYSIS_ATTEMPTS = 3


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

    return await start_analysis(operation_id)


async def run_broadfactor_ingestao(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Triage quotes and, unless dry-running, create and analyze operations."""
    from app.services.eligibility_params_service import get_eligibility_config

    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than zero")

    params = get_eligibility_config()
    pct_max_contrato = float(params["pct_max_contrato"])

    try:
        client = BroadfactorClient()
        aprovadas, descartadas = await asyncio.to_thread(
            client.triar,
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

    motivos = Counter(motivo for _, motivo in descartadas)
    falhas = 0

    if dry_run:
        summary = {
            "status": "dry_run",
            "total": len(aprovadas) + len(descartadas),
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
    from app.services.ingestion_discard_service import record_ingestion_discard
    from app.services.operation_service import OperationService

    for cotacao, motivo in descartadas:
        try:
            record_ingestion_discard(
                cotacao_id=cotacao.id,
                cnpj=cotacao.documento.numero,
                valor_solicitado=cotacao.valor,
                margem_disponivel=cotacao.margem_disponivel,
                valor_enquadrado=cotacao.enquadrar(pct_max_contrato),
                motivo=motivo,
                estagio=INGESTION_STAGE,
            )
        except Exception as exc:
            falhas += 1
            logger.error(
                "broadfactor_ingestao.discard_persist_failed",
                cotacao_id=cotacao.id,
                error=str(exc),
            )

    operation_service = OperationService()
    analysis_jobs: list[tuple[str, str, int, asyncio.Task]] = []
    criadas = 0
    reprocessadas = 0
    duplicadas = 0
    tentativas_esgotadas = 0
    processadas = 0

    for cotacao in aprovadas:
        try:
            existing = _get_existing_operation(supabase, cotacao.id)
            if existing and existing.get("status") != "failed":
                duplicadas += 1
                logger.info(
                    "broadfactor_ingestao.duplicate_skipped",
                    cotacao_id=cotacao.id,
                    operation_id=existing.get("id"),
                    operation_status=existing.get("status"),
                )
                continue

            if limit is not None and processadas >= limit:
                break

            if existing:
                attempts = int(existing.get("analysis_attempts") or 1)
                if attempts >= MAX_ANALYSIS_ATTEMPTS:
                    tentativas_esgotadas += 1
                    logger.error(
                        "broadfactor_ingestao.retry_exhausted",
                        cotacao_id=cotacao.id,
                        operation_id=existing.get("id"),
                        analysis_attempts=attempts,
                    )
                    _update_quote_status(
                        supabase,
                        cotacao.id,
                        "ERRO_ANALISE_FINAL",
                        str(existing["id"]),
                    )
                    continue

                claimed = _claim_failed_operation(supabase, existing)
                if not claimed:
                    duplicadas += 1
                    logger.info(
                        "broadfactor_ingestao.retry_claim_skipped",
                        cotacao_id=cotacao.id,
                        operation_id=existing.get("id"),
                    )
                    continue

                operation_id = str(claimed["id"])
                attempt = int(claimed["analysis_attempts"])
                analysis_jobs.append(
                    (
                        cotacao.id,
                        operation_id,
                        attempt,
                        asyncio.create_task(_start_analysis(operation_id)),
                    )
                )
                reprocessadas += 1
                processadas += 1
                try:
                    _update_quote_status(
                        supabase,
                        cotacao.id,
                        "REPROCESSANDO",
                        operation_id,
                    )
                except Exception as status_exc:
                    falhas += 1
                    logger.error(
                        "broadfactor_ingestao.status_update_failed",
                        cotacao_id=cotacao.id,
                        operation_id=operation_id,
                        error=str(status_exc),
                    )
                logger.info(
                    "broadfactor_ingestao.retry_started",
                    cotacao_id=cotacao.id,
                    operation_id=operation_id,
                    analysis_attempt=attempt,
                    max_analysis_attempts=MAX_ANALYSIS_ATTEMPTS,
                )
                continue

            valor_enquadrado = cotacao.enquadrar(pct_max_contrato)
            _persist_quote(supabase, cotacao, valor_enquadrado)
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
            analysis_jobs.append(
                (
                    cotacao.id,
                    operation_id,
                    1,
                    asyncio.create_task(_start_analysis(operation_id)),
                )
            )
            criadas += 1
            processadas += 1
            try:
                _update_quote_status(
                    supabase,
                    cotacao.id,
                    "OPERACAO_CRIADA",
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

    summary = {
        "status": "completed",
        "total": len(aprovadas) + len(descartadas),
        "aprovadas": len(aprovadas),
        "descartadas": len(descartadas),
        "descartadas_por_motivo": dict(sorted(motivos.items())),
        "criadas": criadas,
        "reprocessadas": reprocessadas,
        "duplicadas": duplicadas,
        "tentativas_esgotadas": tentativas_esgotadas,
        "falhas": falhas,
    }
    logger.info("broadfactor_ingestao.completed", **summary)
    return summary


__all__ = ["MAX_ANALYSIS_ATTEMPTS", "SCHEDULE_BRT", "run_broadfactor_ingestao"]
