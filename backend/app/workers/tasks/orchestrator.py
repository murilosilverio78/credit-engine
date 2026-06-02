"""
Orchestrator: controls the credit analysis pipeline.
Each component runs as a synchronous worker function in a background thread.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import structlog

from app.core.database import supabase

logger = structlog.get_logger()
MANUAL_COMPONENTS = ("cndt_tst", "cnd_federal", "fgts")
PHASE2_COMPONENTS = (
    "contratos",
    "recursos_recebidos",
    "acordos_leniencia",
    "ceis",
    "cnep",
    "cepim",
)
RUNNING_STALE_MINUTES = 15


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _prazo_meses(prazo_dias: int) -> int:
    """Converte prazo em dias para meses usando a premissa de 30 dias/mes."""
    return max(round(prazo_dias / 30), 1)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _component_result_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(result.get("error")) or result.get("status") == "failed"


def _mark_operation_failed(operation_id: str, message: str):
    supabase.table("operations")\
        .update({"status": "failed", "error_message": message})\
        .eq("id", operation_id)\
        .execute()


def _mark_stale_components_failed(operation_id: str, components: list[str]):
    if not components:
        return
    message = f"snapshot running ha mais de {RUNNING_STALE_MINUTES} minutos"
    supabase.table("component_snapshots")\
        .update({"status": "failed", "error_message": message})\
        .eq("operation_id", operation_id)\
        .in_("component", components)\
        .execute()


def _incomplete_components(operation_id: str, components: tuple[str, ...]) -> list[str]:
    now = datetime.now(timezone.utc)
    result = supabase.table("component_snapshots")\
        .select("component,status,started_at,error_message")\
        .eq("operation_id", operation_id)\
        .in_("component", list(components))\
        .execute()
    snapshots = {row.get("component"): row for row in (result.data or [])}

    incomplete = []
    stale_running = []
    for component in components:
        snapshot = snapshots.get(component)
        if not snapshot:
            incomplete.append(f"{component}(missing)")
            continue
        status = snapshot.get("status") or "missing"
        if status == "completed":
            continue
        started_at = _parse_datetime(snapshot.get("started_at"))
        if status == "running" and started_at:
            age = now - started_at
            if age > timedelta(minutes=RUNNING_STALE_MINUTES):
                incomplete.append(f"{component}(running_stale>{RUNNING_STALE_MINUTES}m)")
                stale_running.append(component)
                continue
        incomplete.append(f"{component}({status})")

    _mark_stale_components_failed(operation_id, stale_running)
    return incomplete


async def _run_component(run_fn, operation_id: str):
    """Executa um worker sincrono em thread separada, capturando excecao."""
    try:
        return await asyncio.to_thread(run_fn, operation_id)
    except Exception as exc:
        logger.error(
            "component.error",
            operation_id=operation_id,
            component=run_fn.__name__,
            error=str(exc),
        )
        return {"error": str(exc)}


async def start_analysis(operation_id: str):
    """
    Start the analysis.

    Phase 1: Brasil API and company lookup in parallel.
    Phase 2: contracts, resources and sanctions in parallel.
    Phases 3 and 4 start after phase 2, unless manual uploads are required.
    """
    from app.workers.tasks.acordos_leniencia import run_acordos_leniencia
    from app.workers.tasks.brasil_api import run_brasil_api
    from app.workers.tasks.ceis import run_ceis
    from app.workers.tasks.cepim import run_cepim
    from app.workers.tasks.cnep import run_cnep
    from app.workers.tasks.contratos import run_contratos
    from app.workers.tasks.pessoa_juridica import run_pessoa_juridica
    from app.workers.tasks.recursos_recebidos import run_recursos_recebidos

    logger.info("pipeline.started", operation_id=operation_id)

    phase1_results = await asyncio.gather(
        _run_component(run_brasil_api, operation_id),
        _run_component(run_pessoa_juridica, operation_id),
    )
    if any(_component_result_failed(result) for result in phase1_results):
        message = "pipeline abortado: falha em componente da fase 1"
        _mark_operation_failed(operation_id, message)
        logger.error("pipeline.phase1_failed", operation_id=operation_id, results=phase1_results)
        return {"operation_id": operation_id, "status": "failed", "error": message}

    phase2_results = await asyncio.gather(
        _run_component(run_contratos, operation_id),
        _run_component(run_recursos_recebidos, operation_id),
        _run_component(run_acordos_leniencia, operation_id),
        _run_component(run_ceis, operation_id),
        _run_component(run_cnep, operation_id),
        _run_component(run_cepim, operation_id),
    )
    if any(_component_result_failed(result) for result in phase2_results):
        message = "pipeline abortado: falha em componente da fase 2"
        _mark_operation_failed(operation_id, message)
        logger.error("pipeline.phase2_failed", operation_id=operation_id, results=phase2_results)
        return {"operation_id": operation_id, "status": "failed", "error": message}

    incomplete = _incomplete_components(operation_id, PHASE2_COMPONENTS)
    if incomplete:
        message = "pipeline abortado: fase 2 incompleta: " + ", ".join(incomplete)
        _mark_operation_failed(operation_id, message)
        logger.error(
            "pipeline.phase2_incomplete",
            operation_id=operation_id,
            incomplete_components=incomplete,
        )
        return {"operation_id": operation_id, "status": "failed", "error": message}

    after_phase2 = await _after_phase2(operation_id)
    if isinstance(after_phase2, dict) and after_phase2.get("status") == "failed":
        return after_phase2
    return {"operation_id": operation_id, "status": "pipeline_started"}


async def _after_phase2(operation_id: str):
    """Pause when enabled manual components require certificate uploads."""
    configured = supabase.table("component_config")\
        .select("component")\
        .in_("component", list(MANUAL_COMPONENTS))\
        .eq("enabled", True)\
        .eq("timeout_seconds", 0)\
        .execute()
    manual_components = [
        row["component"]
        for row in (configured.data or [])
        if row["component"] in MANUAL_COMPONENTS
    ]

    if not manual_components:
        logger.info("pipeline.no_manual_uploads", operation_id=operation_id)
        return await _phase3_4(operation_id)

    existing = supabase.table("upload_tasks")\
        .select("document_type,status")\
        .eq("operation_id", operation_id)\
        .in_("document_type", manual_components)\
        .execute()
    existing_components = {
        row["document_type"]
        for row in (existing.data or [])
        if row["status"] in {"pending", "completed"}
    }
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    pending_tasks = [
        {
            "operation_id": operation_id,
            "document_type": component,
            "token": str(uuid4()),
            "status": "pending",
            "expires_at": expires_at,
        }
        for component in manual_components
        if component not in existing_components
    ]
    if pending_tasks:
        supabase.table("upload_tasks").insert(pending_tasks).execute()

    supabase.table("component_snapshots")\
        .update({"status": "waiting_upload"})\
        .eq("operation_id", operation_id)\
        .in_("component", manual_components)\
        .execute()
    supabase.table("operations")\
        .update({"status": "manual_review"})\
        .eq("id", operation_id)\
        .execute()

    logger.info(
        "pipeline.waiting_upload",
        operation_id=operation_id,
        components=manual_components,
    )
    return {
        "operation_id": operation_id,
        "status": "manual_review",
        "components": manual_components,
    }


async def _phase3_4(operation_id: str):
    """Fase 3 (web research) e Fase 4 (score) em sequencia."""
    from app.workers.tasks.score_engine import run_score_engine
    from app.workers.tasks.web_research import run_web_research

    incomplete = _incomplete_components(operation_id, PHASE2_COMPONENTS)
    if incomplete:
        message = "pipeline abortado: fase 2 incompleta: " + ", ".join(incomplete)
        _mark_operation_failed(operation_id, message)
        logger.error(
            "pipeline.phase2_incomplete",
            operation_id=operation_id,
            incomplete_components=incomplete,
        )
        return {"operation_id": operation_id, "status": "failed", "error": message}

    web_result = await _run_component(run_web_research, operation_id)
    if _component_result_failed(web_result):
        message = "pipeline abortado: falha em web_research"
        _mark_operation_failed(operation_id, message)
        logger.error("pipeline.web_research_failed", operation_id=operation_id, result=web_result)
        return {"operation_id": operation_id, "status": "failed", "error": message}

    score_result = await _run_component(run_score_engine, operation_id)
    if _component_result_failed(score_result):
        message = "pipeline abortado: falha em score_engine"
        _mark_operation_failed(operation_id, message)
        logger.error("pipeline.score_engine_failed", operation_id=operation_id, result=score_result)
        return {"operation_id": operation_id, "status": "failed", "error": message}

    await _complete_analysis(operation_id)
    return {"operation_id": operation_id, "status": "completed"}


async def _complete_analysis(operation_id: str):
    """Persist the final score output and terminate the pipeline."""
    result = supabase.table("component_snapshots")\
        .select("parsed_result")\
        .eq("operation_id", operation_id)\
        .eq("component", "score_engine")\
        .single()\
        .execute()

    operation_result = supabase.table("operations")\
        .select("valor_solicitado,prazo_dias")\
        .eq("id", operation_id)\
        .maybe_single()\
        .execute()

    score_result = (result.data or {}).get("parsed_result") or {}
    operation = operation_result.data or {}
    data = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "score": score_result.get("score"),
        "rating": score_result.get("rating"),
        "limite_aprovado": score_result.get("limite_aprovado_rs"),
    }
    rating = str(data["rating"] or "").upper()
    valor = _as_float(operation.get("valor_solicitado"))
    prazo_dias = _as_int(operation.get("prazo_dias"))

    if rating in {"A", "B", "C", "D"} and valor > 0 and prazo_dias > 0:
        try:
            from app.services.pricing_engine import compute_taxa

            pricing = compute_taxa(rating, valor, _prazo_meses(prazo_dias))
            data["taxa_sugerida"] = pricing.get("taxa_sugerida_am")
            data["taxa_breakdown"] = pricing
        except Exception as exc:
            logger.error(
                "pipeline.pricing_error",
                operation_id=operation_id,
                rating=rating,
                valor=valor,
                prazo_dias=prazo_dias,
                error=str(exc),
            )
    else:
        logger.info(
            "pipeline.pricing_skipped",
            operation_id=operation_id,
            rating=rating,
            valor=valor,
            prazo_dias=prazo_dias,
        )

    supabase.table("operations")\
        .update(data)\
        .eq("id", operation_id)\
        .execute()

    logger.info(
        "pipeline.completed",
        operation_id=operation_id,
        score=data["score"],
        rating=data["rating"],
    )
    return {"operation_id": operation_id, "status": "completed"}


async def resume_after_upload(operation_id: str):
    """Continue analysis once all required certificate uploads are complete."""
    resumed = supabase.table("operations")\
        .update({"status": "pending"})\
        .eq("id", operation_id)\
        .eq("status", "manual_review")\
        .execute()
    if not resumed.data:
        logger.info("pipeline.resume_skipped", operation_id=operation_id)
        return {"operation_id": operation_id, "status": "already_resumed"}

    logger.info("pipeline.resumed", operation_id=operation_id)
    return await _phase3_4(operation_id)
