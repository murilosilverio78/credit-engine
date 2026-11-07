"""Read-only API for the quote-stage detail screen."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.cotacao_resumo.motivos import divergencia_pncp_comprasnet, traduzir_motivos
from app.workers.tasks.score_engine import _component_list, _is_active_record

router = APIRouter()

_SEVERITY = {"EM_ANDAMENTO": 0, "COMERCIAL": 1, "CLIENTE": 2, "REVISAR_DADO": 3, "CREDITO": 4, "VETO": 5, "ENCERRADA": 6}
audit = AuditService()


class JustificativaInput(BaseModel):
    justificativa: str | None = None


class CotacaoAuditAction:
    action: Literal["quote_reframed", "quote_free_checks_requested", "quote_full_analysis_requested", "quote_archived"]


def _quote(cotacao_id: str) -> dict[str, Any]:
    result = supabase.table("cotacoes_broadfactor").select("*").eq("cotacao_id", cotacao_id).maybe_single().execute()
    quote = getattr(result, "data", None) or {}
    if not quote:
        raise HTTPException(status_code=404, detail="Cotação não encontrada")
    return quote


def _operation(operation_id: str | None) -> dict[str, Any] | None:
    if not operation_id:
        return None
    result = supabase.table("operations").select("*").eq("id", operation_id).maybe_single().execute()
    return getattr(result, "data", None) or None


def _audit(action: CotacaoAuditAction, quote: dict[str, Any], current_user: dict[str, Any], request: Request, *, payload: dict[str, Any], previous: dict[str, Any] | None = None, new: dict[str, Any] | None = None) -> None:
    data = dict(
        operation_id=quote.get("operation_id"),
        actor_id=current_user.get("id"), actor_type=current_user.get("role") or current_user.get("alcada") or "analista",
        ip_address=request.client.host if request.client else None,
        payload={"cotacao_id": quote["cotacao_id"], "alcada": current_user.get("alcada") or current_user.get("role"), **payload},
        previous_value=previous, new_value=new,
    )
    if action == "quote_reframed": audit.log(action="quote_reframed", **data)
    elif action == "quote_free_checks_requested": audit.log(action="quote_free_checks_requested", **data)
    elif action == "quote_full_analysis_requested": audit.log(action="quote_full_analysis_requested", **data)
    else: audit.log(action="quote_archived", **data)


def _missing_documents_message(cotacao: dict[str, Any], motivos: list[dict[str, Any]]) -> str:
    document_reason = next((item for item in motivos if item["codigo"] == "SEM_DOCUMENTOS_BROADFACTOR"), None)
    missing = [item["rotulo"] for item in (document_reason or {}).get("evidencias", []) if item.get("valor") == "não recebido"]
    docs = ", ".join(missing) or "os documentos da análise"
    return f"Olá! Para seguirmos com sua cotação, precisamos de: {docs}. O valor enquadrado é {_brl(cotacao.get('valor_enquadrado'))} e a cotação expira em {_format_date(cotacao.get('data_expiracao')) or 'data não informada'}. Quando puder, envie por aqui. Obrigado!\n\nEquipe AntecipaGov"


def _sanction_refusal_message(motivos: list[dict[str, Any]]) -> str:
    sanction = next((item for item in motivos if item["classe"] == "VETO" and item["codigo"].startswith("SANCAO_")), None)
    evidence = {item["rotulo"]: item["valor"] for item in (sanction or {}).get("evidencias", [])}
    return f"Olá! Identificamos uma sanção ativa vinculada a esta empresa: órgão {evidence.get('Órgão sancionador', 'não informado')}; tipo {evidence.get('Tipo', 'não informado')}; processo {evidence.get('Processo e publicação', 'não informado')}; vigência {evidence.get('Vigência', 'não informada')}. Por isso não conseguimos seguir com esta cotação agora. Se a sanção for suspensa, a empresa pode nos avisar para reavaliarmos.\n\nEquipe AntecipaGov"

def _params() -> dict[str, Any]:
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        return get_eligibility_config()
    except Exception:
        return {}


def _brl(value: Any) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


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
    is_expired = "cotacao_expirada" in (cotacao.get("estagio_motivo") or "")
    kpis = [
        {"rotulo": "Valor pedido", "valor": cotacao.get("valor_solicitado"), "formato": "BRL", "nota": None},
        {"rotulo": "Capacidade do contrato" if operation and operation.get("capacidade_contrato") is not None else "Valor enquadrado", "valor": (operation or {}).get("capacidade_contrato", cotacao.get("valor_enquadrado")), "formato": "BRL", "nota": None},
        {"rotulo": "Expiraria em" if classe == "ENCERRADA" else "Expira em", "valor": expires, "formato": "DATA", "nota": "expirou" if classe == "ENCERRADA" and is_expired else "saiu antes do prazo" if classe == "ENCERRADA" else None},
    ]
    actions: list[dict[str, Any]] = []
    if classe == "COMERCIAL":
        proposal = next((e["valor"] for item in motivos for e in item["evidencias"] if e["rotulo"] == "Proposta possível"), "—")
        actions = [{"id": "reenquadrar", "rotulo": f"Reenquadrar em {proposal}", "nota": "Leva para Enquadrada e registra na trilha de auditoria.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}, {"id": "arquivar", "rotulo": "Arquivar", "nota": "Tira da fila sem contato com o cliente.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    elif classe == "CLIENTE":
        actions = [{"id": "pedir_documentos", "rotulo": "Pedir documentos ao cliente", "nota": "Abre mensagem pronta para WhatsApp.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False, "mensagem": _missing_documents_message(cotacao, motivos)}, {"id": "consultas_gratuitas", "rotulo": "Rodar consultas gratuitas mesmo assim", "nota": "Libera seguir sem documentos.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    elif classe in {"CREDITO", "VETO", "REVISAR_DADO"}:
        veto = any(item["classe"] == "VETO" for item in motivos)
        actions = [{"id": "recoletar", "rotulo": "Recoletar contrato", "nota": "Corrige o dado de vigência. Não muda a decisão.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Bloqueado: cotação sem operação", "exige_justificativa": False}, {"id": "enviar_analise_completa", "rotulo": "Enviar para análise completa", "nota": "Dispara a análise paga com justificativa.", "habilitada": not veto, "motivo_bloqueio": "Bloqueado: existe veto ativo" if veto else None, "exige_justificativa": True}]
        if veto and any(item["codigo"].startswith("SANCAO_") for item in motivos):
            actions.append({"id": "copiar_mensagem_recusa", "rotulo": "Copiar mensagem de recusa", "nota": "Mensagem pronta para o cliente.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False, "mensagem": _sanction_refusal_message(motivos)})
    elif classe == "ENCERRADA":
        actions = [{"id": "abrir_analise", "rotulo": "Abrir análise", "nota": "Consultas e relatório feitos antes do encerramento.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Não há análise vinculada", "exige_justificativa": False}, {"id": "avisar_comercial", "rotulo": "Avisar comercial", "nota": "Para entender se o cliente fechou com outro financiador.", "habilitada": True, "motivo_bloqueio": None, "exige_justificativa": False}]
    else:
        actions = [{"id": "gerar_relatorio", "rotulo": "Gerar relatório", "nota": "Dispara a análise paga.", "habilitada": bool(operation), "motivo_bloqueio": None if operation else "Não há análise vinculada", "exige_justificativa": False}]
    labels = {"VETO": "Reprovada na triagem", "CREDITO": "Reprovada na triagem", "REVISAR_DADO": "Revisar dado", "CLIENTE": "Parada: sem documentos", "COMERCIAL": "Parada: valor acima do ticket", "EM_ANDAMENTO": "Pronta para análise", "ENCERRADA": "Encerrada"}
    contract_number = (operation or {}).get("contrato_id") or cotacao.get("contrato_id")
    uasg = (operation or {}).get("uasg") or ((snapshots.get("contratos_comprasnet") or {}).get("contrato_comprasnet") or {}).get("uasg")
    contract_label = f"{contract_number} · UASG {uasg}" if contract_number and uasg else contract_number
    proposal = next((e["valor"] for item in motivos for e in item["evidencias"] if e["rotulo"] == "Proposta possível"), "—")
    sanction_end = next((e["valor"].split(" a ")[-1].split(" ·")[0] for item in motivos for e in item["evidencias"] if e["rotulo"] == "Vigência" and " a " in e["valor"]), None)
    summaries = {
        "VETO": f"Há um veto definitivo: nenhuma ação libera a operação{' antes de ' + sanction_end if sanction_end else ''}.",
        "CREDITO": "Reprovada por critério de crédito. Pode seguir para a análise completa com justificativa.",
        "REVISAR_DADO": "Um dado precisa ser revisto antes de decidir.",
        "COMERCIAL": f"Nenhuma consulta foi feita ainda. Existe proposta possível de {proposal}.",
        "CLIENTE": "Isso não indica problema na empresa: a Broadfactor só coleta documentos quando o cliente pede.",
        "EM_ANDAMENTO": "Nenhum motivo de parada. O próximo passo é a análise completa, que tem custo de modelo e só roda quando você manda.",
        "ENCERRADA": "Somente leitura. A cotação não receberá novas consultas.",
    }
    resumo = summaries[classe]
    if len(motivos) > 1 and first:
        resumo = f"{len(motivos)} motivos; {first['titulo']} prevalece. {resumo}"
    return {"cotacao_id": cotacao.get("cotacao_id"), "operation_id": cotacao.get("operation_id"), "cnpj": cotacao.get("cnpj"), "razao_social": cotacao.get("nome_fornecedor") or (operation or {}).get("razao_social"), "contrato": contract_label, "aba_origem": aba, "selo": {"texto": labels[classe], "classe": classe}, "kpis": kpis, "trilha": {"estagio_atual": stage, "estagio_max": cotacao.get("estagio_max"), "encerrada": classe == "ENCERRADA"}, "parada": {"classe": classe, "parou_em": cotacao.get("estagio_atualizado_em"), "titulo": first["titulo"] if first else "Passou em todos os filtros gratuitos", "resumo": resumo}, "motivos": motivos, "acoes": actions, "consultas": _consultas(snapshots, statuses, stage, cotacao)}


@router.post("/{cotacao_id}/reenquadrar")
async def reenquadrar_cotacao(cotacao_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    cotacao = _quote(cotacao_id)
    motivo = cotacao.get("estagio_motivo") or ""
    if "acima_ticket_maximo" not in motivo:
        if cotacao.get("estagio") == "ENQUADRADA":
            return {"cotacao_id": cotacao_id, "status": "already_reframed", "idempotent": True}
        raise HTTPException(status_code=409, detail="Reenquadramento só é permitido para valor acima do ticket máximo")
    maximum = _params().get("ticket_maximo")
    if maximum is None:
        raise HTTPException(status_code=409, detail="Ticket máximo indisponível")
    before = cotacao.get("valor_enquadrado")
    after = min(float(before or maximum), float(maximum))
    from app.workers.tasks.broadfactor_ingestao import _update_quote_stage
    _update_quote_stage(supabase, cotacao_id, "ENQUADRADA", motivo="sem_documentos_broadfactor", operation_id=cotacao.get("operation_id"), n_documentos=cotacao.get("n_documentos") or 0, estagio_max=cotacao.get("estagio_max"))
    supabase.table("cotacoes_broadfactor").update({"valor_enquadrado": after}).eq("cotacao_id", cotacao_id).execute()
    _audit("quote_reframed", cotacao, current_user, request, payload={"motivo": "acima_ticket_maximo"}, previous={"valor_enquadrado": before}, new={"valor_enquadrado": after})
    return {"cotacao_id": cotacao_id, "valor_enquadrado": after, "status": "reframed"}


@router.post("/{cotacao_id}/consultas-gratuitas", status_code=202)
async def solicitar_consultas_gratuitas(cotacao_id: str, background_tasks: BackgroundTasks, request: Request, current_user: dict = Depends(get_current_user)):
    cotacao = _quote(cotacao_id)
    if cotacao.get("operation_id"):
        return {"cotacao_id": cotacao_id, "operation_id": cotacao["operation_id"], "status": "accepted", "idempotent": True}
    if cotacao.get("estagio") != "ENQUADRADA" or "sem_documentos_broadfactor" not in (cotacao.get("estagio_motivo") or ""):
        raise HTTPException(status_code=409, detail="Consultas gratuitas só podem ser solicitadas para cotação sem documentos")
    from app.services.operation_service import OperationService
    operation = await OperationService().create(cnpj=str(cotacao.get("cnpj") or ""), origem_dados="API_BROADFACTOR", cotacao_id=cotacao_id, valor_solicitado=cotacao.get("valor_solicitado"), valor_enquadrado=cotacao.get("valor_enquadrado"), saldo_vincendo=cotacao.get("saldo_vincendo"), contrato_id=cotacao.get("contrato_id"), source="cotacao_acoes")
    supabase.table("cotacoes_broadfactor").update({"operation_id": operation["id"], "estagio": "DOCUMENTADA", "estagio_atualizado_em": datetime.now(timezone.utc).isoformat()}).eq("cotacao_id", cotacao_id).execute()
    _audit("quote_free_checks_requested", {**cotacao, "operation_id": operation["id"]}, current_user, request, payload={"ate_fase": 2})
    from app.workers.tasks.orchestrator import start_analysis
    background_tasks.add_task(start_analysis, str(operation["id"]), ate_fase=2)
    return {"cotacao_id": cotacao_id, "operation_id": operation["id"], "status": "accepted"}


@router.post("/{cotacao_id}/analise-completa", status_code=202)
async def solicitar_analise_completa(cotacao_id: str, payload: JustificativaInput, background_tasks: BackgroundTasks, request: Request, current_user: dict = Depends(get_current_user)):
    cotacao = _quote(cotacao_id)
    operation = _operation(cotacao.get("operation_id"))
    if not operation:
        raise HTTPException(status_code=409, detail="Cotação não possui operação para análise completa")
    snapshots: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    rows = supabase.table("component_snapshots").select("component,status,parsed_result").eq("operation_id", operation["id"]).execute().data or []
    for row in rows:
        if row.get("component"):
            statuses[row["component"]] = row.get("status") or ""
            if isinstance(row.get("parsed_result"), dict): snapshots[row["component"]] = row["parsed_result"]
    motivos = traduzir_motivos(cotacao.get("estagio_motivo"), snapshots, cotacao=cotacao, parametros=_params())
    if any(item["classe"] == "VETO" for item in motivos):
        raise HTTPException(status_code=409, detail="Bloqueado: existe veto ativo")
    classe = max((item["classe"] for item in motivos), key=lambda item: _SEVERITY[item], default="EM_ANDAMENTO")
    requires_reason = classe in {"CREDITO", "REVISAR_DADO"}
    justificativa = (payload.justificativa or "").strip()
    if requires_reason and len(justificativa) < 10:
        raise HTTPException(status_code=422, detail="Justificativa de pelo menos 10 caracteres é obrigatória")
    if operation.get("status") in {"processing", "pending"}:
        return {"cotacao_id": cotacao_id, "operation_id": operation["id"], "status": "accepted", "idempotent": True}
    _audit("quote_full_analysis_requested", cotacao, current_user, request, payload={"justificativa": justificativa or None, "motivos": [item["codigo_origem"] for item in motivos]})
    supabase.table("operations").update({"status": "processing"}).eq("id", operation["id"]).execute()
    from app.workers.tasks.orchestrator import start_report_analysis
    background_tasks.add_task(start_report_analysis, str(operation["id"]))
    return {"cotacao_id": cotacao_id, "operation_id": operation["id"], "status": "accepted"}


@router.post("/{cotacao_id}/arquivar")
async def arquivar_cotacao(cotacao_id: str, payload: JustificativaInput, request: Request, current_user: dict = Depends(get_current_user)):
    cotacao = _quote(cotacao_id)
    if cotacao.get("estagio_motivo") == "arquivada_manual":
        return {"cotacao_id": cotacao_id, "status": "archived", "idempotent": True}
    operation = _operation(cotacao.get("operation_id"))
    if cotacao.get("estagio") == "QUALIFICADA" and operation and operation.get("status") in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="Não é possível arquivar uma análise em andamento")
    before = {"estagio": cotacao.get("estagio"), "estagio_motivo": cotacao.get("estagio_motivo")}
    supabase.table("cotacoes_broadfactor").update({"estagio": "ENCERRADA", "estagio_motivo": "arquivada_manual", "estagio_atualizado_em": datetime.now(timezone.utc).isoformat(), "status_ingestao": "ENCERRADA"}).eq("cotacao_id", cotacao_id).execute()
    _audit("quote_archived", cotacao, current_user, request, payload={"justificativa": (payload.justificativa or "").strip() or None}, previous=before, new={"estagio": "ENCERRADA", "estagio_motivo": "arquivada_manual"})
    return {"cotacao_id": cotacao_id, "status": "archived"}


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
