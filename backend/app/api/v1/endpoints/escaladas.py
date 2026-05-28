from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.database import supabase


router = APIRouter()


@router.get("/pendentes")
async def list_pending_escaladas(current_user: dict = Depends(get_current_user)):
    query = supabase.table("v_escaladas_pendentes").select("*")
    role = current_user.get("alcada") or current_user.get("role")

    if role in {"analyst", "analista"}:
        query = query.eq("deve_resolver_role", "gerente")
    elif role in {"manager", "gerente"}:
        query = query.in_("deve_resolver_role", ["gerente", "diretor"])

    result = query.order("requested_at", desc=False).execute()
    return result.data or []
