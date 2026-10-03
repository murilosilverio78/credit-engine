"""Pure deterministic interpreter for policy v0.

Only parameters and rules contain numeric policy choices.  This module does
not import database clients or the score engine so it can be differentially
tested without external state.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.policy.types import EntradaPolitica, ResultadoPolitica


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finding(entrada: EntradaPolitica, code: str, trail: dict[str, list[dict[str, str | None]]], stage: str):
    item = entrada.findings.get(code)
    if item:
        trail.setdefault(stage, []).append({"codigo": code, "run_id": item.run_id})
    return item


def _rating(score: float, bands: dict[str, Any]) -> str:
    for rating in ("A", "B", "C", "D"):
        if score >= _number(bands.get(rating)):
            return rating
    return "E"


def _level(value: Any, params: dict[str, Any]) -> float:
    levels = params["nivel_nota"]
    aliases = {"Atenção": "Atencao", "atenção": "Atencao", "atencao": "Atencao", "excepcional": "Excepcional", "forte": "Forte", "adequado": "Adequado", "fraco": "Fraco", "critico": "Critico", "crítico": "Critico"}
    value = aliases.get(str(value).strip().lower(), value)
    return _number(levels.get(value), _number(levels["Adequado"]))


def _age_at_reference(finding, reference: date) -> float:
    age = _number(finding.valor)
    evidence = finding.evidencia[0] if finding and finding.evidencia else {}
    emitted = evidence.get("data_referencia") if isinstance(evidence, dict) else None
    try:
        issued = date.fromisoformat(str(emitted))
    except (TypeError, ValueError):
        return age
    return max(0.0, age - (issued - reference).days / 365.25)


def _pd(entrada, params, rating, trail):
    cv_finding = _finding(entrada, "volatilidade_cv", trail, "ajuste_pd")
    years_finding = _finding(entrada, "anos_completos_receita", trail, "ajuste_pd")
    cv = _number(cv_finding.valor) if cv_finding and cv_finding.valor is not None else None
    years = int(_number(years_finding.valor)) if years_finding and years_finding.valor is not None else 0
    minimum = int(_number(params["pd_min_anos_completos_volatilidade"]))
    multiplier, parameter = 1.0, None
    if cv is None and years < minimum:
        faixa, parameter = "HISTORICO_INSUFICIENTE", "pd_mult_historico_insuficiente"
        multiplier = _number(params.get(parameter) or params.get("pd_mult_volatilidade_moderada"), 1.0)
    elif cv is None:
        faixa = "INDISPONIVEL"
    elif cv <= _number(params["pd_cv_corte_moderado"]):
        faixa = "BAIXA"
    elif cv <= _number(params["pd_cv_corte_alto"]):
        faixa, parameter = "MODERADA", "pd_mult_volatilidade_moderada"
        multiplier = _number(params.get(parameter), 1.0)
    else:
        faixa, parameter = "ALTA", "pd_mult_volatilidade_alta"
        multiplier = _number(params.get(parameter), 1.0)
    base = _number(params["pd_performada"]) * _number(params["pd_mult_por_rating"].get(rating))
    return {"cv": cv, "anos_completos": years, "min_anos_completos": minimum, "corte_moderado": _number(params["pd_cv_corte_moderado"]), "corte_alto": _number(params["pd_cv_corte_alto"]), "faixa_volatilidade": faixa, "parametro": parameter, "multiplicador_volatilidade": multiplier, "pd_base": round(base, 6), "pd_ajustada": round(base * multiplier, 6)}


def _new_effects(entrada, rules, trail):
    effects = []
    for rule in rules:
        if rule.get("classe") != "AJUSTE":
            continue
        found = _finding(entrada, str(rule.get("codigo")), trail, "efeitos_novos")
        condition, value = rule.get("condicao") or {}, found.valor if found else None
        applies = False
        if found and found.confianca == "ALTA" and rule.get("codigo") == "conta_vinculada_regime":
            applies = value == condition.get("valor")
        elif found and rule.get("codigo") == "glosa_historica" and isinstance(value, dict):
            applies = _number(value.get("taxa_glosa")) >= _number(condition.get("taxa_glosa_min")) and _number(value.get("faturado_total")) >= _number(condition.get("faturado_total_min"))
        if applies:
            effects.append({"codigo": rule.get("codigo"), "parametro_alvo": rule.get("parametro_alvo"), "direcao": rule.get("direcao"), "magnitude": _number(rule.get("magnitude"))})
    return effects


def _band(value: float, bands: list[dict[str, Any]]) -> float:
    for band in bands:
        if "ate" not in band or value < _number(band["ate"]):
            return _number(band["nota"])
    return _number(bands[-1]["nota"])


def avaliar(entrada: EntradaPolitica, parametros: dict[str, Any], regras: list[dict[str, Any]], data_referencia: date) -> ResultadoPolitica:
    """Evaluate facts only; no I/O, current clock, or score-engine import."""
    trail: dict[str, list[dict[str, str | None]]] = {}
    vetos = []
    for rule in regras:
        if rule.get("classe") != "VETO":
            continue
        found = _finding(entrada, str(rule.get("codigo")), trail, "vetos")
        if found and found.estado == "CONFIRMADO" and found.confianca == "ALTA" and bool(found.valor):
            vetos.append(str(found.codigo))
    if vetos:
        return ResultadoPolitica(_number(parametros["score_bloqueio"]), "E", "E", _number(parametros["score_bloqueio"]), _number(parametros["score_bloqueio"]), 1.0, 1.0, 0.0, 0.0, vetos, None, {}, [], trail)

    idade = _finding(entrada, "idade_empresa_anos", trail, "saude_cadastral")
    capital = _finding(entrada, "capital_social_rs", trail, "saude_cadastral")
    porte = _finding(entrada, "porte_cadastral", trail, "saude_cadastral")
    qsa = _finding(entrada, "qsa_estabilidade", trail, "saude_cadastral")
    idade_score = _band(_age_at_reference(idade, data_referencia), parametros["faixas_idade"]) if idade else 55.0
    capital_score = _band(_number(capital.valor), parametros["faixas_capital"]) if capital else 55.0
    porte_map = parametros["faixas_porte_cadastral"]
    empresarial = _finding(entrada, "natureza_juridica_empresarial", trail, "saude_cadastral")
    porte_score = _number(porte_map.get(str(porte.valor).upper(), porte_map["natureza_nao_empresarial"] if empresarial and not empresarial.valor else porte_map["fallback"])) if porte else _number(porte_map["fallback"])
    qsa_value = qsa.valor if qsa else {}
    recent = qsa_value.get("entrada_mais_recente") if isinstance(qsa_value, dict) else None
    qsa_score = _number(parametros["faixas_estabilidade_qsa"]["sem_dados"])
    if recent:
        years = max((data_referencia - date.fromisoformat(str(recent))).days / 365.25, 0)
        qsa_score = _number(parametros["faixas_estabilidade_qsa"]["maior_3" if years > 3 else "de_1_a_3" if years >= 1 else "menor_1"])
    sub = parametros["subpesos_cadastral"]
    health = round(idade_score * _number(sub["idade"]) + capital_score * _number(sub["capital"]) + porte_score * _number(sub["porte"]) + qsa_score * _number(sub["estabilidade"]), 1)
    if not idade or not capital:
        health = min(health, _number(parametros["score_cap_dado_material_ausente"]))

    active = _finding(entrada, "contratos_ativos_qtd", trail, "relacionamento")
    total = _finding(entrada, "contratos_total_qtd", trail, "relacionamento")
    orgs = _finding(entrada, "orgaos_distintos_qtd", trail, "relacionamento")
    hhi = _finding(entrada, "hhi_recebimentos", trail, "relacionamento")
    months = _finding(entrada, "meses_com_recebimento", trail, "relacionamento")
    maturity = _finding(entrada, "maturidade_max_anos", trail, "relacionamento")
    active_n, total_n, org_n = _number(active.valor), _number(total.valor), _number(orgs.valor)
    volume = 30 if active_n == 0 else 50 if active_n == 1 else 68 if active_n <= 4 else 82 if active_n <= 9 else 92
    fallback = 45 if org_n <= 1 else 62 if org_n == 2 else 78 if org_n <= 4 else 90
    hhi_n = _number(hhi.valor, -1) if hhi else -1
    concentration = 90 if 0 < hhi_n < 2500 else 70 if hhi_n <= 6000 and hhi_n > 0 else 45 if hhi_n > 0 else fallback
    if hhi_n > 0 and months and _number(months.valor) < _number(parametros["hhi_min_meses_recebimento"]):
        concentration = min(concentration, fallback)
    history = 50 if total_n <= 2 else 68 if total_n <= 5 else 82 if total_n <= 12 else 92
    maturity_score = 55 if not maturity or _number(maturity.valor) < 1 else 72 if _number(maturity.valor) < 3 else 88
    rel = parametros["subpesos_relacionamento"]
    relationship = round(volume * _number(rel["volume"]) + concentration * _number(rel["concentracao"]) + history * _number(rel["historico"]) + maturity_score * _number(rel["maturidade"]), 1)

    capability = _finding(entrada, "capacidade_operacional", trail, "niveis")
    reputation = _finding(entrada, "reputacao_mercado", trail, "niveis")
    capability_score = _level(capability.valor if capability else "Adequado", parametros)
    reputation_score = _level(reputation.valor if reputation else "Adequado", parametros)
    weights = parametros["pesos_merito"]
    merit_potential = round(relationship * _number(weights["relacionamento_governamental"]) + capability_score * _number(weights["porte_operacionalidade"]) + health * _number(weights["saude_cadastral"]) + reputation_score * _number(weights["reputacao_mercado"]), 1)

    haircuts = 0.0
    penalties = 0.0
    for code, penalty_key in (("certidao_cnd_federal_pendente", "penalidade_cnd_federal_ausente"), ("certidao_cndt_pendente", "penalidade_cndt_ausente"), ("certidao_fgts_pendente", "penalidade_fgts_ausente")):
        found = _finding(entrada, code, trail, "regularidade")
        value = found.valor if found else None
        state = value.get("estado") if isinstance(value, dict) else "ausente"
        haircuts += _number(parametros["haircut_certidao_por_estado"].get(state))
        if state == "ausente":
            penalties += _number(parametros[penalty_key])
    factor_potential = round(max(_number(parametros["piso_fator_regularidade"]), 1 - haircuts), 2)
    factor = round(max(0.0, factor_potential - penalties / 100), 2)
    balance = _finding(entrada, "balanco_ausente", trail, "balanco")
    balance_penalty = _number(parametros["penalidade_balanco_ausente"]) if balance and bool(balance.valor) else 0.0
    merit = merit_potential
    score = round(max(0.0, round(merit_potential * factor, 1) - balance_penalty), 1)
    rating = _rating(score, parametros["faixas_rating"])
    potential_rating = _rating(round(merit_potential * factor_potential, 1), parametros["faixas_rating"])
    limit = min(_number(entrada.operation.get("valor_enquadrado")), _number(entrada.operation.get("valor_solicitado")) or _number(entrada.operation.get("valor_enquadrado")))
    effects = _new_effects(entrada, regras, trail)
    return ResultadoPolitica(score, rating, potential_rating, merit, merit_potential, factor, factor_potential, balance_penalty, limit, [], _pd(entrada, parametros, rating, trail), {"saude_cadastral": health, "relacionamento_governamental": relationship, "porte_operacionalidade": capability_score, "reputacao_mercado": reputation_score}, effects, trail)
