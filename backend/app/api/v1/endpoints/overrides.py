from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter()


class OverrideInput(BaseModel):
    override_type: Literal["rating", "score", "taxa", "limite", "status_operacao"]
    previous_value: Any
    new_value: Any
    justificativa: str = Field(min_length=1)
    requested_by: Optional[str] = None
    escalar: bool = False


class OverrideReviewInput(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewed_by: str = Field(min_length=1)
    review_comment: Optional[str] = None


@router.post("/operations/{operation_id}/override", status_code=201)
async def create_override(operation_id: str, payload: OverrideInput, request: Request):
    """Solicita override e aplica automaticamente alterações dentro da alçada."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    try:
        override = await svc.create(
            operation_id=operation_id,
            override_type=payload.override_type,
            previous_value=payload.previous_value,
            new_value=payload.new_value,
            justificativa=payload.justificativa,
            requested_by=payload.requested_by,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        if payload.escalar:
            from app.core.database import supabase

            operation = supabase.table("operations")\
                .select("*")\
                .eq("id", operation_id)\
                .single()\
                .execute()\
                .data
            supabase.table("operation_approvals").insert({
                "action": "escalated",
                "credit_override_id": override.get("id"),
                "justificativa": payload.justificativa,
                "operation_id": operation_id,
                "override_campo": payload.override_type,
                "override_valor_de": payload.previous_value,
                "override_valor_para": payload.new_value,
                "rating_momento": operation.get("rating") if operation else None,
                "requested_by": payload.requested_by,
                "requested_role": "analista",
                "score_momento": operation.get("score") if operation else None,
                "valor_operacao": (operation or {}).get("valor_solicitado") or (operation or {}).get("limite_aprovado"),
            }).execute()
        return override
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/operations/{operation_id}/override/{override_id}/review")
async def review_override(
    operation_id: str,
    override_id: str,
    payload: OverrideReviewInput,
    request: Request,
):
    """Aprova ou rejeita override sujeito à alçada committee."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    try:
        return await svc.review(
            operation_id=operation_id,
            override_id=override_id,
            decision=payload.decision,
            reviewed_by=payload.reviewed_by,
            review_comment=payload.review_comment,
            ip_address=request.client.host if request.client else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/operations/{operation_id}/overrides")
async def list_operation_overrides(operation_id: str):
    """Lista overrides de uma operação."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    return await svc.list_for_operation(operation_id)


@router.get("/pending")
async def list_pending_overrides():
    """Lista overrides aguardando revisão pelo comitê."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    return await svc.list_pending()
