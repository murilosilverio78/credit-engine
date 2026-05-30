"""
Orchestrator: controls the credit analysis pipeline.
Each component is an independent Celery task.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from celery import chain, chord, group
import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger()
MANUAL_COMPONENTS = ("cndt_tst", "cnd_federal", "fgts")


def _phase3_4(operation_id: str):
    from app.workers.tasks.score_engine import run_score_engine
    from app.workers.tasks.web_research import run_web_research

    return chain(
        run_web_research.si(operation_id),
        chord(
            group(run_score_engine.si(operation_id)),
            complete_analysis.s(operation_id),
        ),
    )


@celery_app.task(queue="orchestrator", name="orchestrator.complete_analysis")
def complete_analysis(_results: list[dict], operation_id: str):
    """Persist the final score output and terminate the pipeline."""
    from app.core.database import supabase

    result = supabase.table("component_snapshots")\
        .select("parsed_result")\
        .eq("operation_id", operation_id)\
        .eq("component", "score_engine")\
        .single()\
        .execute()

    score_result = (result.data or {}).get("parsed_result") or {}
    data = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "score": score_result.get("score"),
        "rating": score_result.get("rating"),
        "taxa_sugerida": score_result.get("taxa_sugerida_am"),
        "taxa_breakdown": score_result.get("taxa_breakdown"),
        "limite_aprovado": score_result.get("limite_aprovado_rs"),
    }

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


@celery_app.task(queue="orchestrator", name="orchestrator.after_phase2")
def after_phase2(_results: list[dict], operation_id: str):
    """Pause when enabled manual components require certificate uploads."""
    from app.core.database import supabase

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
        _phase3_4(operation_id).apply_async()
        return {"operation_id": operation_id, "status": "continuing"}

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


@celery_app.task(bind=True, queue="orchestrator", name="orchestrator.start_analysis")
def start_analysis(self, operation_id: str):
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

    phase1 = group(
        run_brasil_api.si(operation_id),
        run_pessoa_juridica.si(operation_id),
    )
    phase2 = chord(
        group(
            run_contratos.si(operation_id),
            run_recursos_recebidos.si(operation_id),
            run_acordos_leniencia.si(operation_id),
            run_ceis.si(operation_id),
            run_cnep.si(operation_id),
            run_cepim.si(operation_id),
        ),
        after_phase2.s(operation_id),
    )

    chain(phase1, phase2).apply_async()
    return {"operation_id": operation_id, "status": "pipeline_started"}


@celery_app.task(bind=True, queue="orchestrator", name="orchestrator.resume_after_upload")
def resume_after_upload(self, operation_id: str):
    """Continue analysis once all required certificate uploads are complete."""
    from app.core.database import supabase

    resumed = supabase.table("operations")\
        .update({"status": "pending"})\
        .eq("id", operation_id)\
        .eq("status", "manual_review")\
        .execute()
    if not resumed.data:
        logger.info("pipeline.resume_skipped", operation_id=operation_id)
        return {"operation_id": operation_id, "status": "already_resumed"}

    logger.info("pipeline.resumed", operation_id=operation_id)
    _phase3_4(operation_id).apply_async()
    return {"operation_id": operation_id, "status": "pipeline_resumed"}
