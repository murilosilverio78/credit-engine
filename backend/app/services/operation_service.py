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
        operation["contratos_verificados"] = None
        try:
            parsed_by_component = {
                item.get("component"): item.get("parsed_result")
                for item in (snapshots.data or [])
                if isinstance(item, dict)
            }
            portal = parsed_by_component.get("contratos")
            comprasnet = parsed_by_component.get("contratos_comprasnet")
            if not isinstance(portal, dict) or not isinstance(comprasnet, dict):
                raise ValueError("snapshot_contratos_ou_comprasnet_ausente")
            from app.services.verified_contracts_service import contratos_verificados

            operation["contratos_verificados"] = contratos_verificados(
                portal,
                comprasnet,
                operation.get("cnpj") or "",
            )
        except Exception as exc:
            logger.warning(
                "operation.verified_contracts_unavailable",
                operation_id=operation_id,
                error=str(exc),
            )
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
        busca: Optional[str] = None,
        rating: Optional[str] = None,
        relatorio: Optional[str] = None,
        tipo_motivo: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Lista operações com filtros opcionais."""
        if estagio:
            return await self._list_funil(
                estagio=estagio,
                cnpj=cnpj,
                busca=busca,
                rating=rating,
                relatorio=relatorio,
                tipo_motivo=tipo_motivo,
                limit=limit,
                offset=offset,
            )

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

    def _stage_counts(self) -> dict[str, int] | None:
        """Contagens do funil calculadas pelo banco, sem limite do PostgREST."""
        try:
            result = supabase.rpc("resumo_funil_operacoes").execute()
        except Exception as exc:
            logger.warning("operation.stage_counts_unavailable", error=str(exc))
            return None
        row = (result.data or [{}])[0]
        return row.get("estagios") or {}

    def _funnel_summary(self) -> dict[str, Any]:
        """Resumo global do funil, sem depender da aba paginada."""
        try:
            result = supabase.rpc("resumo_funil_operacoes").execute()
            row = (result.data or [{}])[0]
            return {
                "total_fila": row.get("total_fila"),
                "estagios": row.get("estagios") or {},
                "relatorios_gerados": row.get("relatorios_gerados"),
            }
        except Exception as exc:
            logger.warning("operation.funnel_summary_unavailable", error=str(exc))
        return {
            "total_fila": None,
            "estagios": None,
            "relatorios_gerados": None,
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
        busca: Optional[str],
        rating: Optional[str],
        relatorio: Optional[str],
        tipo_motivo: Optional[str],
        limit: int,
        offset: int,
    ) -> dict:
        result = supabase.rpc("listar_funil_operacoes", {
            "p_estagio": estagio,
            "p_cnpj": cnpj,
            "p_busca": busca,
            "p_rating": rating,
            "p_relatorio": relatorio,
            "p_tipo_motivo": tipo_motivo,
            "p_limit": limit,
            "p_offset": offset,
        }).execute()
        quotes = result.data or []

        from app.services.eligibility_params_service import get_eligibility_config
        try:
            parametros = get_eligibility_config()
        except Exception as exc:
            logger.warning("operation.funnel_reason_parameters_unavailable", error=str(exc))
            parametros = {}

        items = []
        for quote in quotes:
            operation_id = quote.get("operation_id")
            relatorio = None
            if quote.get("relatorio_gerado"):
                relatorio = {
                    "gerado": True,
                    "rating": quote.get("rating"),
                    "score": quote.get("score"),
                    "taxa_sugerida": quote.get("taxa_sugerida"),
                    "operation_id": operation_id,
                }
            items.append({
                "id": operation_id or quote["cotacao_id"],
                "operation_id": operation_id,
                "cotacao_id": quote["cotacao_id"],
                "cnpj": quote.get("cnpj"),
                "razao_social": quote.get("razao_social") or quote.get("nome_fornecedor"),
                "source": quote.get("source") or "broadfactor_ingestao",
                "created_at": quote.get("operation_created_at") or quote.get("created_at"),
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
                "pendencias": quote.get("pendencias") or [],
                "score_flags": quote.get("score_flags") or [],
                "relatorio": relatorio,
            })

        total = int(quotes[0].get("total_count") or 0) if quotes else 0
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
        busca: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Lista análises iniciadas pela tela administrativa, fora do funil."""
        result = supabase.rpc("listar_analises_manuais", {
            "p_incluir_testes": incluir_testes,
            "p_cnpj": cnpj,
            "p_busca": busca,
            "p_limit": limit,
            "p_offset": offset,
        }).execute()
        items = result.data or []
        total = int(items[0].get("total_count") or 0) if items else 0
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
