from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Response
from pydantic import BaseModel, field_validator
from typing import Literal, Optional
import re

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.report_pdf_service import ReportPdfService

router = APIRouter()
audit = AuditService()

RATING_RANK = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}

VALID_TRANSITIONS = {
    "approve":            {"completed"},
    "reject":             {"completed"},
    "escalate":           {"completed"},
    "resolve-escalation": {"escalated"},
}


class PropostaInput(BaseModel):
    cnpj: str
    valor_solicitado: Optional[float] = None
    contrato_id: Optional[str] = None
    contrato_saldo: Optional[float] = None
    prazo_dias: Optional[int] = None
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
    from app.services.operation_service import OperationService
    from app.workers.tasks.orchestrator import start_analysis

    eligibility = check_eligibility(
        cnpj=payload.cnpj,
        valor_solicitado=payload.valor_solicitado,
        contrato_saldo=payload.contrato_saldo,
        prazo_dias=payload.prazo_dias,
    )
    if not eligibility.elegivel:
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
        valor_solicitado=payload.valor_solicitado,
        contrato_id=payload.contrato_id,
        contrato_saldo=payload.contrato_saldo,
        prazo_dias=payload.prazo_dias,
        source=payload.source,
    )

    background_tasks.add_task(start_analysis, str(operation["id"]))

    return {
        "operation_id": operation["id"],
        "cnpj": payload.cnpj,
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
    approval = _insert_approval(operation, "approved", current_user, payload.justificativa)
    _update_operation_status(operation_id, "approved", expected_status=operation["status"])
    audit.log(
        operation_id=operation_id,
        action="operation_approved",
        actor_id=current_user.get("id"),
        actor_type=role,
        ip_address=request.client.host if request.client else None,
        payload={"approval_id": approval.get("id")},
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
        },
    )
    return {"ok": True, "approval_id": approval.get("id")}
