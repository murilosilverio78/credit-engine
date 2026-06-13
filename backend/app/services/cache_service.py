"""CacheService: evita consultas duplicadas por CNPJ com TTL por componente."""
from datetime import datetime, timedelta, timezone
import time
from typing import Optional

import structlog

from app.core.database import supabase


logger = structlog.get_logger()

# TTL padrão por componente (horas), sobrescrito por component_config.cache_ttl_hours.
DEFAULT_TTL: dict[str, int] = {
    "brasil_api": 24,
    "pessoa_juridica": 12,
    "contratos": 12,
    "recursos_recebidos": 12,
    "ceis": 12,
    "cnep": 12,
    "cepim": 12,
    "acordos_leniencia": 12,
    "cndt_tst": 24,
    "cnd_federal": 24,
    "fgts": 24,
    "serasa_pj": 24,
    "boa_vista": 24,
    "serpro": 24,
    "web_research": 48,
    "score_engine": 0,
}

_TTL_OVERRIDES: dict[str, int] = {}
_TTL_OVERRIDES_TS = 0.0
_TTL_OVERRIDES_SECONDS = 60.0


def _get_ttl_overrides() -> dict[str, int]:
    """Lê TTLs de component_config e mantém cache em memória por 60 segundos."""
    global _TTL_OVERRIDES, _TTL_OVERRIDES_TS

    now = time.monotonic()
    if now - _TTL_OVERRIDES_TS < _TTL_OVERRIDES_SECONDS:
        return _TTL_OVERRIDES

    try:
        result = supabase.table("component_config")\
            .select("component,cache_ttl_hours")\
            .execute()
        _TTL_OVERRIDES = {
            row["component"]: row["cache_ttl_hours"]
            for row in (result.data or [])
            if row.get("cache_ttl_hours") is not None
        }
    except Exception as exc:
        logger.warning("cache.ttl_overrides_failed", error=str(exc))
        _TTL_OVERRIDES = {}

    _TTL_OVERRIDES_TS = now
    return _TTL_OVERRIDES


def _ttl_hours(component: str) -> int:
    # score_engine contém a decisão final de risco e deve sempre recalcular.
    if component == "score_engine":
        return 0
    overrides = _get_ttl_overrides()
    return overrides.get(component, DEFAULT_TTL.get(component, 0))


class CacheService:
    def get(self, cnpj: str, component: str) -> Optional[dict]:
        """
        Retorna resultado em cache se válido e não expirado.

        O TTL vem de component_config.cache_ttl_hours quando configurado; caso
        contrário, usa DEFAULT_TTL. score_engine nunca usa cache.
        """
        if _ttl_hours(component) == 0:
            return None

        try:
            now = datetime.now(timezone.utc).isoformat()
            result = supabase.table("cnpj_cache")\
                .select("result")\
                .eq("cnpj", cnpj)\
                .eq("component", component)\
                .gt("expires_at", now)\
                .single()\
                .execute()

            if result.data:
                logger.info("cache.hit", cnpj=cnpj, component=component)
                return result.data["result"]

        except Exception:
            pass

        return None

    def set(self, cnpj: str, component: str, result: dict):
        """
        Salva resultado no cache com TTL do componente.

        Usa upsert para sobrescrever cache expirado do mesmo CNPJ+componente.
        """
        ttl_hours = _ttl_hours(component)
        if ttl_hours == 0:
            return

        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        ).isoformat()

        try:
            supabase.table("cnpj_cache").upsert({
                "cnpj": cnpj,
                "component": component,
                "result": result,
                "expires_at": expires_at,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="cnpj,component").execute()

            logger.info("cache.set", cnpj=cnpj, component=component, ttl_hours=ttl_hours)

        except Exception as e:
            logger.warning("cache.set_failed", cnpj=cnpj, component=component, error=str(e))

    def invalidate(self, cnpj: str, component: Optional[str] = None):
        """
        Invalida cache de um CNPJ.
        Se component=None, invalida todos os componentes do CNPJ.
        """
        query = supabase.table("cnpj_cache").delete().eq("cnpj", cnpj)
        if component:
            query = query.eq("component", component)
        query.execute()
        logger.info("cache.invalidated", cnpj=cnpj, component=component or "all")

    def cleanup_expired(self):
        """Remove entradas expiradas."""
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("cnpj_cache").delete().lt("expires_at", now).execute()
        logger.info("cache.cleanup_done")
