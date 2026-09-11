from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Response
from pydantic import BaseModel, field_validator
from typing import Literal, Optional
from datetime import datetime, timedelta, timezone
import re

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.report_pdf_service import ReportPdfService

router = APIRouter()
audit = AuditService()

RATING_RANK = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
SCORE_REPROCESSING_STALE_MINUTES = 15

VALID_TRANSITIONS = {
    "approve":            {"completed"},
    "reject":             {"completed"},
    "escalate":           {"completed"},
    "resolve-escalation": {"escalated"},
}


class PropostaInput(BaseModel):
    cnpj: str
    origem_dados: Literal["API_BROADFACTOR", "MANUAL"]
    cotacao_id: Optional[str] = None
    valor_solicitado: Optional[float] = None
    contrato_id: Optional[str] = None
    contrato_saldo: Optional[float] = None
    margem_disponivel: Optional[float] = None
    prazo_dias: Optional[int] = None
    prazo_vincendo_meses: Optional[int] = None
    source: str = "frontend_mvp"

    @field_validator("cnpj")
    @classmethod
    def validate_cnpj(cls, v):
        digits = re.sub(r"\D", "", v)
        if len(digits) != 14:
            raise ValueError("CNPJ deve ter 14 dígitos")
        return digits


class ApprovalInput(BaseModel):
    justificativa: Optional[str] = None


class ResolveEscalationInput(BaseModel):
    approval_id: Optional[str] = None
    action: Literal["escalation_approved", "escalation_rejected"]
    justificativa: Optional[str] = None


def _operation_snapshot(operation_id: str) -> dict:
    result = supabase.table("operations")        .select("*")        .eq("id", operation_id)        .single()        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    return result.data


def _score_manual_review_context(
    operation_id: str,
) -> tuple[bool | None, list[str]]:
    result = (
        supabase.table("component_snapshots")
        .select("parsed_result")
        .eq("operation_id", operation_id)
        .eq("component", "score_engine")
        .eq("status", "completed")
        .maybe_single()
        .execute()
    )
    if result is None or not getattr(result, "data", None):
        return None, []
    parsed_result = result.data.get("parsed_result")
    if not isinstance(parsed_result, dict) or not parsed_result:
        return None, []
    raw_sources = parsed_result.get("fontes_sancao_nao_verificadas") or []
    sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else []
    return parsed_result.get("requer_revisao_manual") is True, sources


def _claim_score_reprocessing(operation_id: str) -> None:
    snapshot = supabase.table("component_snapshots")\
        .select("status,started_at")\
        .eq("operation_id", operation_id)\
        .eq("component", "score_engine")\
        .maybe_single()\
        .execute()

    if snapshot.data:
        current_status = snapshot.data.get("status")
        if current_status == "running":
            started_at = snapshot.data.get("started_at")
            try:
                started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                started = None
            cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=SCORE_REPROCESSING_STALE_MINUTES
            )
            if started and started >= cutoff:
                raise HTTPException(
                    status_code=409,
                    detail="Reprocessamento de score já está em andamento",
                )
        started_at = datetime.now(timezone.utc).isoformat()
        update_query = supabase.table("component_snapshots")\
            .update({
                "status": "running",
                "started_at": started_at,
                "error_message": None,
            })\
            .eq("operation_id", operation_id)\
            .eq("component", "score_engine")\
            .eq("status", current_status)
        previous_started_at = snapshot.data.get("started_at")
        if current_status == "running":
            if previous_started_at is None:
                update_query = update_query.is_("started_at", "null")
            else:
                update_query = update_query.eq("started_at", previous_started_at)
        claimed = update_query.execute()
        if not claimed.data:
            raise HTTPException(
                status_code=409,
                detail="Reprocessamento de score já foi iniciado por outra solicitação",
            )
        return

    try:
        supabase.table("component_snapshots").insert({
            "operation_id": operation_id,
            "component": "score_engine",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as exc:
        concurrent = supabase.table("component_snapshots")\
            .select("status")\
            .eq("operation_id", operation_id)\
            .eq("component", "score_engine")\
            .maybe_single()\
            .execute()
        if concurrent.data:
            raise HTTPException(
                status_code=409,
                detail="Reprocessamento de score já foi iniciado por outra solicitação",
            ) from exc
        raise


def _insert_approval(
    operation: dict,
    action: str,
    current_user: dict,
    justificativa: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    data = {
        "action": action,
        "justificativa": justificativa,
        "operation_id": operation["id"],
        "rating_momento": operation.get("rating"),
        "requested_role": current_user.get("role"),
        "score_momento": operation.get("score"),
        "valor_operacao": operation.get("valor_solicitado") or operation.get("limite_aprovado"),
    }
    if current_user.get("id"):
        data["requested_by"] = current_user.get("id")
    if extra:
        data.update(extra)
    result = supabase.table("operation_approvals").insert(data).execute()
    return result.data[0]


def _update_operation_status(operation_id: str, new_status: str, expected_status: str):
    """Update condicional atômico — levanta 409 em race condition ou double-submit."""
    result = supabase.table("operations")        .update({"status": new_status})        .eq("id", operation_id)        .eq("status", expected_status)        .execute()

    if not result.data:
        current = supabase.table("operations")            .select("status")            .eq("id", operation_id)            .single()            .execute()
        current_status = current.data.get("status", "unknown") if current.data else "unknown"
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONCURRENT_STATE_CHANGE",
                "current_status": current_status,
                "message": "Status da operação foi alterado por outra requisição simultânea",
            },
        )


def _get_alcada_config(role: str) -> dict:
    """Lê configuração de alçada do Supabase para o role informado."""
    result = supabase.table("alcada_config")        .select("*")        .eq("role", role)        .single()        .execute()
    if not result.data:
        raise HTTPException(
            status_code=500,
            detail=f"Configuração de alçada não encontrada para role '{role}'",
        )
    return result.data


def _check_state_transition(operation: dict, action: str):
    """Levanta 409 se o status atual não permite a transição solicitada."""
    current = operation.get("status", "")
    allowed = VALID_TRANSITIONS.get(action, set())
    if current not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_STATE_TRANSITION",
                "current_status": current,
                "allowed_statuses": sorted(allowed),
                "action": action,
            },
        )


