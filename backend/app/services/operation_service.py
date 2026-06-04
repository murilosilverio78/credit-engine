"""
OperationService: CRUD de operações de crédito.
"""
import asyncio
from typing import Optional
from datetime import datetime, timezone
from httpx import ConnectError, RemoteProtocolError

from app.core.database import supabase
import structlog

logger = structlog.get_logger()


async def _safe_execute(query, retries=2):
    for attempt in range(retries):
        try:
            return query.execute()
        except (RemoteProtocolError, ConnectError):
            if attempt == retries - 1:
                raise
            await asyncio.sleep(0.3)


class OperationService:

    async def create(
        self,
        cnpj: str,
        valor_solicitado: Optional[float] = None,
        contrato_id: Optional[str] = None,
        contrato_saldo: Optional[float] = None,
        prazo_dias: Optional[int] = None,
        source: str = "frontend_mvp",
    ) -> dict:
        """Cria nova operação com status 'pending'."""
        data = {
            "cnpj": cnpj,
            "status": "pending",
            "source": source,
        }
        if valor_solicitado:
            data["valor_solicitado"] = valor_solicitado
        if contrato_id:
            data["contrato_id"] = contrato_id
        if contrato_saldo:
            data["contrato_saldo"] = contrato_saldo
        if prazo_dias:
            data["prazo_dias"] = prazo_dias

        result = supabase.table("operations").insert(data).execute()
        operation = result.data[0]

        # Cria snapshots pendentes para cada componente ativo
        await self._init_snapshots(operation["id"])

        logger.info("operation.created", operation_id=operation["id"], cnpj=cnpj)
        return operation

    async def _init_snapshots(self, operation_id: str):
        """Cria registros de snapshot pendente para cada componente habilitado."""
        configs_query = supabase.table("component_config")\
            .select("component")\
            .eq("enabled", True)
        configs = await _safe_execute(configs_query)

        snapshots = [
            {
                "operation_id": operation_id,
                "component": c["component"],
                "status": "pending",
            }
            for c in configs.data
        ]

        if snapshots:
            insert_query = supabase.table("component_snapshots").insert(snapshots)
            await _safe_execute(insert_query)

    async def get_with_snapshots(self, operation_id: str) -> Optional[dict]:
        """Retorna operação com todos os snapshots de componentes."""
        operation_query = supabase.table("operations")\
            .select("*")\
            .eq("id", operation_id)\
            .maybe_single()
        result = await _safe_execute(operation_query)

        if not result or not result.data:
            return None

        operation = result.data

        snapshots_query = supabase.table("component_snapshots")\
            .select("component, status, score_contrib, duration_ms, error_message, completed_at, parsed_result")\
            .eq("operation_id", operation_id)
        snapshots = await _safe_execute(snapshots_query)

        operation["components"] = snapshots.data
        return operation

    async def list(
        self,
        status: Optional[str] = None,
        cnpj: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Lista operações com filtros opcionais."""
        query = supabase.table("operations")\
            .select("id, cnpj, razao_social, status, rating, score, taxa_sugerida, source, created_at")\
            .order("created_at", desc=True)\
            .range(offset, offset + limit - 1)

        if status:
            query = query.eq("status", status)
        if cnpj:
            query = query.eq("cnpj", cnpj)

        result = query.execute()
        return {"items": result.data, "total": len(result.data), "limit": limit, "offset": offset}

    async def update_status(self, operation_id: str, status: str, **kwargs):
        """Atualiza status e campos opcionais da operação."""
        data = {"status": status, **kwargs}
        if status == "completed":
            data["completed_at"] = datetime.now(timezone.utc).isoformat()

        supabase.table("operations")\
            .update(data)\
            .eq("id", operation_id)\
            .execute()

    async def get_cnpj(self, operation_id: str) -> Optional[str]:
        """Retorna apenas o CNPJ de uma operação."""
        result = supabase.table("operations")\
            .select("cnpj")\
            .eq("id", operation_id)\
            .maybe_single()\
            .execute()
        if not result or not result.data:
            return None
        return result.data["cnpj"]
