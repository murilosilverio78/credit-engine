import time

import structlog


_CACHE_TTL = 60
_cache = {"params": None, "ts": 0.0}
logger = structlog.get_logger()

REQUIRED_PARAMS = {
    "ticket_minimo",
    "ticket_maximo",
    "pct_max_margem",
    "prazo_padrao_meses",
    "dias_minimos_expiracao",
    "prazo_minimo_dias",
    "cnpj_idade_minima_meses",
}


def _load_from_db() -> dict[str, float] | None:
    from app.core.database import supabase

    try:
        rows = (
            supabase.table("eligibility_parameters")
            .select("key,value")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.error("eligibility_params.load_error", error=str(exc))
        return None

    params = {row["key"]: float(row["value"]) for row in rows}
    missing = sorted(REQUIRED_PARAMS - params.keys())
    if missing:
        logger.error("eligibility_params.missing", keys=missing)
        return None
    return params


def get_eligibility_config(force_reload: bool = False) -> dict[str, float]:
    now = time.time()
    if (
        force_reload
        or _cache["params"] is None
        or (now - _cache["ts"]) > _CACHE_TTL
    ):
        params = _load_from_db()
        if params is None:
            if _cache["params"] is None:
                raise RuntimeError("Parametros de elegibilidade indisponiveis")
            return _cache["params"]
        _cache.update({"params": params, "ts": now})
    return _cache["params"]


def invalidate_cache() -> None:
    _cache["ts"] = 0.0
