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
    result = supabase.table("operations")\
        .select("*")\
        .eq("id", operation_id)\
        .single()\
        .execute()
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


def _update_operation_status(operation_id: str, status: str):
    supabase.table("operations")\
        .update({"status": status})\
        .eq("id", operation_id)\
        .execute()


@router.post("/", status_code=201)
async def create_operation(payload: PropostaInput, background_tasks: BackgroundTasks):
    """
    Cria uma nova operação de crédito e inicia o pipeline de análise.
    Retorna imediatamente com o operation_id; análise roda em background.
    """
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

    # Dispara pipeline assíncrono
    background_tasks.add_task(start_analysis, str(operation["id"]))

    return {
        "operation_id": operation["id"],
        "cnpj": payload.cnpj,
        "status": "pending",
        "message": "Análise iniciada. Acompanhe via /api/v1/operations/{id}",
    }


@router.get("/{operation_id}")
async def get_operation(operation_id: str):
    """Retorna o status completo de uma operação, incluindo snapshots por componente."""
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
    """Gera o relatorio de credito completo em PDF no backend."""
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
    """Lista operações com filtros. Usado pelo Admin UI."""
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
    operation = _operation_snapshot(operation_id)
    approval = _insert_approval(operation, "approved", current_user, payload.justificativa)
    _update_operation_status(operation_id, "approved")
    audit.log(
        operation_id=operation_id,
        action="operation_approved",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "analista"),
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
    approval = _insert_approval(operation, "rejected", current_user, payload.justificativa.strip())
    _update_operation_status(operation_id, "rejected")
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
    approval = _insert_approval(operation, "escalated", current_user, payload.justificativa)
    _update_operation_status(operation_id, "escalated")
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
    justificativa = (payload.justificativa or "").strip()
    if payload.action == "escalation_rejected" and len(justificativa) < 10:
        raise HTTPException(status_code=400, detail="Justificativa obrigatória com ao menos 10 caracteres")
    operation = _operation_snapshot(operation_id)
    decision_extra = {"decided_role": current_user.get("role")}
    if current_user.get("id"):
        decision_extra["decided_by"] = current_user.get("id")
    approval = _insert_approval(
        operation,
        payload.action,
        current_user,
        justificativa or None,
        extra=decision_extra,
    )
    _update_operation_status(
        operation_id,
        "approved" if payload.action == "escalation_approved" else "rejected",
    )
    audit.log(
        operation_id=operation_id,
        action=payload.action,
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "gerente"),
        ip_address=request.client.host if request.client else None,
        override_reason=justificativa or None,
        payload={
            "approval_id": approval.get("id"),
            "resolved_approval_id": payload.approval_id,
        },
    )
    return {"ok": True, "approval_id": approval.get("id")}
