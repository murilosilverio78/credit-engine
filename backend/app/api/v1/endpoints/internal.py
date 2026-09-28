import asyncio
import secrets
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.analysis_runtime import is_shutting_down
from app.services.operation_watchdog_service import run_operation_watchdog
from app.workers.tasks.broadfactor_ingestao import run_broadfactor_ingestao


router = APIRouter()
IPIFY_URL = "https://api.ipify.org?format=json"
PORTAL_DIAGNOSTIC_URL = (
    "https://api.portaldatransparencia.gov.br/api-de-dados/pessoa-juridica"
)
PORTAL_DIAGNOSTIC_CNPJ = "00000000000191"
DIAGNOSTIC_TIMEOUT_SECONDS = 15.0


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
    **kwargs,
) -> tuple[httpx.Response | None, dict]:
    started_at = time.monotonic()
    try:
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
            PORTAL_DIAGNOSTIC_URL,
            headers={"chave-api-dados": settings.PORTAL_TRANSPARENCIA_TOKEN},
            params={"cnpj": PORTAL_DIAGNOSTIC_CNPJ},
        )

    return {
        "ip_saida": ip_saida,
        "ipify": ipify,
        "portal_transparencia": portal,
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
