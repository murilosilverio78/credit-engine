from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.auth import get_current_user


router = APIRouter()


class OverrideCreateInput(BaseModel):
    override_type: str
    previous_value: Any
    new_value: Any
    justificativa: str = Field(min_length=1)


class OverrideReviewInput(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewed_by: str = Field(min_length=1)
    review_comment: Optional[str] = None


@router.get("/operations/{operation_id}/validate-taxa")
async def validate_taxa_override(
    operation_id: str,
    taxa_proposta: float = Query(...),
    requesting_role: str = Query(...),
):
    """Simula validacao de override de taxa em tempo real."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    try:
        return await svc._validate_taxa_override(
            operation_id=operation_id,
            taxa_proposta=taxa_proposta,
            requesting_role=requesting_role,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/operations/{operation_id}/override")
async def create_override(
    operation_id: str,
    payload: OverrideCreateInput,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Solicita override de taxa e aplica automaticamente dentro da alcada."""
    from app.services.override_service import OverrideService

    if payload.override_type != "taxa":
        raise HTTPException(status_code=400, detail="Apenas override de taxa e permitido")

    svc = OverrideService()
    requesting_role = current_user.get("role") or current_user.get("alcada") or "analista"
    try:
        return await svc.create(
            operation_id=operation_id,
            override_type=payload.override_type,
            previous_value=payload.previous_value,
            new_value=payload.new_value,
            justificativa=payload.justificativa,
            requested_by=current_user.get("id"),
            requesting_role=requesting_role,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
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
    """Aprova ou rejeita override sujeito a alcada superior."""
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
    """Lista overrides de uma operacao."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    return await svc.list_for_operation(operation_id)


@router.get("/pending")
async def list_pending_overrides():
    """Lista overrides aguardando revisao."""
    from app.services.override_service import OverrideService

    svc = OverrideService()
    return await svc.list_pending()
