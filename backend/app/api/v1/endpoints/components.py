from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_components():
    """Lista configurações de todos os componentes."""
    from app.core.database import supabase
    result = supabase.table("component_config").select("*").execute()
    return result.data


@router.patch("/{component}/toggle")
async def toggle_component(component: str, enabled: bool):
    """Ativa ou desativa um componente."""
    from app.core.database import supabase
    supabase.table("component_config")\
        .update({"enabled": enabled})\
        .eq("component", component)\
        .execute()
    return {"component": component, "enabled": enabled}