def _check_alcada(operation: dict, alcada: dict):
    """Levanta 403 se valor ou rating da operação excede a alçada do usuário."""
    valor = operation.get("valor_solicitado") or operation.get("limite_aprovado") or 0
    rating = operation.get("rating") or "E"
    max_valor = alcada.get("max_valor") or 0
    max_rating = alcada.get("max_rating") or "A"

    valor_excede = valor > max_valor
    rating_excede = RATING_RANK.get(rating, 5) > RATING_RANK.get(max_rating, 1)

    if valor_excede or rating_excede:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ALCADA_EXCEDIDA",
                "escalate": True,
                "valor_operacao": valor,
                "max_valor_role": max_valor,
                "rating_operacao": rating,
                "max_rating_role": max_rating,
            },
        )


@router.post("/", status_code=201)
async def create_operation(payload: PropostaInput, background_tasks: BackgroundTasks):
    from app.services.eligibility_service import check_eligibility
    from app.services.ingestion_discard_service import record_ingestion_discard
    from app.services.operation_service import OperationService
    from app.workers.tasks.orchestrator import start_analysis

    eligibility = check_eligibility(
        cnpj=payload.cnpj,
        valor_solicitado=payload.valor_solicitado,
        contrato_saldo=payload.contrato_saldo,
        margem_disponivel=payload.margem_disponivel,
        prazo_dias=payload.prazo_dias,
        prazo_vincendo_meses=payload.prazo_vincendo_meses,
    )
    if not eligibility.elegivel:
        record_ingestion_discard(
            cotacao_id=payload.cotacao_id,
            cnpj=payload.cnpj,
            valor_solicitado=payload.valor_solicitado,
            margem_disponivel=payload.margem_disponivel,
            valor_enquadrado=eligibility.valor_enquadrado,
            motivo=eligibility.motivo or "Falha de elegibilidade",
            estagio="elegibilidade",
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ELIGIBILITY_FAILED",
                "message": eligibility.motivo,
                "campo": eligibility.campo,
            },
        )

    svc = OperationService()
    operation = await svc.create(
        cnpj=payload.cnpj,
        origem_dados=payload.origem_dados,
        cotacao_id=payload.cotacao_id,
        valor_solicitado=payload.valor_solicitado,
        valor_enquadrado=eligibility.valor_enquadrado,
        saldo_vincendo=eligibility.saldo_vincendo,
        contrato_id=payload.contrato_id,
        contrato_saldo=payload.contrato_saldo,
        margem_disponivel=payload.margem_disponivel,
        prazo_dias=payload.prazo_dias,
        prazo_vincendo_meses=payload.prazo_vincendo_meses,
        prazo_final_meses=eligibility.prazo_final_meses,
        prazo_vincendo_indisponivel=(
            "prazo_vincendo_indisponivel" in eligibility.flags
        ),
        source=payload.source,
    )

    background_tasks.add_task(start_analysis, str(operation["id"]))

    return {
        "operation_id": operation["id"],
        "cnpj": payload.cnpj,
        "valor_enquadrado": eligibility.valor_enquadrado,
        "saldo_vincendo": eligibility.saldo_vincendo,
        "prazo_final_meses": eligibility.prazo_final_meses,
        "flags": eligibility.flags,
        "status": "pending",
        "message": "Análise iniciada. Acompanhe via /api/v1/operations/{id}",
    }


