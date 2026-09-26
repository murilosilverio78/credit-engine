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
        origem_dados: str,
        cotacao_id: Optional[str] = None,
        valor_solicitado: Optional[float] = None,
        valor_enquadrado: Optional[float] = None,
        saldo_vincendo: Optional[float] = None,
        contrato_id: Optional[str] = None,
        contrato_saldo: Optional[float] = None,
        margem_disponivel: Optional[float] = None,
        prazo_dias: Optional[int] = None,
        prazo_vincendo_meses: Optional[int] = None,
        prazo_final_meses: Optional[int] = None,
        prazo_vincendo_indisponivel: bool = False,
        source: str = "frontend_mvp",
    ) -> dict:
        """Cria nova operação com status 'pending'."""
        data = {
            "cnpj": cnpj,
            "origem_dados": origem_dados,
            "status": "pending",
            "source": source,
            "prazo_vincendo_indisponivel": prazo_vincendo_indisponivel,
        }
        if cotacao_id is not None:
            data["cotacao_id"] = cotacao_id
        if valor_solicitado is not None:
            data["valor_solicitado"] = valor_solicitado
        if valor_enquadrado is not None:
            data["valor_enquadrado"] = valor_enquadrado
        if saldo_vincendo is not None:
            data["saldo_vincendo"] = saldo_vincendo
        if contrato_id is not None:
            data["contrato_id"] = contrato_id
        if contrato_saldo is not None:
            data["contrato_saldo"] = contrato_saldo
        if margem_disponivel is not None:
            data["margem_disponivel"] = margem_disponivel
        if prazo_dias is not None:
            data["prazo_dias"] = prazo_dias
        if prazo_vincendo_meses is not None:
            data["prazo_vincendo_meses"] = prazo_vincendo_meses
        if prazo_final_meses is not None:
            data["prazo_final_meses"] = prazo_final_meses

        result = supabase.table("operations").insert(data).execute()
        operation = result.data[0]

        # Cria snapshots pendentes para cada componente ativo
        await self._init_snapshots(operation["id"])

        try:
            from app.services.cliente_service import ClienteService

            ClienteService().vincular_operacao(operation["id"])
        except Exception as exc:
            logger.warning(
                "client.dual_write_failed",
                action="link_operation_after_create",
                source_operation_id=operation["id"],
                error=str(exc),
            )

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
        try:
            audit_query = supabase.table("audit_trail")\
                .select("previous_value,new_value,payload,created_at")\
                .eq("operation_id", operation_id)\
                .eq("action", "score_reprocessed")\
                .order("created_at", desc=True)\
                .limit(1)
            audit_result = await _safe_execute(audit_query)
            operation["score_reprocessamento"] = (
                audit_result.data[0] if audit_result.data else None
            )
        except Exception as exc:
            operation["score_reprocessamento"] = None
            logger.warning(
                "operation.score_reprocessing_audit_unavailable",
                operation_id=operation_id,
                error=str(exc),
            )
        return operation

    async def list(
        self,
        status: Optional[str] = None,
        cnpj: Optional[str] = None,
        estagio: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Lista operações com filtros opcionais."""
        if estagio:
            return await self._list_funil(estagio=estagio, cnpj=cnpj, limit=limit, offset=offset)

        query = supabase.table("operations")            .select(
                "id, cnpj, razao_social, status, rating, score, taxa_sugerida, source, created_at, cotacao_id",
                count="exact",
            )            .order("created_at", desc=True)            .range(offset, offset + limit - 1)

        if status:
            query = query.eq("status", status)
        if cnpj:
            query = query.eq("cnpj", cnpj)

        result = query.execute()
        items = result.data or []
        self._attach_quote_stage(items)
        total = result.count if result.count is not None else len(result.data)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "estagios": self._stage_counts(),
        }

    def _stage_counts(self) -> dict[str, int]:
        try:
            result = supabase.table("cotacoes_broadfactor")\
                .select("estagio")\
                .eq("ambiente", "PRODUCAO")\
                .execute()
        except Exception as exc:
            logger.warning("operation.stage_counts_unavailable", error=str(exc))
            return {}
        counts: dict[str, int] = {}
        for row in result.data or []:
            stage = row.get("estagio")
            if stage:
                counts[stage] = counts.get(stage, 0) + 1
        return counts

    def _attach_quote_stage(self, items: list[dict]) -> None:
        operation_ids = [item["id"] for item in items if item.get("id")]
        if not operation_ids:
            return
        try:
            result = supabase.table("cotacoes_broadfactor")\
                .select("operation_id,cotacao_id,estagio,estagio_motivo,n_documentos,tipos_documento")\
                .in_("operation_id", operation_ids)\
                .execute()
        except Exception as exc:
            logger.warning("operation.quote_stage_unavailable", error=str(exc))
            return
        by_operation = {
            str(row.get("operation_id")): row
            for row in result.data or []
            if row.get("operation_id")
        }
        for item in items:
            quote = by_operation.get(str(item.get("id")))
            if quote:
                item.update({
                    "cotacao_id": quote.get("cotacao_id"),
                    "estagio": quote.get("estagio"),
                    "estagio_motivo": quote.get("estagio_motivo"),
                    "n_documentos": quote.get("n_documentos"),
                    "tipos_documento": quote.get("tipos_documento"),
                    "operation_id": item.get("id"),
                })

    async def _list_funil(
        self,
        *,
        estagio: str,
        cnpj: Optional[str],
        limit: int,
        offset: int,
    ) -> dict:
        query = supabase.table("cotacoes_broadfactor")\
            .select(
                "cotacao_id,cnpj,nome_fornecedor,valor_solicitado,valor_enquadrado,"
                "operation_id,estagio,estagio_motivo,n_documentos,tipos_documento,"
                "estagio_atualizado_em,created_at",
                count="exact",
            )\
            .eq("ambiente", "PRODUCAO")\
            .eq("estagio", estagio)\
            .order("estagio_atualizado_em", desc=True)\
            .range(offset, offset + limit - 1)
        if cnpj:
            query = query.eq("cnpj", cnpj)
        result = query.execute()
        quotes = result.data or []
        operation_ids = [row["operation_id"] for row in quotes if row.get("operation_id")]
        operations: dict[str, dict] = {}
        if operation_ids:
            op_result = supabase.table("operations")\
                .select("id,status,rating,score,taxa_sugerida,source,created_at,razao_social")\
                .in_("id", operation_ids)\
                .execute()
            operations = {str(row["id"]): row for row in op_result.data or []}

        items = []
        for quote in quotes:
            op = operations.get(str(quote.get("operation_id"))) or {}
            items.append({
                "id": op.get("id") or quote["cotacao_id"],
                "operation_id": op.get("id"),
                "cotacao_id": quote["cotacao_id"],
                "cnpj": quote.get("cnpj"),
                "razao_social": op.get("razao_social") or quote.get("nome_fornecedor"),
                "status": op.get("status") or "pending",
                "rating": op.get("rating"),
                "score": op.get("score"),
                "taxa_sugerida": op.get("taxa_sugerida"),
                "source": op.get("source") or "broadfactor_ingestao",
                "created_at": op.get("created_at") or quote.get("created_at"),
                "valor_solicitado": quote.get("valor_solicitado"),
                "valor_enquadrado": quote.get("valor_enquadrado"),
                "estagio": quote.get("estagio"),
                "estagio_motivo": quote.get("estagio_motivo"),
                "n_documentos": quote.get("n_documentos"),
                "tipos_documento": quote.get("tipos_documento"),
            })

        total = result.count if result.count is not None else len(items)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "estagios": self._stage_counts(),
        }

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
