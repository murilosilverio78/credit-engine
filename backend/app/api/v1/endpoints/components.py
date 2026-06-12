from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService

router = APIRouter()
audit = AuditService()


@router.get("/")
async def list_components(_: dict = Depends(get_current_user)):
    """Lista configurações de todos os componentes."""
    result = supabase.table("component_config").select("*").execute()
    return result.data


@router.patch("/{component}/toggle")
async def toggle_component(
    component: str,
    enabled: bool,
    current_user: dict = Depends(get_current_user),
):
    """Ativa ou desativa um componente."""
    if current_user.get("role") != "diretor":
        raise HTTPException(
            status_code=403,
            detail="Apenas diretores podem alterar componentes",
        )

    before = supabase.table("component_config")\
        .select("enabled")\
        .eq("component", component)\
        .single()\
        .execute()
    previous_value = before.data.get("enabled") if before.data else None

    supabase.table("component_config")\
        .update({"enabled": enabled})\
        .eq("component", component)\
        .execute()

    audit.log(
        operation_id=None,
        action="component_toggle",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "system"),
        payload={
            "component": component,
            "previous_value": previous_value,
            "new_value": enabled,
            "changed_by": current_user.get("email"),
        },
    )
    return {"component": component, "enabled": enabled}
