from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.pricing_params_service import invalidate_cache


router = APIRouter()
audit = AuditService()


class PricingParameterUpdate(BaseModel):
    value: float
    justificativa: str = Field(min_length=10)


class PricingMatrixUpdate(BaseModel):
    pd_mult: Optional[float] = None
    lgd_mult: Optional[float] = None
    bond_cobertura: Optional[float] = None
    bond_premio_aa: Optional[float] = None
    perfil: Optional[str] = None
    justificativa: str = Field(min_length=10)


@router.get("/parameters")
async def list_pricing_parameters():
    result = supabase.table("pricing_parameters")\
        .select("*")\
        .order("grupo", desc=False)\
        .order("key", desc=False)\
        .execute()
    return result.data


@router.get("/matrix")
async def list_pricing_matrix():
    result = supabase.table("pricing_rating_matrix")\
        .select("*")\
        .order("ordem", desc=False)\
        .execute()
    return result.data


@router.get("/audit")
async def list_pricing_audit():
    result = supabase.table("audit_trail")\
        .select("id, action, actor_id, actor_type, override_reason, previous_value, new_value, payload, created_at")\
        .in_("action", ["pricing_parameter_updated", "pricing_matrix_updated"])\
        .order("created_at", desc=True)\
        .limit(20)\
        .execute()
    return result.data


@router.patch("/parameters/{key}")
async def update_pricing_parameter(
    key: str,
    payload: PricingParameterUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Somente diretor pode alterar precificação")

    previous = supabase.table("pricing_parameters")\
        .select("*")\
        .eq("key", key)\
        .single()\
        .execute()
    if not previous.data:
        raise HTTPException(status_code=404, detail="Parâmetro não encontrado")

    data = {
        "updated_by": current_user.get("id"),
        "value": payload.value,
    }
    result = supabase.table("pricing_parameters")\
        .update(data)\
        .eq("key", key)\
        .execute()

    audit.log(
        operation_id=None,
        action="pricing_parameter_updated",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "diretor"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa,
        previous_value=previous.data,
        new_value=result.data[0] if result.data else data,
        payload={"table": "pricing_parameters", "key": key},
    )
    invalidate_cache()

    return result.data[0]


@router.patch("/matrix/{rating}")
async def update_pricing_matrix(
    rating: Literal["A", "B", "C", "D", "E"],
    payload: PricingMatrixUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Somente diretor pode alterar precificação")

    previous = supabase.table("pricing_rating_matrix")\
        .select("*")\
        .eq("rating", rating)\
        .single()\
        .execute()
    if not previous.data:
        raise HTTPException(status_code=404, detail="Rating não encontrado")

    data = payload.model_dump(exclude={"justificativa"}, exclude_unset=True)
    data["updated_by"] = current_user.get("id")
    result = supabase.table("pricing_rating_matrix")\
        .update(data)\
        .eq("rating", rating)\
        .execute()

    audit.log(
        operation_id=None,
        action="pricing_matrix_updated",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "diretor"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa,
        previous_value=previous.data,
        new_value=result.data[0] if result.data else data,
        payload={"table": "pricing_rating_matrix", "rating": rating},
    )
    invalidate_cache()

    return result.data[0]
