from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService


router = APIRouter()
audit = AuditService()


class AlcadaUpdate(BaseModel):
    max_valor: Optional[float] = None
    max_rating: Optional[Literal["A", "B", "C", "D", "E"]] = None
    pode_override: Optional[bool] = None
    override_max_valor: Optional[float] = None
    override_max_rating: Optional[Literal["A", "B", "C", "D", "E"]] = None
    pode_aprovar_escalada: Optional[bool] = None
    justificativa: str = Field(min_length=10)


@router.get("")
async def list_alcadas():
    result = supabase.table("alcada_config")\
        .select("*")\
        .order("max_valor", desc=False)\
        .execute()
    return result.data


@router.get("/audit")
async def list_alcada_audit():
    result = supabase.table("audit_trail")\
        .select("id, action, actor_id, actor_type, override_reason, previous_value, new_value, created_at")\
        .eq("action", "alcada_config_updated")\
        .order("created_at", desc=True)\
        .limit(20)\
        .execute()
    return result.data


@router.patch("/{role}")
async def update_alcada(
    role: str,
    payload: AlcadaUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(status_code=403, detail="Somente diretor pode alterar alçadas")

    previous = supabase.table("alcada_config")\
        .select("*")\
        .eq("role", role)\
        .single()\
        .execute()
    if not previous.data:
        raise HTTPException(status_code=404, detail="Alçada não encontrada")

    data = payload.model_dump(exclude={"justificativa"}, exclude_none=True)
    data["updated_by"] = current_user.get("id")
    result = supabase.table("alcada_config")\
        .update(data)\
        .eq("role", role)\
        .execute()

    audit.log(
        operation_id=None,
        action="alcada_config_updated",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "diretor"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa,
        previous_value=previous.data,
        new_value=result.data[0] if result.data else data,
        payload={"table": "alcada_config", "role": role},
    )

    return result.data[0]
