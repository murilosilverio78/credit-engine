"""
SnapshotService: persiste resultados de cada componente de consulta.
"""
from typing import Optional
from datetime import datetime, timezone
from app.core.database import supabase
import structlog

logger = structlog.get_logger()


class SnapshotService:

    def get_cnpj(self, operation_id: str) -> str:
        """Retorna CNPJ da operação (síncrono — chamado dentro de workers Celery)."""
        result = supabase.table("operations")\
            .select("cnpj")\
            .eq("id", operation_id)\
            .single()\
            .execute()
        return result.data["cnpj"]

    def mark_running(self, operation_id: str, component: str):
        """Marca componente como 'running' e registra started_at."""
        supabase.table("component_snapshots").update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("operation_id", operation_id).eq("component", component).execute()

    def save_result(
        self,
        operation_id: str,
        component: str,
        raw_result: Optional[dict],
        parsed_result: Optional[dict],
        status: str,
        duration_ms: int,
        error_message: Optional[str] = None,
        score_contrib: Optional[float] = None,
        cost_usd: Optional[float] = None,
        from_cache: bool = False,
    ):
        """Persiste resultado do componente no snapshot."""
        data = {
            "status": status,
            "raw_result": raw_result,
            "parsed_result": parsed_result,
            "duration_ms": duration_ms,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if error_message:
            data["error_message"] = error_message
        if score_contrib is not None:
            data["score_contrib"] = score_contrib
        if cost_usd is not None:
            data["cost_usd"] = cost_usd

        supabase.table("component_snapshots")\
            .update(data)\
            .eq("operation_id", operation_id)\
            .eq("component", component)\
            .execute()

        logger.info(
            "snapshot.saved",
            operation_id=operation_id,
            component=component,
            status=status,
            duration_ms=duration_ms,
            from_cache=from_cache,
        )

    def mark_waiting_upload(self, operation_id: str, component: str):
        """Marca componente como aguardando upload manual."""
        supabase.table("component_snapshots").update({
            "status": "waiting_upload",
        }).eq("operation_id", operation_id).eq("component", component).execute()

    def get_all_results(self, operation_id: str) -> list[dict]:
        """Retorna todos os snapshots completados de uma operação."""
        result = supabase.table("component_snapshots")\
            .select("component, status, parsed_result, score_contrib")\
            .eq("operation_id", operation_id)\
            .execute()
        return result.data

    def count_pending(self, operation_id: str) -> int:
        """Conta componentes ainda não concluídos (pending, running, waiting_upload)."""
        result = supabase.table("component_snapshots")\
            .select("id", count="exact")\
            .eq("operation_id", operation_id)\
            .in_("status", ["pending", "running", "waiting_upload"])\
            .execute()
        return result.count or 0

    def increment_retry(self, operation_id: str, component: str):
        """Incrementa contador de retries."""
        result = supabase.table("component_snapshots")\
            .select("retry_count")\
            .eq("operation_id", operation_id)\
            .eq("component", component)\
            .single()\
            .execute()

        current = result.data.get("retry_count", 0) or 0
        supabase.table("component_snapshots").update({
            "retry_count": current + 1,
        }).eq("operation_id", operation_id).eq("component", component).execute()