@router.get("/{operation_id}")
async def get_operation(operation_id: str):
    from app.services.operation_service import OperationService

    svc = OperationService()
    operation = await svc.get_with_snapshots(operation_id)

    if not operation:
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    return operation


@router.post("/{operation_id}/reprocessar-score", status_code=202)
async def reprocess_operation_score(
    operation_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(
            status_code=403,
            detail="Somente diretor pode reprocessar o score",
        )

    operation = _operation_snapshot(operation_id)
    if operation.get("status") not in {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_STATE_FOR_SCORE_REPROCESSING",
                "current_status": operation.get("status"),
                "allowed_statuses": ["completed", "failed"],
            },
        )

    _claim_score_reprocessing(operation_id)
    previous_value = {
        "score": operation.get("score"),
        "rating": operation.get("rating"),
        "taxa_sugerida": operation.get("taxa_sugerida"),
    }

    from app.workers.tasks.orchestrator import reprocess_score

    background_tasks.add_task(
        reprocess_score,
        operation_id,
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "diretor"),
        ip_address=request.client.host if request.client else None,
        previous_value=previous_value,
    )
    return {
        "operation_id": operation_id,
        "status": "accepted",
        "previous_value": previous_value,
        "message": "Reprocessamento do score iniciado",
    }


@router.get("/{operation_id}/score-versions")
async def list_score_versions(
    operation_id: str,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(
            status_code=403,
            detail="Somente diretor pode consultar o histórico do score",
        )

    _operation_snapshot(operation_id)
    result = supabase.table("score_snapshot_versions")\
        .select(
            "id,operation_id,snapshot_id,parsed_result,raw_result,score_contrib,"
            "started_at,completed_at,archived_at,archive_reason"
        )\
        .eq("operation_id", operation_id)\
        .order("archived_at", desc=True)\
        .execute()
    return {"items": result.data or [], "total": len(result.data or [])}


@router.get("/{operation_id}/report.pdf")
async def get_operation_report_pdf(
    operation_id: str,
    _current_user: dict = Depends(get_current_user),
):
    pdf = await ReportPdfService().render_operation_pdf(operation_id)
    filename = f"credit-engine-{operation_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/")
