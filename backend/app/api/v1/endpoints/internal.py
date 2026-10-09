import asyncio
import secrets
import time
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.analysis_runtime import is_shutting_down
from app.services.operation_watchdog_service import run_operation_watchdog
from app.services.findings.backfill import reemitir_operacao, select_operations
from app.services.policy.loader import load_policy
from app.services.policy.shadow import avaliar_sombra
from app.workers.http_utils import (
    acquire_portal_call,
    portal_api_url,
    portal_guardrail_snapshot,
    portal_headers,
)
from app.workers.tasks.broadfactor_ingestao import run_broadfactor_ingestao


router = APIRouter()
IPIFY_URL = "https://api.ipify.org?format=json"
PORTAL_DIAGNOSTIC_CNPJ = "00000000000191"
DIAGNOSTIC_TIMEOUT_SECONDS = 15.0
_monotonic = time.monotonic


class FindingsReemitRequest(BaseModel):
    operation_ids: list[UUID] | None = None
    ambiente: str = "PRODUCAO"
    limite: int = Field(default=20, ge=1, le=100)
    aplicar: bool = False


class PolicyShadowRequest(BaseModel):
    operation_ids: list[UUID] | None = None
    ambiente: str = "PRODUCAO"
    limite: int = Field(default=20, ge=1, le=100)
    aplicar: bool = False

    @field_validator("ambiente")
    @classmethod
    def validate_ambiente(cls, value: str) -> str:
        if value not in {"PRODUCAO", "TESTE"}:
            raise ValueError("ambiente deve ser PRODUCAO ou TESTE")
        return value


def verify_internal_token(
    x_internal_token: Annotated[
        str | None,
        Header(alias="X-Internal-Token"),
    ] = None,
) -> None:
    configured_token = settings.INTERNAL_JOB_TOKEN
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Job interno nao configurado: INTERNAL_JOB_TOKEN esta vazio.",
        )
    if x_internal_token is None or not secrets.compare_digest(
        x_internal_token,
        configured_token,
    ):
        raise HTTPException(status_code=401, detail="Token interno invalido.")


def _timed_get(
    client: httpx.Client,
    url: str,
    *,
    guard_portal: bool = False,
    **kwargs,
) -> tuple[httpx.Response | None, dict]:
    started_at = time.monotonic()
    try:
        if guard_portal:
            acquire_portal_call()
        response = client.get(url, **kwargs)
        error = None
    except Exception as exc:
        response = None
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 2)
    return response, {
        "status_code": response.status_code if response is not None else None,
        "tempo_ms": elapsed_ms,
        "erro": error,
    }


def _run_ip_diagnostic() -> dict:
    with httpx.Client(
        timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
        verify=settings.HTTPX_VERIFY_SSL,
    ) as client:
        ip_response, ipify = _timed_get(client, IPIFY_URL)
        ip_saida = None
        if ip_response is not None and ip_response.status_code == 200:
            try:
                ip_saida = ip_response.json().get("ip")
            except (ValueError, AttributeError):
                ipify["erro"] = "resposta JSON invalida"

        _, portal = _timed_get(
            client,
            portal_api_url("/pessoa-juridica"),
            guard_portal=True,
            headers=portal_headers(),
            params={"cnpj": PORTAL_DIAGNOSTIC_CNPJ},
        )

    return {
        "ip_saida": ip_saida,
        "ipify": ipify,
        "portal_transparencia": portal,
        "guardrails": portal_guardrail_snapshot(),
    }


@router.get("/diagnostico/ip")
async def diagnose_outbound_ip(
    _: None = Depends(verify_internal_token),
):
    return await asyncio.to_thread(_run_ip_diagnostic)


@router.post("/watchdog/operacoes")
async def trigger_operation_watchdog(
    _: None = Depends(verify_internal_token),
):
    return await asyncio.to_thread(run_operation_watchdog)


@router.post("/findings/reemitir")
async def reemitir_findings(request: FindingsReemitRequest, _: None = Depends(verify_internal_token)):
    from app.core.database import supabase

    operation_ids = await asyncio.to_thread(
        select_operations,
        operation_ids=[str(item) for item in request.operation_ids] if request.operation_ids is not None else None,
        ambiente=request.ambiente,
        limite=request.limite,
        database=supabase,
    )
    started = _monotonic()
    processed, remaining, operations, totals = 0, [], [], {}
    for index, operation_id in enumerate(operation_ids):
        if _monotonic() - started >= 45:
            remaining = operation_ids[index:]
            break
        result = await asyncio.to_thread(reemitir_operacao, operation_id, aplicar=request.aplicar, database=supabase)
        processed += 1
        operations.append(result)
        for outcome in result["especialistas"].values():
            totals[outcome] = totals.get(outcome, 0) + 1
    return {"aplicar": request.aplicar, "processadas": processed, "restantes": remaining, "operacoes": operations, "totais": totals}


