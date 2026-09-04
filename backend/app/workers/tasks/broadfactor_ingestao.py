"""Broadfactor quote ingestion job.

There is no scheduler in the current asyncio deployment. Invoke
``run_broadfactor_ingestao`` externally at 08:05 and 14:15 America/Sao_Paulo.
The module never calls Broadfactor during import.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import structlog

from app.integrations.broadfactor.client import BroadfactorClient, Cotacao


logger = structlog.get_logger()
SCHEDULE_BRT = ("08:05", "14:15")
INGESTION_STAGE = "S0_INGESTAO"


def _operation_exists(supabase: Any, cotacao_id: str) -> bool:
    result = (
        supabase.table("operations")
        .select("id")
        .eq("cotacao_id", cotacao_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


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
    (
        supabase.table("cotacoes_broadfactor")
        .update(data)
        .eq("cotacao_id", cotacao_id)
        .execute()
    )


async def _start_analysis(operation_id: str):
    from app.workers.tasks.orchestrator import start_analysis

    return await start_analysis(operation_id)


async def run_broadfactor_ingestao() -> dict[str, Any]:
    """Triage current quotes, persist accepted ones and start their analyses."""
    from app.core.database import supabase
    from app.services.eligibility_params_service import get_eligibility_config
    from app.services.ingestion_discard_service import record_ingestion_discard
    from app.services.operation_service import OperationService

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
            "duplicadas": 0,
            "falhas": 1,
        }

    motivos = Counter(motivo for _, motivo in descartadas)
    falhas = 0

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
    analysis_jobs: list[tuple[str, asyncio.Task]] = []
    criadas = 0
    duplicadas = 0

    for cotacao in aprovadas:
        try:
            if _operation_exists(supabase, cotacao.id):
                duplicadas += 1
                logger.info(
                    "broadfactor_ingestao.duplicate_skipped",
                    cotacao_id=cotacao.id,
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
                (cotacao.id, asyncio.create_task(_start_analysis(operation_id)))
            )
            criadas += 1
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
            *(task for _, task in analysis_jobs),
            return_exceptions=True,
        )
        for (cotacao_id, _), result in zip(analysis_jobs, results):
            if isinstance(result, Exception):
                falhas += 1
                logger.error(
                    "broadfactor_ingestao.analysis_failed",
                    cotacao_id=cotacao_id,
                    error=str(result),
                )

    summary = {
        "status": "completed",
        "total": len(aprovadas) + len(descartadas),
        "aprovadas": len(aprovadas),
        "descartadas": len(descartadas),
        "descartadas_por_motivo": dict(sorted(motivos.items())),
        "criadas": criadas,
        "duplicadas": duplicadas,
        "falhas": falhas,
    }
    logger.info("broadfactor_ingestao.completed", **summary)
    return summary


__all__ = ["SCHEDULE_BRT", "run_broadfactor_ingestao"]
