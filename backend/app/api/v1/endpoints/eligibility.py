import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.eligibility_params_service import invalidate_cache
from app.services.operation_watchdog_service import invalidate_watchdog_config_cache


router = APIRouter()
audit = AuditService()

EDITABLE_PARAMETERS = {
    "ticket_minimo",
    "ticket_maximo",
    "pct_max_contrato",
    "prazo_padrao_meses",
    "dias_minimos_expiracao",
    "prazo_minimo_dias",
    "cnpj_idade_minima_meses",
    "watchdog_heartbeat_timeout_minutos",
}


class EligibilityParameterUpdate(BaseModel):
    value: float
    justificativa: str = Field(min_length=10)


def _unprocessable(detail: str) -> None:
    raise HTTPException(status_code=422, detail=detail)


def _related_value(parameters: dict[str, dict], key: str) -> float:
    row = parameters.get(key)
    try:
        return float(row["value"] if row else None)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=503,
            detail=f"Parametro relacionado indisponivel: {key}",
        ) from None


def _validate_value(
    key: str,
    value: float,
    parameters: dict[str, dict],
) -> None:
    if key not in EDITABLE_PARAMETERS:
        _unprocessable("Parametro nao editavel por este endpoint")
    if not math.isfinite(value):
        _unprocessable("Valor deve ser um numero finito")

    if key == "ticket_minimo":
        ticket_maximo = _related_value(parameters, "ticket_maximo")
        if value <= 0 or value >= ticket_maximo:
            _unprocessable("ticket_minimo deve ser maior que zero e menor que ticket_maximo")
        return
    if key == "ticket_maximo":
        ticket_minimo = _related_value(parameters, "ticket_minimo")
        if value <= ticket_minimo:
            _unprocessable("ticket_maximo deve ser maior que ticket_minimo")
        return

    ranges = {
        "pct_max_contrato": (0.01, 1.00),
        "prazo_padrao_meses": (1, 60),
        "dias_minimos_expiracao": (0, 90),
        "prazo_minimo_dias": (0, 365),
        "cnpj_idade_minima_meses": (0, 120),
        "watchdog_heartbeat_timeout_minutos": (5, 120),
    }
    minimum, maximum = ranges[key]
    if not minimum <= value <= maximum:
        _unprocessable(f"{key} deve estar entre {minimum:g} e {maximum:g}")


@router.get("/parameters")
async def list_eligibility_parameters():
    result = (
        supabase.table("eligibility_parameters")
        .select("*")
        .order("grupo", desc=False)
        .order("key", desc=False)
        .execute()
    )
    return result.data


@router.patch("/parameters/{key}")
async def update_eligibility_parameter(
    key: str,
    payload: EligibilityParameterUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user.get("role") != "diretor":
        raise HTTPException(
            status_code=403,
            detail="Somente diretor pode alterar parametros de elegibilidade",
        )

    current_result = supabase.table("eligibility_parameters").select("*").execute()
    parameters = {
        str(row["key"]): row
        for row in (current_result.data or [])
        if row.get("key")
    }
    previous = parameters.get(key)
    if previous is None:
        raise HTTPException(status_code=404, detail="Parametro nao encontrado")

    _validate_value(key, payload.value, parameters)
    data = {
        "value": payload.value,
        "updated_by": current_user.get("id"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = (
        supabase.table("eligibility_parameters")
        .update(data)
        .eq("key", key)
        .execute()
    )
    updated = result.data[0] if result.data else {**previous, **data}

    audit.log(
        operation_id=None,
        action="eligibility_parameter_updated",
        actor_id=current_user.get("id"),
        actor_type=current_user.get("role", "diretor"),
        ip_address=request.client.host if request.client else None,
        override_reason=payload.justificativa,
        previous_value=previous,
        new_value=updated,
        payload={"table": "eligibility_parameters", "key": key},
    )
    invalidate_cache()
    invalidate_watchdog_config_cache()
    return updated