@router.post("/politica/sombra")
async def executar_politica_sombra(request: PolicyShadowRequest, _: None = Depends(verify_internal_token)):
    """Run shadow comparison sequentially; this endpoint never decides credit."""
    from app.core.database import supabase

    try:
        await asyncio.to_thread(load_policy, database=supabase, status="SOMBRA")
    except LookupError:
        raise HTTPException(status_code=409, detail="Nao ha politica em SOMBRA.")
    operation_ids = await asyncio.to_thread(
        select_operations,
        operation_ids=[str(item) for item in request.operation_ids] if request.operation_ids is not None else None,
        ambiente=request.ambiente,
        limite=request.limite,
        database=supabase,
    )
    started = _monotonic()
    operations, remaining, totals = [], [], {}
    for index, operation_id in enumerate(operation_ids):
        if _monotonic() - started >= 45:
            remaining = operation_ids[index:]
            break
        result = await asyncio.to_thread(avaliar_sombra, operation_id, database=supabase, aplicar=request.aplicar)
        operations.append({"operation_id": operation_id, "classe_geral": result["classe_geral"], "divergencias": result["divergencias"]})
        key = result["classe_geral"]
        totals[key] = totals.get(key, 0) + 1
    return {"aplicar": request.aplicar, "processadas": len(operations), "restantes": remaining, "operacoes": operations, "totais": totals}


@router.post("/ingestao/broadfactor")
async def trigger_broadfactor_ingestion(
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=100),
    _: None = Depends(verify_internal_token),
):
    if is_shutting_down():
        raise HTTPException(
            status_code=503,
            detail="Serviço em encerramento; tente novamente em instantes",
        )

    if dry_run:
        return await run_broadfactor_ingestao(dry_run=True, limit=limit)

    background_tasks.add_task(
        run_broadfactor_ingestao,
        dry_run=False,
        limit=limit,
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "dry_run": False,
            "limit": limit,
        },
    )


@router.post("/operations/{operation_id}/recoletar-cadastro")
async def recoletar_cadastro(
    operation_id: UUID,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_internal_token),
):
    """Refresh only the registry component, without consuming score work."""
    from app.core.database import supabase
    from app.services.audit_service import AuditService
    from app.workers.tasks.brasil_api import run_brasil_api
    from app.workers.tasks.orchestrator import refresh_degraded_registry_flag

    operation_id_text = str(operation_id)
    operation_result = supabase.table("operations").select(
        "id,status,cotacao_id,source,analysis_attempts"
    ).eq("id", operation_id_text).maybe_single().execute()
    operation = operation_result.data or {}
    if not operation:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    if operation.get("status") in {"processing", "running"}:
        raise HTTPException(status_code=409, detail="Há análise em andamento para esta operação")

    snapshots_result = supabase.table("component_snapshots").select(
        "component,status"
    ).eq("operation_id", operation_id_text).execute()
    failed_before = {
        row.get("component") for row in (snapshots_result.data or [])
        if row.get("status") == "failed"
    }
    await asyncio.to_thread(run_brasil_api, operation_id_text, use_cache=False)
    degraded = await asyncio.to_thread(refresh_degraded_registry_flag, operation_id_text)
    source_result = supabase.table("component_snapshots").select(
        "parsed_result"
    ).eq("operation_id", operation_id_text).eq("component", "brasil_api").maybe_single().execute()
    parsed = (source_result.data or {}).get("parsed_result") or {}
    fonte = parsed.get("fonte") or "BRASIL_API"
    previous_status = operation.get("status")
    next_status = previous_status
    outcome: dict | None = None

    if operation.get("cotacao_id") and previous_status == "aguardando_relatorio":
        from app.services.funil_qualificacao_service import atualizar_estagio_pos_fase2, decidir_status_pos_fase2

        _stage, motivos = await asyncio.to_thread(
            atualizar_estagio_pos_fase2, str(operation["cotacao_id"]), operation_id_text
        )
        outcome = decidir_status_pos_fase2(motivos)
        next_status = outcome["status"]
        supabase.table("operations").update({
            "status": next_status,
            "pendencia_coleta": outcome["pendencia_coleta"],
        }).eq("id", operation_id_text).execute()
    elif (
        previous_status == "failed"
        and operation.get("source") == "admin_ui"
        and (failed_before - {"score_engine"}) == {"brasil_api"}
    ):
        attempts = int(operation.get("analysis_attempts") or 0)
        if attempts >= 3:
            raise HTTPException(status_code=409, detail="Limite de tentativas de análise atingido")
        next_status = "pending"
        supabase.table("operations").update({
            "status": next_status,
            "analysis_attempts": attempts + 1,
            "error_message": None,
            "completed_at": None,
        }).eq("id", operation_id_text).eq("status", "failed").execute()
        from app.workers.tasks.orchestrator import start_analysis
        background_tasks.add_task(start_analysis, operation_id_text, recovery=True)

    if next_status != previous_status:
        AuditService().log(
            operation_id=operation_id_text,
            action="operation_status_changed",
            actor_type="system",
            previous_value={"status": previous_status},
            new_value={"status": next_status},
            payload={
                "contexto": "recoleta_cadastro",
                "fonte": fonte,
                "motivos_classificados": outcome.get("motivos_classificados") if outcome else None,
            },
        )
    return {
        "operation_id": operation_id_text,
        "status": next_status,
        "dado_cadastral_degradado": degraded,
        "fonte": fonte,
        "analysis_restarted": next_status == "pending" and previous_status == "failed",
    }