async def list_operations(
    status: Optional[str] = None,
    cnpj: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    from app.services.operation_service import OperationService

    svc = OperationService()
    return await svc.list(status=status, cnpj=cnpj, limit=limit, offset=offset)


@router.post("/{operation_id}/approve")
async def approve_operation(
    operation_id: str,
    payload: ApprovalInput,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    role = current_user.get("role", "analista")
    operation = _operation_snapshot(operation_id)
    _check_state_transition(operation, "approve")
    alcada = _get_alcada_config(role)
    _check_alcada(operation, alcada)
    requer_revisao_manual, fontes_sancao = _score_manual_review_context(operation_id)
    if requer_revisao_manual is None:
        raise HTTPException(
            status_code=409,
            detail="Score em reprocessamento ou indisponível; aguarde para aprovar",
        )
    justificativa = (payload.justificativa or "").strip()
    if requer_revisao_manual and len(justificativa) < 10:
        raise HTTPException(
            status_code=400,
            detail="Sanções não verificadas: justificativa obrigatória para aprovar",
        )
    approval = _insert_approval(
        operation,
        "approved",
        current_user,
        justificativa or None,
    )
    _update_operation_status(operation_id, "approved", expected_status=operation["status"])
    audit.log(
        operation_id=operation_id,
        action="operation_approved",
        actor_id=current_user.get("id"),
        actor_type=role,
        ip_address=request.client.host if request.client else None,
        payload={
            "approval_id": approval.get("id"),
            "sancao_nao_verificada": requer_revisao_manual,
            "fontes_sancao_nao_verificadas": fontes_sancao,
        },
    )
    return {"ok": True, "approval_id": approval.get("id")}


@router.post("/{operation_id}/reject")
async def reject_operation(
    operation_id: str,
    payload: ApprovalInput,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if not payload.justificativa or len(payload.justificativa.strip()) < 10:
        raise HTTPException(status_code=400, detail="Justificativa obrigatória com ao menos 10 caracteres")
    operation = _operation_snapshot(operation_id)
    if operation.get("status") == "escalated":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "USE_RESOLVE_ESCALATION",
                "message": "Operação escalada deve ser decidida via resolve-escalation",
            },
        )
    _check_state_transition(operation, "reject")
    approval = _insert_approval(operation, "rejected", current_user, payload.justificativa.strip())
    _update_operation_status(operation_id, "rejected", expected_status=operation["status"])
    audit.log(
        operation_id=operation_id,
        action="operation_rejected",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "analista"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa.strip(),
        payload={"approval_id": approval.get("id")},
    )
    return {"ok": True, "approval_id": approval.get("id")}


@router.post("/{operation_id}/escalate")
async def escalate_operation(
    operation_id: str,
    payload: ApprovalInput,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    operation = _operation_snapshot(operation_id)
    _check_state_transition(operation, "escalate")
    approval = _insert_approval(operation, "escalated", current_user, payload.justificativa)
    _update_operation_status(operation_id, "escalated", expected_status=operation["status"])
    audit.log(
        operation_id=operation_id,
        action="operation_escalated",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "analista"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa,
        payload={"approval_id": approval.get("id")},
    )
    return {"ok": True, "approval_id": approval.get("id")}


@router.post("/{operation_id}/resolve-escalation")
async def resolve_escalation(
    operation_id: str,
    payload: ResolveEscalationInput,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    role = current_user.get("role", "")
    if role not in {"gerente", "diretor", "comite"}:
        raise HTTPException(status_code=403, detail="Apenas gerentes e diretores podem resolver escaladas")

    operation = _operation_snapshot(operation_id)
    _check_state_transition(operation, "resolve-escalation")

    alcada = _get_alcada_config(role)
    if not alcada.get("pode_aprovar_escalada"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "SEM_PERMISSAO_ESCALADA",
                "message": f"Role '{role}' não tem permissão para aprovar escaladas",
            },
        )
    _check_alcada(operation, alcada)

    justificativa = (payload.justificativa or "").strip()
    if payload.action == "escalation_rejected" and len(justificativa) < 10:
        raise HTTPException(status_code=400, detail="Justificativa obrigatória com ao menos 10 caracteres")

    requer_revisao_manual = False
    fontes_sancao: list[str] = []
    if payload.action == "escalation_approved":
        review_context, fontes_sancao = _score_manual_review_context(operation_id)
        if review_context is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Score em reprocessamento ou indisponível; aguarde para aprovar"
                ),
            )
        requer_revisao_manual = review_context
        if requer_revisao_manual and len(justificativa) < 10:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Sanções não verificadas: justificativa obrigatória para aprovar"
                ),
            )

    decision_extra = {"decided_role": role}
    if current_user.get("id"):
        decision_extra["decided_by"] = current_user.get("id")

    approval = _insert_approval(
        operation, payload.action, current_user, justificativa or None, extra=decision_extra,
    )
    new_status = "approved" if payload.action == "escalation_approved" else "rejected"
    _update_operation_status(operation_id, new_status, expected_status="escalated")

    audit.log(
        operation_id=operation_id,
        action=payload.action,
        actor_id=current_user.get("id"),
        actor_type=role,
        ip_address=request.client.host if request.client else None,
        override_reason=justificativa or None,
        payload={
            "approval_id": approval.get("id"),
            "resolved_approval_id": payload.approval_id,
            **(
                {
                    "sancao_nao_verificada": requer_revisao_manual,
                    "fontes_sancao_nao_verificadas": fontes_sancao,
                }
                if payload.action == "escalation_approved"
                else {}
            ),
        },
    )
    return {"ok": True, "approval_id": approval.get("id")}
