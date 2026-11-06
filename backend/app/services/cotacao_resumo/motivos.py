"""Translate persisted funnel reasons into the approved quote-summary contract."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog

from app.workers.tasks.score_engine import _component_list, _is_active_record

logger = structlog.get_logger()

_CLASSES = {
    "VETO": ("Veto", "Veto"),
    "CREDITO": ("Crédito", "Reprovada"),
    "REVISAR_DADO": ("Revisar dado", "Revisar dado"),
    "CLIENTE": ("Aguardando cliente", "Depende do cliente"),
    "COMERCIAL": ("Ajustável", "Reversível pelo comercial"),
    "ENCERRADA": ("Encerrada", "Encerrada"),
}


def _brl(value: Any) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "—"


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip()[:10], pattern).date()
        except ValueError:
            continue
    return None


def _fmt_date(value: Any) -> str:
    parsed = _date(value)
    return parsed.strftime("%d/%m/%Y") if parsed else "—"


def _evidence(rotulo: str, valor: Any, tom: str = "neutro") -> dict[str, str]:
    return {"rotulo": rotulo, "valor": str(valor), "tom": tom}


def _base(codigo: str, origem: str, classe: str, titulo: str, texto: str, evidencias: list[dict[str, str]], fonte: str, *, component: str | None = None) -> dict[str, Any]:
    chip, _ = _CLASSES[classe]
    result: dict[str, Any] = {
        "codigo": codigo,
        "codigo_origem": origem,
        "classe": classe,
        "chip": chip,
        "titulo": titulo,
        "texto": texto,
        "evidencias": evidencias,
        "fonte": fonte,
    }
    if component:
        result["ref"] = {"component": component}
    return result


def _sanction_record(component: str, snapshots: dict[str, Any]) -> dict[str, Any] | None:
    data = snapshots.get(component) or {}
    for record in _component_list(data):
        if isinstance(record, dict) and _is_active_record(record):
            return record
    return None


def _sanction(origen: str, component: str, snapshots: dict[str, Any]) -> dict[str, Any]:
    record = _sanction_record(component, snapshots) or {}
    end = record.get("dataFimSancao") or record.get("data_fim")
    end_date = _date(end)
    remaining = (end_date - date.today()).days if end_date else None
    scope = record.get("abrangencia") or record.get("abrangenciaSancao") or "—"
    organ = record.get("orgaoSancionador") or record.get("orgao") or record.get("orgaoResponsavel") or "—"
    tipo = record.get("tipoSancao") or record.get("tipo") or "—"
    fundamento = record.get("fundamentoLegal") or record.get("fundamento") or "—"
    process = record.get("processo") or record.get("numeroProcesso") or "—"
    publication = record.get("publicacao") or record.get("dataPublicacao") or "—"
    vigencia = _fmt_date(record.get("dataInicioSancao") or record.get("data_inicio")) + " a " + _fmt_date(end)
    if remaining is not None:
        vigencia += f" · {remaining} dias restantes"
    label = component.upper() if component != "acordos_leniencia" else "acordo de leniência"
    return _base(
        f"SANCAO_{component.upper()}", origen, "VETO", f"Sanção ativa no {label}",
        "A política trata qualquer sanção ativa como veto.",
        [
            _evidence("Órgão sancionador", organ),
            _evidence("Tipo", tipo), _evidence("Fundamento", fundamento),
            _evidence("Vigência", vigencia, "ruim"), _evidence("Abrangência", scope),
            _evidence("Processo e publicação", f"{process} · {publication}"),
        ], f"Portal da Transparência ({label}).", component=component,
    )


def divergencia_pncp_comprasnet(snapshots: dict[str, Any]) -> dict[str, Any]:
    pncp = snapshots.get("contratos_pncp") or {}
    comprasnet = snapshots.get("contratos_comprasnet") or {}
    p = pncp.get("contrato_cedido") or {}
    c = comprasnet.get("contrato_comprasnet") or {}
    comparisons = (("valor_global", "valor_global"), ("data_inicio_vigencia", "vigencia_inicio"), ("data_fim_vigencia", "vigencia_fim"))
    different = any(
        p.get(left) not in (None, "") and c.get(right) not in (None, "") and str(p[left]) != str(c[right])
        for left, right in comparisons
    )
    return {"divergente": different, "pncp": p, "comprasnet": c}


def traduzir_motivos(estagio_motivo: str | None, snapshots: dict[str, Any], *, cotacao: dict[str, Any] | None = None, parametros: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return only structured, presentation-ready reasons; never writes data."""
    cotacao, parametros = cotacao or {}, parametros or {}
    motivos: list[dict[str, Any]] = []
    for original in (part.strip() for part in (estagio_motivo or "").split(";")):
        if not original:
            continue
        if original.startswith("Sancao ativa em "):
            component = original.removeprefix("Sancao ativa em ").lower()
            motivos.append(_sanction(original, component, snapshots)); continue
        if original == "Sancao ativa identificada em bases restritivas":
            component = next((name for name in ("ceis", "cnep", "cepim", "ceaf") if _sanction_record(name, snapshots)), "ceis")
            motivos.append(_sanction(original, component, snapshots)); continue
        if original == "Acordo de leniencia ativo":
            motivos.append(_sanction(original, "acordos_leniencia", snapshots)); continue
        if original.startswith("Situacao cadastral diferente de ATIVA"):
            situation = original.split(":", 1)[-1].strip()
            motivos.append(_base("SITUACAO_CADASTRAL", original, "VETO", f"Situação cadastral {situation}", "A situação cadastral não atende à política.", [_evidence("Situação", situation, "ruim")], "BrasilAPI ou Portal da Transparência.", component="brasil_api")); continue
        if original.startswith("tipo_nao_elegivel:"):
            value = original.split(":", 1)[1]
            motivos.append(_base("TIPO_NAO_ELEGIVEL", original, "VETO", "Tipo de recebível não elegível", "O tipo da cotação não pode seguir para análise.", [_evidence("Tipo recebido", value, "ruim")], "Cotação da Broadfactor.")); continue
        if original.startswith("cedente_nao_pj:"):
            value = original.split(":", 1)[1]
            motivos.append(_base("CEDENTE_NAO_PJ", original, "VETO", "Cedente não é pessoa jurídica", "A política exige cedente pessoa jurídica.", [_evidence("Tipo recebido", value, "ruim")], "Cotação da Broadfactor.")); continue
        if original == "abaixo_ticket_minimo":
            motivos.append(_base("ABAIXO_TICKET_MINIMO", original, "VETO", "Valor abaixo do ticket mínimo", "O valor enquadrável não alcança o mínimo da política.", [_evidence("Valor pedido", _brl(cotacao.get("valor_solicitado"))), _evidence("Valor enquadrável", _brl(cotacao.get("valor_enquadrado")), "ruim"), _evidence("Ticket mínimo", _brl(parametros.get("ticket_minimo")), "ruim")], "Cotação da Broadfactor e parâmetros de elegibilidade.")); continue
        if original == "acima_ticket_maximo":
            maximum = parametros.get("ticket_maximo")
            proposal = min(float(cotacao.get("valor_enquadrado") or maximum or 0), float(maximum or 0)) if maximum is not None else cotacao.get("valor_enquadrado")
            motivos.append(_base("ACIMA_TICKET_MAXIMO", original, "COMERCIAL", "Valor acima do ticket máximo", "O valor pedido e o que o contrato comporta ficam acima do ticket máximo; há proposta possível no teto da política.", [_evidence("Valor pedido", _brl(cotacao.get("valor_solicitado"))), _evidence("Saldo vincendo do contrato", _brl(cotacao.get("saldo_vincendo"))), _evidence("Enquadrável", _brl(cotacao.get("valor_enquadrado"))), _evidence("Ticket máximo da política", _brl(maximum), "ruim"), _evidence("Proposta possível", _brl(proposal), "bom")], "Cotação da Broadfactor e parâmetros de elegibilidade.")); continue
        if original == "janela_expiracao_insuficiente":
            motivos.append(_base("JANELA_EXPIRACAO_INSUFICIENTE", original, "VETO", "Cotação expira antes do prazo mínimo", "A cotação expira antes da janela mínima configurada.", [_evidence("Expiração", _fmt_date(cotacao.get("data_expiracao")), "ruim"), _evidence("Mínimo", f"{parametros.get('dias_minimos_expiracao', '—')} dias", "ruim")], "Cotação da Broadfactor.")); continue
        if original == "sem_documentos_broadfactor":
            expected = ("Contrato social", "Último balanço", "Faturamento", "Endividamento", "CND Federal", "IR do sócio avalista")
            motivos.append(_base("SEM_DOCUMENTOS_BROADFACTOR", original, "CLIENTE", "Nenhum documento na Broadfactor", "Documentos que a análise usa e que ainda não chegaram:", [_evidence(item, "não recebido", "ruim") for item in expected], "Listagem de documentos da Broadfactor, atualizada 2x por dia.")); continue
        if original.startswith("capacidade_insuficiente:"):
            divergence = divergencia_pncp_comprasnet(snapshots)
            p, c = divergence["pncp"], divergence["comprasnet"]
            classe = "REVISAR_DADO" if divergence["divergente"] else "CREDITO"
            motivos.append(_base("CAPACIDADE_INSUFICIENTE", original, classe, "Capacidade do contrato insuficiente" if not divergence["divergente"] else "Capacidade do contrato zerada", "A divergência entre PNCP e Comprasnet deve ser revisada antes de reutilizar este dado." if divergence["divergente"] else "O fluxo futuro líquido do contrato não suporta o ticket mínimo.", [_evidence("Vigência no PNCP", f"{_fmt_date(p.get('data_inicio_vigencia'))} a {_fmt_date(p.get('data_fim_vigencia'))}", "ruim" if divergence["divergente"] else "neutro"), _evidence("Prazo vincendo no Comprasnet", f"{c.get('prazo_vincendo_meses') or '—'} meses"), _evidence("Valor global", f"{_brl(c.get('valor_global'))} (Comprasnet) · {_brl(p.get('valor_global'))} (PNCP)"), _evidence("Capacidade calculada", _brl(original.split(":", 1)[1]), "ruim"), _evidence("Divergência entre fontes", "Sim: vigência e valor global" if divergence["divergente"] else "Não", "atencao" if divergence["divergente"] else "bom")], "Fontes: PNCP e Comprasnet.", component="contratos_pncp")); continue
        prefixes = {
            "prazo_vincendo_insuficiente:": ("PRAZO_VINCENDO_INSUFICIENTE", "Prazo vincendo abaixo de 60 dias", "CREDITO"),
            "historico_recebimentos_insuficiente:": ("HISTORICO_RECEBIMENTOS_INSUFICIENTE", "Histórico de recebimentos curto", "CREDITO"),
            "orgaos_pagadores_insuficientes:": ("ORGAOS_PAGADORES_INSUFICIENTES", "Poucos órgãos pagadores", "CREDITO"),
            "cobertura_insuficiente:": ("COBERTURA_INSUFICIENTE", "Cobertura de recebimentos insuficiente", "CREDITO"),
        }
        matched = next((prefix for prefix in prefixes if original.startswith(prefix)), None)
        if matched:
            code, title, classe = prefixes[matched]
            value = original.split(":", 1)[1]
            motivos.append(_base(code, original, classe, title, "O critério gratuito não atende ao mínimo da política.", [_evidence("Valor apurado", value, "ruim")], "Snapshots da operação.")); continue
        if original == "contrato_comprasnet_nao_encontrado":
            comprasnet = snapshots.get("contratos_comprasnet") or {}
            motivos.append(_base("CONTRATO_COMPRASNET_NAO_ENCONTRADO", original, "CREDITO", "Contrato não localizado no Comprasnet", "Não foi localizado um contrato elegível no Comprasnet.", [_evidence("Contrato", (comprasnet.get("contrato_comprasnet") or {}).get("numero") or "—", "ruim"), _evidence("UASG", (comprasnet.get("contrato_comprasnet") or {}).get("uasg") or "—")], "Comprasnet.", component="contratos_comprasnet")); continue
        if original == "situacao_cadastral_nao_verificada" or original.startswith("indisponibilidade_fonte:") or original.startswith("erro_documentos_broadfactor:"):
            component = original.split(":", 1)[1] if ":" in original else "brasil_api"
            snapshot = snapshots.get(component) or {}
            motivos.append(_base("FONTE_INDISPONIVEL", original, "REVISAR_DADO", f"Consulta {component.replace('_', ' ').upper()} indisponível", "A fonte necessária para a verificação ficou indisponível.", [_evidence("Componente", component), _evidence("Último erro", snapshot.get("error_message") or "—", "atencao")], f"Componente {component}.", component=component)); continue
        closed = {
            "cotacao_expirada": ("COTACAO_EXPIRADA", "Cotação expirada", "A cotação expirou antes de avançar."),
            "cotacao_ausente_na_listagem": ("COTACAO_AUSENTE_NA_LISTAGEM", "Cotação saiu da listagem da Broadfactor", "Registro do que já tinha sido feito antes do encerramento."),
            "QUOTATION_INACTIVE": ("QUOTATION_INACTIVE", "Cotação inativa na Broadfactor", "A Broadfactor informou que a cotação está inativa."),
            "arquivada_manual": ("ARQUIVADA_MANUAL", "Cotação arquivada", "A cotação foi arquivada manualmente."),
        }
        if original in closed:
            code, title, text = closed[original]
            motivos.append(_base(code, original, "ENCERRADA", title, text, [_evidence("Último estágio alcançado", cotacao.get("estagio_max") or "—"), _evidence("Data da cotação", _fmt_date(cotacao.get("data_cotacao"))), _evidence("Expiração", _fmt_date(cotacao.get("data_expiracao"))), _evidence("Documentos recebidos", cotacao.get("n_documentos") or 0)], "Comparação entre janelas de ingestão da Broadfactor.")); continue
        logger.warning("cotacao_resumo.unknown_reason", codigo_origem=original)
        motivos.append(_base("MOTIVO_DESCONHECIDO", original, "REVISAR_DADO", "Motivo a revisar", original, [_evidence("Texto original", original, "atencao")], "Código recebido do funil."))
    return motivos