EXECUTABLE_COMPONENTS = frozenset(
    {"contratos_pncp", "contratos_comprasnet", "brasil_api", "recursos_recebidos"}
)


def _component_result_summary(component: str, parsed_result: dict) -> dict:
    if component != "contratos_pncp":
        return parsed_result
    keys = (
        "n_contratos",
        "n_vigentes",
        "valor_anualizado_vigente",
        "n_orgaos",
        "contrato_cedido_match",
        "contrato_cedido",
    )
    return {key: parsed_result.get(key) for key in keys}


@router.post("/operations/{operation_id}/componentes/{component}/executar")
async def executar_componente(
    operation_id: UUID,
    component: str,
    use_cache: bool = Query(default=True),
    reavaliar_funil: bool = Query(default=False),
    _: None = Depends(verify_internal_token),
):
    """Executa uma fonte liberada e, opcionalmente, reavalia o funil aberto."""
    if component not in EXECUTABLE_COMPONENTS:
        raise HTTPException(status_code=422, detail="Componente não permitido para execução interna")

    from app.core.database import supabase

    operation_id_text = str(operation_id)
    operation_result = (
        supabase.table("operations")
        .select("id,status,cotacao_id")
        .eq("id", operation_id_text)
        .maybe_single()
        .execute()
    )
    operation = operation_result.data or {}
    if not operation:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    if operation.get("status") in {"processing", "running"}:
        raise HTTPException(status_code=409, detail="Há análise em andamento para esta operação")

    from app.workers.tasks.brasil_api import run_brasil_api
    from app.workers.tasks.contratos_comprasnet import run_contratos_comprasnet
    from app.workers.tasks.contratos_pncp import run_contratos_pncp
    from app.workers.tasks.recursos_recebidos import run_recursos_recebidos

    runners = {
        "brasil_api": run_brasil_api,
        "contratos_comprasnet": run_contratos_comprasnet,
        "contratos_pncp": run_contratos_pncp,
        "recursos_recebidos": run_recursos_recebidos,
    }
    await asyncio.to_thread(
        runners[component], operation_id_text, use_cache=use_cache
    )
    snapshot_result = (
        supabase.table("component_snapshots")
        .select("status,parsed_result")
        .eq("operation_id", operation_id_text)
        .eq("component", component)
        .maybe_single()
        .execute()
    )
    snapshot = snapshot_result.data or {}
    parsed_result = snapshot.get("parsed_result") or {}

    reavaliacao: dict | None = None
    previous_status = operation.get("status")
    if (
        reavaliar_funil
        and operation.get("cotacao_id")
        and previous_status == "aguardando_relatorio"
    ):
        from app.services.audit_service import AuditService
        from app.services.funil_qualificacao_service import (
            atualizar_estagio_pos_fase2,
            decidir_status_pos_fase2,
        )

        _stage, motivos = await asyncio.to_thread(
            atualizar_estagio_pos_fase2,
            str(operation["cotacao_id"]),
            operation_id_text,
        )
        outcome = decidir_status_pos_fase2(motivos)
        next_status = outcome["status"]
        supabase.table("operations").update({
            "status": next_status,
            "pendencia_coleta": outcome["pendencia_coleta"],
        }).eq("id", operation_id_text).execute()
        reavaliacao = {
            "status": next_status,
            "pendencia_coleta": outcome["pendencia_coleta"],
            "motivos_classificados": outcome["motivos_classificados"],
        }
        if next_status != previous_status:
            AuditService().log(
                operation_id=operation_id_text,
                action="operation_status_changed",
                actor_type="system",
                previous_value={"status": previous_status},
                new_value={"status": next_status},
                payload={
                    "contexto": "execucao_componente",
                    "component": component,
                    "motivos_classificados": outcome["motivos_classificados"],
                },
            )

    return {
        "operation_id": operation_id_text,
        "component": component,
        "status": snapshot.get("status") or "missing",
        "parsed_result": _component_result_summary(component, parsed_result),
        "reavaliacao_funil": reavaliacao,
    }
