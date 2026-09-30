"""
OperationService: CRUD de operações de crédito.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from datetime import datetime, timezone
from httpx import ConnectError, RemoteProtocolError

from app.core.database import supabase
import structlog

logger = structlog.get_logger()


def _format_brl(value: Any) -> str:
    """Formato compacto para os motivos que expõem limites configuráveis."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    if amount >= 1_000_000 and amount % 1_000_000 == 0:
        return f"R$ {amount / 1_000_000:g} mi"
    if amount >= 1_000 and amount % 1_000 == 0:
        return f"R$ {amount / 1_000:g} mil"
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mapear_motivos_funil(
    estagio_motivo: str | None,
    parametros: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """Converte os códigos persistidos do funil em motivos próprios para a UI.

    A ingestão e a qualificação continuam gravando códigos estáveis. Esta é a
    única fronteira que os transforma em texto, inclusive para não quebrar a
    listagem quando surgir um código mais novo que a aplicação.
    """
    if not estagio_motivo:
        return []

    params = parametros or {}
    ticket_minimo = _format_brl(params.get("ticket_minimo"))
    ticket_maximo = _format_brl(params.get("ticket_maximo"))
    dias_expiracao = int(params.get("dias_minimos_expiracao") or 0)
    cobertura_min = float(params.get("funil_cobertura_min") or 0)
    historico_min = int(params.get("funil_hist_min_meses") or 0)
    orgaos_min = int(params.get("funil_orgaos_min") or 0)

    motivos: list[dict[str, str]] = []
    for raw_code in estagio_motivo.split(";"):
        codigo = raw_code.strip()
        if not codigo:
            continue
        tipo = "indisponibilidade" if codigo.startswith("indisponibilidade_fonte:") else "criterio"
        detalhe = ""
        rotulo = ""

        if codigo == "abaixo_ticket_minimo":
            rotulo = f"Abaixo do ticket mínimo ({ticket_minimo})"
            detalhe = f"Valor enquadrado abaixo do mínimo configurado de {ticket_minimo}."
        elif codigo == "acima_ticket_maximo":
            rotulo = f"Acima do ticket máximo ({ticket_maximo})"
            detalhe = f"Valor enquadrado acima do máximo configurado de {ticket_maximo}."
        elif codigo.startswith("tipo_nao_elegivel:"):
            tipo_cotacao = codigo.split(":", 1)[1]
            rotulo = "Cotação de empenho" if tipo_cotacao.upper() == "EMPENHO" else f"Tipo não elegível: {tipo_cotacao}"
            detalhe = "O tipo da cotação não pode seguir para análise."
        elif codigo == "janela_expiracao_insuficiente":
            rotulo = f"Janela de expiração insuficiente (mínimo {dias_expiracao} dias)"
            detalhe = "A cotação expira antes da janela mínima configurada."
        elif codigo.startswith("prazo_vincendo_insuficiente:"):
            prazo = codigo.split(":", 1)[1]
            rotulo = f"Prazo vincendo insuficiente ({prazo})"
            detalhe = "O prazo de vencimento não atende ao mínimo do funil."
        elif codigo.startswith("cobertura_insuficiente:"):
            cobertura = codigo.split(":", 1)[1].replace(".", ",")
            minimo = f"{cobertura_min:g}".replace(".", ",")
            rotulo = f"Cobertura {cobertura}x (mínimo {minimo}x)"
            detalhe = "Recebimentos acumulados insuficientes para o valor enquadrado."
        elif codigo.startswith("historico_recebimentos_insuficiente:"):
            meses = codigo.split(":", 1)[1]
            rotulo = f"Histórico de recebimentos: {meses} (mínimo {historico_min}m)"
            detalhe = "O histórico de recebimentos é menor que o mínimo configurado."
        elif codigo.startswith("orgaos_pagadores_insuficientes:"):
            quantidade = codigo.split(":", 1)[1]
            rotulo = f"Órgãos pagadores: {quantidade} (mínimo {orgaos_min})"
            detalhe = "Há menos órgãos pagadores que o mínimo configurado."
        elif codigo == "contrato_comprasnet_nao_encontrado":
            rotulo = "Contrato no Comprasnet não encontrado"
            detalhe = "Não foi localizado um contrato elegível no Comprasnet."
        elif codigo.startswith("indisponibilidade_fonte:"):
            fonte = codigo.split(":", 1)[1].replace("_", " ")
            rotulo = f"Sanções não verificadas (fonte indisponível: {fonte.upper()})"
            detalhe = "A fonte necessária para a verificação ficou indisponível."
        else:
            rotulo = f"Critério não atendido: {codigo.replace('_', ' ')}"
            detalhe = "Motivo recebido do funil sem rótulo específico."

        motivos.append({
            "codigo": codigo,
            "rotulo": rotulo,
            "detalhe": detalhe,
            "tipo": tipo,
        })
    return motivos


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
        uasg: Optional[str] = None,
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
        if uasg is not None:
            data["uasg"] = uasg
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

    def _funnel_summary(self) -> dict[str, Any]:
        """Resumo global do funil, sem depender da aba paginada."""
        estagios = self._stage_counts()
        try:
            quotes_result = (
                supabase.table("cotacoes_broadfactor")
                .select("operation_id")
                .eq("ambiente", "PRODUCAO")
                .execute()
            )
            operation_ids = [
                row["operation_id"]
                for row in quotes_result.data or []
                if row.get("operation_id")
            ]
            if not operation_ids:
                raise ValueError("no funnel operations")
            result = (
                supabase.table("component_snapshots")
                .select("operation_id")
                .in_("operation_id", operation_ids)
                .eq("component", "score_engine")
                .eq("status", "completed")
                .execute()
            )
            operation_ids = {
                str(row.get("operation_id"))
                for row in result.data or []
                if row.get("operation_id")
            }
        except ValueError:
            operation_ids = set()
        except Exception as exc:
            logger.warning("operation.funnel_report_count_unavailable", error=str(exc))
            operation_ids = set()
        return {
            "total_fila": sum(estagios.values()),
            "estagios": estagios,
            "relatorios_gerados": len(operation_ids),
        }

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
                "cotacao_id,cnpj,nome_fornecedor,valor_solicitado,margem_disponivel,"
                "saldo_vincendo,valor_enquadrado,tipo,data_expiracao,"
                "operation_id,estagio,estagio_motivo,n_documentos,tipos_documento,"
                "estagio_max,estagio_atualizado_em,created_at",
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
        score_completed_ids: set[str] = set()
        if operation_ids:
            op_result = supabase.table("operations")\
                .select("id,status,rating,score,taxa_sugerida,source,created_at,razao_social")\
                .in_("id", operation_ids)\
                .execute()
            operations = {str(row["id"]): row for row in op_result.data or []}
            snapshot_result = supabase.table("component_snapshots")\
                .select("operation_id")\
                .in_("operation_id", operation_ids)\
                .eq("component", "score_engine")\
                .eq("status", "completed")\
                .execute()
            score_completed_ids = {
                str(row.get("operation_id"))
                for row in snapshot_result.data or []
                if row.get("operation_id")
            }

        from app.services.eligibility_params_service import get_eligibility_config
        try:
            parametros = get_eligibility_config()
        except Exception as exc:
            logger.warning("operation.funnel_reason_parameters_unavailable", error=str(exc))
            parametros = {}

        items = []
        for quote in quotes:
            op = operations.get(str(quote.get("operation_id"))) or {}
            operation_id = op.get("id")
            relatorio = None
            if operation_id and str(operation_id) in score_completed_ids:
                relatorio = {
                    "gerado": True,
                    "rating": op.get("rating"),
                    "score": op.get("score"),
                    "taxa_sugerida": op.get("taxa_sugerida"),
                    "operation_id": operation_id,
                }
            items.append({
                "id": operation_id or quote["cotacao_id"],
                "operation_id": operation_id,
                "cotacao_id": quote["cotacao_id"],
                "cnpj": quote.get("cnpj"),
                "razao_social": op.get("razao_social") or quote.get("nome_fornecedor"),
                "source": op.get("source") or "broadfactor_ingestao",
                "created_at": op.get("created_at") or quote.get("created_at"),
                "valor_solicitado": quote.get("valor_solicitado"),
                "margem_disponivel": quote.get("margem_disponivel"),
                "saldo_vincendo": quote.get("saldo_vincendo"),
                "valor_enquadrado": quote.get("valor_enquadrado"),
                "tipo": quote.get("tipo"),
                "data_expiracao": quote.get("data_expiracao"),
                "estagio": quote.get("estagio"),
                "estagio_max": quote.get("estagio_max"),
                "estagio_atualizado_em": quote.get("estagio_atualizado_em"),
                "estagio_motivo": quote.get("estagio_motivo"),
                "motivos": mapear_motivos_funil(quote.get("estagio_motivo"), parametros),
                "n_documentos": quote.get("n_documentos"),
                "tipos_documento": quote.get("tipos_documento"),
                "relatorio": relatorio,
            })

        total = result.count if result.count is not None else len(items)
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            **self._funnel_summary(),
        }

    async def list_manual(
        self,
        *,
        incluir_testes: bool = False,
        cnpj: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Lista análises iniciadas pela tela administrativa, fora do funil."""
        query = (
            supabase.table("operations")
            .select(
                "id,cnpj,razao_social,status,rating,score,taxa_sugerida,"
                "valor_solicitado,created_at,source,ambiente",
                count="exact",
            )
            .eq("source", "admin_ui")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if not incluir_testes:
            query = query.eq("ambiente", "PRODUCAO")
        if cnpj:
            query = query.eq("cnpj", cnpj)
        result = query.execute()
        items = result.data or []
        total = result.count if result.count is not None else len(items)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

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
