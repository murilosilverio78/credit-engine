"""
Orchestrator: controla o pipeline completo de análise de crédito.
Cada componente é uma task Celery independente.
O orquestrador sequencia, detecta pausas (uploads pendentes) e retoma.
"""
from datetime import datetime, timezone

from celery import chain, group, chord
from app.workers.celery_app import celery_app
import structlog

logger = structlog.get_logger()


@celery_app.task(queue="orchestrator", name="orchestrator.complete_analysis")
def complete_analysis(_results: list[dict], operation_id: str):
    """Finaliza a operação com a saída já persistida pelo score engine."""
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


@celery_app.task(bind=True, queue="orchestrator", name="orchestrator.start_analysis")
def start_analysis(self, operation_id: str):
    """
    Inicia o pipeline completo para uma operação.

    Fase 1 (paralela): Brasil API e pessoa jurídica
    Fase 2 (paralela): contratos, recursos e sanções
    Fase 3: reputação web
    Fase 4: score e relatório final
    """
    from app.workers.tasks.brasil_api import run_brasil_api
    from app.workers.tasks.pessoa_juridica import run_pessoa_juridica
    from app.workers.tasks.contratos import run_contratos
    from app.workers.tasks.recursos_recebidos import run_recursos_recebidos
    from app.workers.tasks.acordos_leniencia import run_acordos_leniencia
    from app.workers.tasks.ceis import run_ceis
    from app.workers.tasks.cnep import run_cnep
    from app.workers.tasks.cepim import run_cepim
    from app.workers.tasks.web_research import run_web_research
    from app.workers.tasks.score_engine import run_score_engine

    logger.info("pipeline.started", operation_id=operation_id)

    # Fase 1: paralela
    phase1 = group(
        run_brasil_api.si(operation_id),
        run_pessoa_juridica.si(operation_id),
    )

    # Fase 2: paralela
    phase2 = group(
        run_contratos.si(operation_id),
        run_recursos_recebidos.si(operation_id),
        run_acordos_leniencia.si(operation_id),
        run_ceis.si(operation_id),
        run_cnep.si(operation_id),
        run_cepim.si(operation_id),
    )

    # Fase 3 e 4: pesquisa sequencial, seguida do score e callback terminal.
    phase3_4 = chain(
        run_web_research.si(operation_id),
        chord(
            group(run_score_engine.si(operation_id)),
            complete_analysis.s(operation_id),
        ),
    )

    # Pipeline completo
    pipeline = chain(phase1, phase2, phase3_4)
    pipeline.apply_async()

    return {"operation_id": operation_id, "status": "pipeline_started"}


@celery_app.task(bind=True, queue="orchestrator", name="orchestrator.resume_after_upload")
def resume_after_upload(self, operation_id: str):
    """
    Retoma o pipeline após upload manual de certidão.
    Verifica se ainda há uploads pendentes antes de continuar.
    """
    from app.workers.tasks.web_research import run_web_research
    from app.workers.tasks.score_engine import run_score_engine

    logger.info("pipeline.resumed", operation_id=operation_id)

    # Verifica se todos uploads foram feitos
    # (a verificação real acontece dentro do run_score_engine)
    pipeline = chain(
        run_web_research.si(operation_id),
        chord(
            group(run_score_engine.si(operation_id)),
            complete_analysis.s(operation_id),
        ),
    )
    pipeline.apply_async()

    return {"operation_id": operation_id, "status": "pipeline_resumed"}
