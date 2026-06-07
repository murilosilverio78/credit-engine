from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.database import supabase


router = APIRouter()


@router.get("/pendentes")
async def list_pending_escaladas(current_user: dict = Depends(get_current_user)):
    query = supabase.table("v_escaladas_pendentes").select("*")
    role = current_user.get("role") or current_user.get("alcada") or ""

    role_aliases = {
        "analyst": "analista",
        "manager": "gerente",
        "committee": "diretor",
        "comite": "diretor",
    }
    role = role_aliases.get(role, role)

    if role == "analista":
        return []
    if role == "gerente":
        query = query.eq("deve_resolver_role", "gerente")
    elif role == "diretor":
        query = query.in_("deve_resolver_role", ["gerente", "diretor"])
    else:
        return []

    result = query.order("requested_at", desc=False).execute()
    return result.data or []
