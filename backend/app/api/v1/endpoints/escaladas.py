from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.database import supabase


router = APIRouter()


@router.get("/pendentes")
async def list_pending_escaladas(current_user: dict = Depends(get_current_user)):
    query = supabase.table("v_escaladas_pendentes").select("*")
    role = current_user.get("role")

    if role == "gerente":
        query = query.eq("requested_role", "analista")
    elif role == "analista":
        query = query.eq("requested_role", "__none__")

    result = query.order("created_at", desc=False).execute()
    return result.data
