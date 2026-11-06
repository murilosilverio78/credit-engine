"""Read-only API for the quote-stage detail screen."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.database import supabase
from app.services.cotacao_resumo.motivos import divergencia_pncp_comprasnet, traduzir_motivos
from app.workers.tasks.score_engine import _component_list, _is_active_record

router = APIRouter()

_SEVERITY = {"EM_ANDAMENTO": 0, "COMERCIAL": 1, "CLIENTE": 2, "REVISAR_DADO": 3, "CREDITO": 4, "VETO": 5, "ENCERRADA": 6}


def _params() -> dict[str, Any]:
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        return get_eligibility_config()
    except Exception:
        return {}


def _format_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _consultas(snapshots: dict[str, Any], statuses: dict[str, str], stage: str, cotacao: dict[str, Any]) -> dict[str, Any]:
    def item(name: str, component: str, result: str, tom: str) -> dict[str, str]:
        return {"nome": name, "coleta": "ok" if statuses.get(component) == "completed" else "não executada", "resultado": result if statuses.get(component) == "completed" else "não executada", "tom": tom if statuses.get(component) == "completed" else "neutro"}

    if stage in {"LISTA_ESPERA", "ENQUADRADA"}:
        motivo = cotacao.get("estagio_motivo") or ""
        tipo_invalido = "tipo_nao_elegivel" in motivo or "cedente_nao_pj" in motivo
        abaixo_minimo = "abaixo_ticket_minimo" in motivo
        acima_maximo = "acima_ticket_maximo" in motivo
        rows = [
            {"nome": "Tipo de recebível", "coleta": "regra", "resultado": str(cotacao.get("tipo") or "—"), "tom": "ruim" if tipo_invalido else "bom"},
            {"nome": "Valor", "coleta": "regra", "resultado": "abaixo do ticket mínimo" if abaixo_minimo else "acima do ticket máximo" if acima_maximo else "dentro do ticket", "tom": "ruim" if abaixo_minimo else "atencao" if acima_maximo else "bom"},
        ]
        if stage == "ENQUADRADA":
            rows.append({"nome": "Documentos Broadfactor", "coleta": "regra", "resultado": f"{cotacao.get('n_documentos') or 0} documentos", "tom": "atencao" if not cotacao.get("n_documentos") else "bom"})
        return {
            "titulo": "REGRAS APLICADAS (SEM CUSTO)",
            "itens": rows,
            "nao_executados": "Ainda não executadas: consultas gratuitas (cadastro, sanções, contratos, pagamentos) e análise paga.",
        }

    brasil = snapshots.get("brasil_api") or {}
    pessoa = snapshots.get("pessoa_juridica") or {}
    rows = [item("Cadastro (BrasilAPI)", "brasil_api", str(brasil.get("situacao_cadastral") or "—"), "bom" if str(brasil.get("situacao_cadastral") or "").upper() == "ATIVA" else "atencao")]
    if statuses.get("pessoa_juridica") == "completed":
        sanction = bool(pessoa.get("possui_sancao"))
        rows.append(item("Resumo de sanções (Portal)", "pessoa_juridica", "sanção sinalizada" if sanction else "nada consta", "ruim" if sanction else "bom"))
    for component, name, count_key in (("ceis", "CEIS", "total_registros"), ("cnep", "CNEP", "total_registros"), ("cepim", "CEPIM", "total_registros"), ("acordos_leniencia", "Acordos de leniência", "total_acordos")):
        result = snapshots.get(component) or {}
        records = _component_list(result)
        active_count = sum(1 for record in records if isinstance(record, dict) and _is_active_record(record))
        count = int(result.get(count_key) or len(records) or 0)
        if active_count:
            result_text = f"{active_count} {'acordo ativo' if component == 'acordos_leniencia' else 'sanção ativa'}"
            tone = "ruim"
        elif count:
            result_text, tone = "nada consta (registros vencidos)", "bom"
        else:
            result_text, tone = "nada consta", "bom"
        rows.append(item(name, component, result_text, tone))
    pncp = snapshots.get("contratos_pncp") or {}
    pncp_result = "localizado" if pncp.get("contrato_cedido") else "não localizado"
    rows.append(item("Contrato no PNCP", "contratos_pncp", pncp_result, "bom" if pncp_result == "localizado" else "atencao"))
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    contract = comprasnet.get("contrato_comprasnet") or {}
    months = contract.get("prazo_vincendo_meses")
    comprasnet_result = f"{months} meses vincendos" if months is not None else ("localizado" if comprasnet.get("status_consulta") == "ENCONTRADO" else "não localizado")
    divergent = divergencia_pncp_comprasnet(snapshots)["divergente"]
    rows.append(item("Contrato no Comprasnet", "contratos_comprasnet", comprasnet_result, "atencao" if divergent else "bom" if comprasnet.get("status_consulta") == "ENCONTRADO" else "atencao"))
    payments = snapshots.get("pagamentos_portal") or {}
    orders = payments.get("ordens_bancarias") or []
    rows.append(item("Pagamentos (Portal)", "pagamentos_portal", "nada consta" if payments.get("status_consulta") == "ENCONTRADO" and not orders else ("informativo" if payments.get("status_consulta") == "ENCONTRADO" else "não aplicável"), "bom" if payments.get("status_consulta") == "ENCONTRADO" and not orders else "neutro"))
    if stage == "ENCERRADA":
        title, missing = "CONSULTAS EXECUTADAS", "Cotação encerrada: nenhuma consulta nova será executada."
    else:
        title = "CONSULTAS EXECUTADAS"
        missing = "Não executados porque a cotação parou na triagem: score, pesquisa reputacional, extração do contrato, documentos financeiros, CNDT e FGTS. Nenhum custo de análise paga foi gerado."
    return {"titulo": title, "itens": rows, "nao_executados": missing}


def montar_resumo(cotacao: dict[str, Any], operation: dict[str, Any] | None, snapshots: dict[str, Any], statuses: dict[str, str], *, parametros: dict[str, Any] | None = None) -> dict[str, Any]:
    motivos = traduzir_motivos(cotacao.get("estagio_motivo"), snapshots, cotacao=cotacao, parametros=parametros if parametros is not None else _params())
    classe = max((item["classe"] for item in motivos), key=lambda item: _SEVERITY[item], default="EM_ANDAMENTO")
    stage = cotacao.get("estagio") or "LISTA_ESPERA"
    if classe == "EM_ANDAMENTO" and stage == "ENCERRADA":
        classe = "ENCERRADA"
    first = max(motivos, key=lambda item: _SEVERITY[item["classe"]]) if motivos else None
    status = (operation or {}).get("status")
    aba = "REPROVADAS" if status == "reprovada_triagem" else "COTACOES_ENCERRADAS" if status == "cotacao_encerrada" else stage
    expires = _format_date(cotacao.get("data_expiracao"))
    kpis = [
        {"rotulo": "Valor pedido", "valor": cotacao.get("valor_solicitado"), "formato": "BRL", "nota": None},
        {"rotulo": "Capacidade do contrato" if operation and operation.get("capacidade_contrato") is not None else "Valor enquadrado", "valor": (operation or {}).get("capacidade_contrato", cotacao.get("valor_enquadrado")), "formato": "BRL", "nota": None},
        {"rotulo": "Expira em", "valor": expires, "formato": "DATA", "nota": None},
    ]
    actions: list[dict[str, Any]] = []
    if classe == "COMERCIAL":
        proposal = next((e["valor"] for item in motivos for e in item["evidencias"] if e["rotulo"] == "Proposta possível"), "—")
        actions = [{"id": "reenquadrar", "rotulo": f"Reenquadrar em {proposal}", "nota": "Leva para Enquadrada e registra na trilha de auditoria.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}, {"id": "arquivar", "rotulo": "Arquivar", "nota": "Tira da fila sem contato com o cliente.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    elif classe == "CLIENTE":
        actions = [{"id": "pedir_documentos", "rotulo": "Pedir documentos ao cliente", "nota": "Abre mensagem pronta para WhatsApp.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}, {"id": "consultas_gratuitas", "rotulo": "Rodar consultas gratuitas mesmo assim", "nota": "Libera seguir sem documentos.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    elif classe in {"CREDITO", "VETO", "REVISAR_DADO"}:
        veto = any(item["classe"] == "VETO" for item in motivos)
        actions = [{"id": "recoletar", "rotulo": "Recoletar contrato", "nota": "Corrige o dado de vigência. Não muda a decisão.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Bloqueado: cotação sem operação", "exige_justificativa": False}, {"id": "enviar_analise_completa", "rotulo": "Enviar para análise completa", "nota": "Dispara a análise paga com justificativa.", "habilitada": not veto, "motivo_bloqueio": "Bloqueado: existe veto ativo" if veto else None, "exige_justificativa": True}]
    elif classe == "ENCERRADA":
        actions = [{"id": "abrir_analise", "rotulo": "Abrir análise", "nota": "Consultas e relatório feitos antes do encerramento.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Não há análise vinculada", "exige_justificativa": False}, {"id": "avisar_comercial", "rotulo": "Avisar comercial", "nota": "Para entender se o cliente fechou com outro financiador.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    else:
        actions = [{"id": "gerar_relatorio", "rotulo": "Gerar relatório", "nota": "Dispara a análise paga.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Não há análise vinculada", "exige_justificativa": False}]
    labels = {"VETO": "Reprovada na triagem", "CREDITO": "Reprovada na triagem", "REVISAR_DADO": "Revisar dado", "CLIENTE": "Parada: sem documentos", "COMERCIAL": "Parada: valor acima do ticket", "EM_ANDAMENTO": "Pronta para análise", "ENCERRADA": "Encerrada"}
    contract_number = (operation or {}).get("contrato_id") or cotacao.get("contrato_id")
    uasg = (operation or {}).get("uasg") or ((snapshots.get("contratos_comprasnet") or {}).get("contrato_comprasnet") or {}).get("uasg")
    contract_label = f"{contract_number} · UASG {uasg}" if contract_number and uasg else contract_number
    resumo = first["texto"] if first else "Nenhum motivo de parada. O próximo passo é a análise completa."
    if len(motivos) > 1 and first:
        resumo = f"{len(motivos)} motivos. {first['titulo']} prevalece. {first['texto']}"
    return {"cotacao_id": cotacao.get("cotacao_id"), "operation_id": cotacao.get("operation_id"), "cnpj": cotacao.get("cnpj"), "razao_social": cotacao.get("nome_fornecedor") or (operation or {}).get("razao_social"), "contrato": contract_label, "aba_origem": aba, "selo": {"texto": labels[classe], "classe": classe}, "kpis": kpis, "trilha": {"estagio_atual": stage, "estagio_max": cotacao.get("estagio_max"), "encerrada": classe == "ENCERRADA"}, "parada": {"classe": classe, "parou_em": cotacao.get("estagio_atualizado_em"), "titulo": first["titulo"] if first else "Passou em todos os filtros gratuitos", "resumo": resumo}, "motivos": motivos, "acoes": actions, "consultas": _consultas(snapshots, statuses, stage, cotacao)}


@router.get("/{cotacao_id}/resumo")
async def get_cotacao_resumo(cotacao_id: str):
    quote_result = supabase.table("cotacoes_broadfactor").select("*").eq("cotacao_id", cotacao_id).maybe_single().execute()
    cotacao = quote_result.data or {}
    if not cotacao:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    operation_id = cotacao.get("operation_id")
    operation: dict[str, Any] | None = None
    snapshots: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    if operation_id:
        operation = (supabase.table("operations").select("*").eq("id", operation_id).maybe_single().execute().data or None)
        rows = supabase.table("component_snapshots").select("component,status,parsed_result,error_message,completed_at").eq("operation_id", operation_id).execute().data or []
        for row in rows:
            component = row.get("component")
            if component:
                statuses[component] = row.get("status") or ""
                parsed = row.get("parsed_result") or {}
                if isinstance(parsed, dict):
                    snapshots[component] = {**parsed, "error_message": row.get("error_message"), "completed_at": row.get("completed_at")}
    return montar_resumo(cotacao, operation, snapshots, statuses)
