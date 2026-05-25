"""
CacheService: evita consultas duplicadas por CNPJ.
Persiste no PostgreSQL (tabela cnpj_cache) com TTL configurável por componente.
Redis pode ser adicionado como cache L1 no futuro.
"""
from typing import Optional
from datetime import datetime, timezone, timedelta
from app.core.database import supabase
import structlog

logger = structlog.get_logger()

# TTL padrão por componente (horas) — sobrescrito pela tabela component_config
DEFAULT_TTL: dict[str, int] = {
    "brasil_api": 24,
    "portal_transparencia": 12,
    "cndt_tst": 24,
    "cnd_federal": 24,
    "fgts": 24,
    "serasa_pj": 24,
    "boa_vista": 24,
    "serpro": 24,
    "web_research": 48,
    "score_engine": 0,   # score nunca cacheia — sempre recalcula
}


class CacheService:

    def get(self, cnpj: str, component: str) -> Optional[dict]:
        """
        Retorna resultado em cache se válido (não expirado).
        Retorna None se não encontrar ou expirado.
        """
        if DEFAULT_TTL.get(component, 0) == 0:
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
            pass  # Cache miss — continua normalmente

        return None

    def set(self, cnpj: str, component: str, result: dict):
        """
        Salva resultado no cache com TTL do componente.
        Usa upsert para sobrescrever cache expirado do mesmo CNPJ+componente.
        """
        ttl_hours = DEFAULT_TTL.get(component, 24)
        if ttl_hours == 0:
            return  # score_engine não cacheia

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
        """Remove entradas expiradas. Chamar via task Celery periódica."""
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("cnpj_cache").delete().lt("expires_at", now).execute()
        logger.info("cache.cleanup_done")
