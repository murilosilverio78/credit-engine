"""
Componente: score_engine
Consolida o score final por modelo hibrido deterministico.

Python aplica gates, scorers estruturados, regularidade e agregacao. O LLM
julga apenas Porte/Operacionalidade em nivel discreto, sem emitir score final,
rating, taxa ou bloqueio.

Tipo: LLM | Fila: llm | Cache: nunca (sempre recalcula)
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

import anthropic
import structlog

from app.utils.encoding import fix_dict_encoding
from app.workers.base import BaseComponentTask

logger = structlog.get_logger()

NIVEL_NOTA = {
    "Excepcional": 95,
    "Forte": 85,
    "Adequado": 70,
    "Atencao": 55,
    "Fraco": 40,
    "Critico": 20,
}

PESOS_MERITO = {
    "relacionamento_governamental": 0.30,
    "porte_operacionalidade": 0.28,
    "saude_cadastral": 0.24,
    "reputacao_mercado": 0.18,
}

SUBPESOS_CADASTRAL = {
    "idade": 0.35,
    "capital": 0.29,
    "porte": 0.24,
    "estabilidade": 0.12,
}

SUBPESOS_RELAC = {
    "volume": 0.30,
    "diversificacao": 0.30,
    "historico": 0.25,
    "maturidade": 0.15,
}

LIMITE_PCT_CONTRATO = 0.70
PISO_FATOR_REG = 0.75
CERTIDOES_REGULARIDADE = ("cnd_federal", "cndt_tst", "fgts")

COMPONENT_DIMENSION_MAP = {
    "brasil_api": "saude_cadastral",
    "pessoa_juridica": "saude_cadastral",
    "contratos": "relacionamento_governamental",
    "web_research": "reputacao_mercado",
    "recursos_recebidos": "porte_operacionalidade",
}

PORTE_SYSTEM_PROMPT = """Você avalia UMA dimensão de risco de crédito: Porte e Operacionalidade — a capacidade
real de a empresa EXECUTAR seus contratos sem falha operacional. Neste produto
(antecipação de recebíveis de contrato do governo federal, com cessão fiduciária),
o risco dominante NÃO é insolvência do cedente — é FALHA DE PERFORMANCE: a empresa não
entrega, o recebível é glosado/retido, e o crédito antecipado não se realiza. Sua nota
mede essa probabilidade.

População de referência: prestadoras PJ ao governo federal, em geral de pequeno/médio
porte. A empresa típica é MEDIANA, não excepcional.

Raciocine NESTA ORDEM, antes de escolher o nível:
1. Alavancagem operacional: valor total de contratos ativos vs. capital social e porte.
   Contratos muito maiores que a estrutura = risco de performance alto.
2. Intensidade de mão de obra e contingência: terceirização, limpeza, facilities,
   vigilância dependem de folha contínua e têm alta contingência trabalhista —
   falha de folha = descontinuidade operacional.
3. Aderência entre o CNAE/atividade declarada e o objeto real dos contratos.
   Divergência = dispersão e risco.
4. Proxy de capacidade: faturamento estimado, funcionários, estrutura.

Âncoras de nível:
- Excepcional: grande porte, baixa alavancagem, execução comprovada em contratos de
  porte similar, setor de baixa intensidade de risco.
- Forte: porte médio com folga operacional clara sobre os contratos.
- Adequado: porte compatível com os contratos, sem folga nem fragilidade aparente. (DEFAULT)
- Atencao: pequeno porte com alavancagem alta (contratos >> capital/estrutura), OU setor
  de alta intensidade de mão de obra, OU divergência CNAE x objeto dos contratos.
- Fraco: subdimensionamento operacional evidente para o porte dos contratos.
- Critico: incapacidade operacional aparente.

REGRA: ausência de problema NÃO é excelência. Sem evidência POSITIVA de folga
operacional, o teto é "Adequado". "Forte" e "Excepcional" exigem folga demonstrada.

Se faltar dado essencial (sem porte, sem capital, sem contratos), retorne nível
"Atencao" com a flag "dado_insuficiente" e explique.

Retorne APENAS JSON válido, com o raciocínio ANTES do nível:
{
  "raciocinio": "<3-5 frases, análise antes da nota>",
  "nivel": "<Excepcional|Forte|Adequado|Atencao|Fraco|Critico>",
  "fatores": ["<fator>", ...],
  "flags": ["<flag>", ...]
}
"""


def nivel_label(score: float) -> str:
    if score >= 90:
        return "Excepcional"
    if score >= 80:
        return "Forte"
    if score >= 67:
        return "Adequado"
    if score >= 52:
        return "Atencao"
    if score >= 38:
        return "Fraco"
    return "Critico"


def rating_de(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    clean = re.sub(r"[^\d,.-]", "", str(value))
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text[:19] if "T" in text else text, fmt)
            return parsed.date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _years_since(value: Any, today: date | None = None) -> float | None:
    parsed = _as_date(value)
    if not parsed:
        return None
    today = today or datetime.now(timezone.utc).date()
    return max((today - parsed).days / 365.25, 0)


def _first_snapshot(snapshots: dict[str, Any], *components: str) -> dict[str, Any]:
    for component in components:
        data = snapshots.get(component)
        if isinstance(data, dict):
            return data
    return {}


def _component_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("registros", "items", "data", "resultados", "sancoes", "acordos"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _has_records(data: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                return True
            continue
        numeric = _as_float(value)
        if numeric and numeric > 0:
            return True
    return False


def _dimension(
    score: float,
    peso: float,
    fatores: list[str],
    relatorio: str,
    flags: list[str] | None = None,
    fonte: str = "python",
    nivel: str | None = None,
) -> dict[str, Any]:
    rounded = round(score, 1)
    return {
        "score": rounded,
        "nivel": nivel or nivel_label(rounded),
        "peso": peso,
        "fatores": fatores,
        "relatorio": relatorio,
        "flags": flags or [],
        "fonte": fonte,
        "score_contrib": round(rounded * peso, 2),
    }


def _idade_score(anos: float) -> int:
    if anos < 1:
        return 25
    if anos < 2:
        return 45
    if anos < 5:
        return 60
    if anos < 10:
        return 75
    if anos <= 20:
        return 88
    return 95


def _capital_score(capital: float) -> int:
    if capital < 10_000:
        return 30
    if capital < 50_000:
        return 50
    if capital < 200_000:
        return 65
    if capital < 500_000:
        return 78
    if capital <= 2_000_000:
        return 88
    return 95


def _porte_score(porte: Any) -> int | None:
    text = str(porte or "").upper()
    if "MEI" in text or "MICROEMPREENDEDOR" in text:
        return 35
    if "EPP" in text or "PEQUENO" in text:
        return 72
    if "MICRO" in text or re.search(r"\bME\b", text):
        return 58
    if "MEDIO" in text or "MÉDIO" in text:
        return 85
    if "GRANDE" in text:
        return 95
    return None


def _qsa_estabilidade_score(qsa: Any) -> tuple[int, list[str]]:
    if not isinstance(qsa, list) or not qsa:
        return 75, ["estabilidade_qsa_nao_validada"]
    entradas = [
        years
        for socio in qsa
        if isinstance(socio, dict)
        for years in [_years_since(socio.get("data_entrada") or socio.get("data_inicio"))]
        if years is not None
    ]
    if not entradas:
        return 75, ["estabilidade_qsa_nao_validada"]
    menor_tempo = min(entradas)
    if menor_tempo > 3:
        return 85, []
    if menor_tempo >= 1:
        return 70, []
    return 55, []


def _atividade_restrita(snapshot: dict[str, Any]) -> bool:
    textos = [
        str(snapshot.get("atividade_principal") or ""),
        str(snapshot.get("cnae_fiscal_descricao") or ""),
        json.dumps(snapshot.get("atividades_secundarias") or [], ensure_ascii=False),
    ]
    joined = " ".join(textos).lower()
    restritos = ("vigilancia", "seguranca", "arma", "financeira", "credito")
    return any(token in joined for token in restritos)


def score_saude_cadastral(snapshots: dict[str, Any]) -> dict[str, Any]:
    brasil = _first_snapshot(snapshots, "brasil_api", "pessoa_juridica")
    pessoa = _first_snapshot(snapshots, "pessoa_juridica", "brasil_api")
    flags: list[str] = []
    fatores: list[str] = []

    situacao = (
        brasil.get("situacao_cadastral")
        or brasil.get("descricao_situacao_cadastral")
        or pessoa.get("situacao_cadastral")
        or pessoa.get("situacao")
    )
    data_abertura = brasil.get("data_abertura") or brasil.get("abertura") or pessoa.get("data_abertura")
    capital = _as_float(brasil.get("capital_social") or pessoa.get("capital_social"))
    porte_raw = brasil.get("porte") or pessoa.get("porte")

    idade_anos = _years_since(data_abertura)
    idade = _idade_score(idade_anos) if idade_anos is not None else None
    capital_score = _capital_score(capital) if capital is not None else None
    porte = _porte_score(porte_raw)
    estabilidade, estabilidade_flags = _qsa_estabilidade_score(brasil.get("qsa") or pessoa.get("qsa"))
    flags.extend(estabilidade_flags)

    material_missing = []
    if not situacao:
        material_missing.append("situacao")
    if idade is None:
        material_missing.append("idade")
    if capital_score is None:
        material_missing.append("capital")
    if material_missing:
        flags.append("dado_nao_validado")
        fatores.append(f"Dados materiais ausentes: {', '.join(material_missing)}")

    if porte is None:
        porte = 58
        flags.append("porte_nao_validado")

    score = round(
        SUBPESOS_CADASTRAL["idade"] * (idade or 55)
        + SUBPESOS_CADASTRAL["capital"] * (capital_score or 55)
        + SUBPESOS_CADASTRAL["porte"] * porte
        + SUBPESOS_CADASTRAL["estabilidade"] * estabilidade,
        1,
    )
    if material_missing:
        score = min(score, 55.0)

    if _atividade_restrita(brasil):
        flags.append("atividade_restrita_ou_incompativel")

    fatores.extend([
        f"Situacao cadastral: {situacao or 'nao validada'}",
        f"Idade empresarial: {idade_anos:.1f} anos" if idade_anos is not None else "Idade empresarial nao validada",
        f"Capital social: R$ {capital:,.2f}" if capital is not None else "Capital social nao validado",
        f"Porte declarado: {porte_raw or 'nao validado'}",
    ])
    return _dimension(
        score,
        PESOS_MERITO["saude_cadastral"],
        fatores,
        "Saude cadastral calculada por idade, capital, porte e estabilidade societaria.",
        flags,
    )


def _active_contracts(contratos: dict[str, Any]) -> list[dict[str, Any]]:
    detalhes = contratos.get("contratos_detalhe") or contratos.get("contratos") or []
    if not isinstance(detalhes, list):
        return []
    active = []
    for item in detalhes:
        if not isinstance(item, dict):
            continue
        if item.get("ativo") is True:
            active.append(item)
            continue
        status = str(item.get("situacao") or item.get("status") or "").lower()
        if any(token in status for token in ("ativo", "vigente", "em execucao", "em execução")):
            active.append(item)
    return active


def _contract_duration_years(contract: dict[str, Any]) -> float | None:
    start = _as_date(contract.get("data_inicio") or contract.get("inicio_vigencia"))
    end = _as_date(contract.get("data_fim") or contract.get("fim_vigencia") or contract.get("data_termino"))
    if not start or not end:
        return None
    return max((end - start).days / 365.25, 0)


def score_relacionamento(snapshots: dict[str, Any]) -> dict[str, Any]:
    contratos = _first_snapshot(snapshots, "contratos")
    flags: list[str] = []

    active = _active_contracts(contratos)
    ativos_count = int(contratos.get("contratos_ativos") or len(active) or 0)
    total_count = int(contratos.get("total_contratos") or len(contratos.get("contratos_detalhe") or []) or ativos_count)
    orgaos = contratos.get("orgaos_contratantes")
    if isinstance(orgaos, list):
        org_count = len({str(org) for org in orgaos if org})
    else:
        orgs_from_contracts = {
            str(item.get("orgao") or item.get("orgao_nome") or item.get("contratante"))
            for item in active
            if isinstance(item, dict) and (item.get("orgao") or item.get("orgao_nome") or item.get("contratante"))
        }
        org_count = len(orgs_from_contracts)

    if ativos_count == 0:
        volume = 30
        flags.append("sem_contrato_ativo")
    elif ativos_count == 1:
        volume = 50
    elif ativos_count <= 4:
        volume = 68
    elif ativos_count <= 9:
        volume = 82
    else:
        volume = 92

    if org_count <= 1:
        diversificacao = 45
    elif org_count == 2:
        diversificacao = 62
    elif org_count <= 4:
        diversificacao = 78
    else:
        diversificacao = 90

    if total_count <= 2:
        historico = 50
    elif total_count <= 5:
        historico = 68
    elif total_count <= 12:
        historico = 82
    else:
        historico = 92

    durations = [value for item in active for value in [_contract_duration_years(item)] if value is not None]
    max_duration = max(durations) if durations else None
    if max_duration is None or max_duration < 1:
        maturidade = 55
    elif max_duration < 3:
        maturidade = 72
    else:
        maturidade = 88

    score = round(
        SUBPESOS_RELAC["volume"] * volume
        + SUBPESOS_RELAC["diversificacao"] * diversificacao
        + SUBPESOS_RELAC["historico"] * historico
        + SUBPESOS_RELAC["maturidade"] * maturidade,
        1,
    )
    fatores = [
        f"{ativos_count} contratos ativos",
        f"{org_count} orgaos distintos",
        f"{total_count} contratos no historico",
        f"Maturidade maxima: {max_duration:.1f} anos" if max_duration is not None else "Maturidade nao validada",
    ]
    return _dimension(
        score,
        PESOS_MERITO["relacionamento_governamental"],
        fatores,
        "Relacionamento governamental calculado por volume, diversificacao, historico e maturidade dos contratos.",
        flags,
    )


def _valid_level(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    aliases = {
        "Atenção": "Atencao",
        "atencao": "Atencao",
        "atenção": "Atencao",
        "excepcional": "Excepcional",
        "forte": "Forte",
        "adequado": "Adequado",
        "fraco": "Fraco",
        "critico": "Critico",
        "crítico": "Critico",
    }
    return text if text in NIVEL_NOTA else aliases.get(text.lower())


def score_reputacao(snapshots: dict[str, Any]) -> dict[str, Any]:
    web = _first_snapshot(snapshots, "web_research")
    flags: list[str] = []
    nivel = _valid_level(web.get("nivel"))
    if not nivel:
        nivel = "Adequado"
        flags.append("reputacao_indisponivel")

    positive_signals = web.get("fatores_reputacao") or []
    if not positive_signals and web.get("score_reputacao", 0) >= 85:
        positive_signals = ["score_reputacao_alto"]
    if nivel == "Excepcional" and not positive_signals:
        nivel = "Forte"
        flags.append("reputacao_capada_sem_sinal_positivo")

    flags.extend(web.get("flags_reputacao") or [])
    score = NIVEL_NOTA[nivel]
    fatores = web.get("fatores_reputacao") or web.get("alertas") or []
    if not fatores and web.get("resumo"):
        fatores = [web["resumo"]]
    relatorio = web.get("raciocinio_reputacao") or web.get("resumo") or "Reputacao classificada pelo snapshot de pesquisa web."
    return _dimension(
        score,
        PESOS_MERITO["reputacao_mercado"],
        fatores,
        relatorio,
        flags,
        fonte="llm",
        nivel=nivel,
    )


def _certidao_estado(certidao: dict[str, Any]) -> tuple[str, float, list[str]]:
    flags: list[str] = []
    resultado = str(
        certidao.get("resultado")
        or certidao.get("situacao")
        or certidao.get("status")
        or ""
    ).strip().lower()
    validade = _as_date(certidao.get("data_validade") or certidao.get("validade"))
    valida = certidao.get("valida")

    if validade and validade < datetime.now(timezone.utc).date():
        flags.append("dado_nao_validado")
        return "vencida", 0.05, flags
    if valida is None and certidao:
        flags.append("dado_nao_validado")
        return "nao_validada", 0.05, flags
    if valida is False:
        flags.append("dado_nao_validado")
        return "nao_validada", 0.05, flags
    if "positiva_com_efeitos" in resultado or "efeitos de negativa" in resultado:
        return "positiva_com_efeitos_negativa", 0.04, flags
    if "positiva" in resultado:
        return "positiva", 0.10, flags
    if "negativa" in resultado and valida is not False:
        return "negativa", 0.0, flags
    if not certidao:
        flags.append("certidao_indisponivel")
        return "indisponivel", 0.0, flags
    flags.append("dado_nao_validado")
    return "nao_validada", 0.05, flags


def score_regularidade(snapshots: dict[str, Any]) -> dict[str, Any]:
    haircuts = []
    flags: list[str] = []
    total = 0.0
    for component in CERTIDOES_REGULARIDADE:
        estado, haircut, cert_flags = _certidao_estado(_first_snapshot(snapshots, component))
        total += haircut
        flags.extend(cert_flags)
        haircuts.append({"certidao": component, "estado": estado, "haircut": round(haircut, 2)})
    fator = round(max(PISO_FATOR_REG, 1 - total), 2)
    return {"fator": fator, "haircuts": haircuts, "flags": sorted(set(flags))}


def _is_active_record(record: dict[str, Any]) -> bool:
    joined = " ".join(str(record.get(key) or "") for key in ("situacao", "status", "situacao_cadastral", "data_fim"))
    text = joined.lower()
    inactive_tokens = ("inativo", "baixado", "encerrado", "cancelado", "suspenso")
    if any(token in text for token in inactive_tokens):
        return False
    return any(token in text for token in ("ativo", "vigente", "impedimento", "proibicao", "proibição"))


def gates_deterministicos(snapshots: dict[str, Any]) -> list[str]:
    bloqueios: list[str] = []
    cadastro = _first_snapshot(snapshots, "brasil_api", "pessoa_juridica")
    situacao = (
        cadastro.get("situacao_cadastral")
        or cadastro.get("descricao_situacao_cadastral")
        or cadastro.get("situacao")
    )
    if situacao and str(situacao).strip().upper() != "ATIVA":
        bloqueios.append(f"Situacao cadastral diferente de ATIVA: {situacao}")

    pessoa = _first_snapshot(snapshots, "pessoa_juridica")
    if pessoa.get("possui_sancao") or any(pessoa.get(key) for key in ("sancionado_ceis", "sancionado_cnep", "sancionado_cepim", "sancionado_ceaf")):
        bloqueios.append("Sancao ativa identificada em bases restritivas")

    for component in ("ceis", "cnep", "cepim", "ceaf"):
        data = snapshots.get(component)
        if isinstance(data, dict) and _has_records(data, "possui_sancao", "total_registros"):
            registros = _component_list(data)
            if not registros or any(isinstance(item, dict) and _is_active_record(item) for item in registros):
                bloqueios.append(f"Sancao ativa em {component.upper()}")

    acordos = snapshots.get("acordos_leniencia")
    if isinstance(acordos, dict) and _has_records(acordos, "possui_acordo", "total_registros", "total_acordos"):
        registros = _component_list(acordos)
        if not registros or any(isinstance(item, dict) and _is_active_record(item) for item in registros):
            bloqueios.append("Acordo de leniencia ativo")

    return sorted(set(bloqueios))


def score_porte_llm(cnpj: str, snapshots: dict[str, Any], client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    from app.core.config import settings

    client = client or anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    payload = {
        "brasil_api": snapshots.get("brasil_api"),
        "pessoa_juridica": snapshots.get("pessoa_juridica"),
        "contratos": snapshots.get("contratos"),
        "recursos_recebidos": snapshots.get("recursos_recebidos"),
    }
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        temperature=0,
        system=PORTE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Avalie Porte e Operacionalidade da empresa CNPJ {cnpj}.\n\n"
                    f"DADOS ESTRUTURADOS:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            }
        ],
    )
    text = response.content[0].text
    try:
        clean = re.sub(r"```json|```", "", text).strip()
        result = fix_dict_encoding(json.loads(clean))
        nivel = _valid_level(result.get("nivel")) or "Atencao"
        flags = list(result.get("flags") or [])
        if not _valid_level(result.get("nivel")):
            flags.append("nivel_invalido")
        return _dimension(
            NIVEL_NOTA[nivel],
            PESOS_MERITO["porte_operacionalidade"],
            list(result.get("fatores") or []),
            result.get("raciocinio") or "Porte e operacionalidade classificados pelo LLM.",
            flags,
            fonte="llm",
            nivel=nivel,
        )
    except Exception as exc:
        logger.error("score_engine.porte_parse_error", error=str(exc), raw_response=text)
        return _dimension(
            NIVEL_NOTA["Atencao"],
            PESOS_MERITO["porte_operacionalidade"],
            [],
            "Nao foi possivel interpretar a resposta de Porte/Operacionalidade.",
            ["parse_falhou"],
            fonte="llm",
            nivel="Atencao",
        )


DIMENSION_LABELS = {
    "relacionamento_governamental": "Relacionamento governamental",
    "porte_operacionalidade": "Porte/Operacionalidade",
    "saude_cadastral": "Saude cadastral",
    "reputacao_mercado": "Reputacao de mercado",
}


def _limite_aprovado(snapshots: dict[str, Any], operacao: dict[str, Any]) -> tuple[float, list[str]]:
    contratos = _first_snapshot(snapshots, "contratos")
    valor_total_ativo = _as_float(contratos.get("valor_total_ativo"))
    if valor_total_ativo and valor_total_ativo > 0:
        return round(valor_total_ativo * LIMITE_PCT_CONTRATO, 2), []

    contrato_saldo = _as_float(operacao.get("contrato_saldo")) or 0
    valor_solicitado = _as_float(operacao.get("valor_solicitado")) or 0
    base = contrato_saldo if contrato_saldo > 0 else valor_solicitado
    if base <= 0:
        return 0.0, ["limite_sem_base_contrato"]
    limite = round(base * LIMITE_PCT_CONTRATO, 2)
    return min(limite, valor_solicitado) if valor_solicitado > 0 else limite, []


def _flags_relevantes(
    dimensoes: dict[str, Any],
    regularidade: dict[str, Any],
    extras: list[str] | None = None,
) -> list[str]:
    flags = {
        flag
        for dim in dimensoes.values()
        for flag in dim.get("flags", [])
    } | set(regularidade.get("flags", [])) | set(extras or [])
    return sorted(flag for flag in flags if flag)


def _parecer_estruturado(
    score: float,
    rating: str,
    merit: float,
    dimensoes: dict[str, Any],
    regularidade: dict[str, Any],
    pontos_positivos: list[str],
    pontos_atencao: list[str],
    flags_extra: list[str] | None = None,
    bloqueios: list[str] | None = None,
) -> dict[str, Any]:
    bloqueios = bloqueios or []
    if bloqueios:
        return {
            "conclusao": {
                "rating": "E",
                "score": 20,
                "merit": 20,
                "fator_regularidade": 1.0,
                "texto": bloqueios[0],
            },
            "dimensoes": [],
            "regularidade": {"fator": 1.0, "haircuts": [], "flags": []},
            "pontos_positivos": [],
            "pontos_atencao": bloqueios,
            "flags_relevantes": bloqueios,
        }

    fator = regularidade.get("fator", 1.0)
    haircuts = regularidade.get("haircuts", [])
    haircut_textos = [
        f"{item.get('certidao')}: {item.get('estado')} ({float(item.get('haircut') or 0):.2f})"
        for item in haircuts
        if float(item.get("haircut") or 0) > 0
    ]
    dimensoes_lista = []
    for chave in PESOS_MERITO:
        dim = dimensoes.get(chave, {})
        dimensoes_lista.append({
            "chave": chave,
            "label": DIMENSION_LABELS.get(chave, chave),
            "nivel": dim.get("nivel", "—"),
            "score": dim.get("score", 0),
            "peso": dim.get("peso", PESOS_MERITO[chave]),
            "score_contrib": dim.get("score_contrib", 0),
            "relatorio": dim.get("relatorio") or "—",
            "fatores": dim.get("fatores") or [],
            "flags": dim.get("flags") or [],
            "fonte": dim.get("fonte") or "python",
        })

    return {
        "conclusao": {
            "rating": rating,
            "score": score,
            "merit": merit,
            "fator_regularidade": fator,
            "texto": (
                f"Rating {rating}, score final {score:.1f}. "
                f"O resultado combina merito {merit:.1f} com fator de regularidade {fator:.2f} "
                f"({merit:.1f} x {fator:.2f} = {score:.1f})."
            ),
        },
        "dimensoes": dimensoes_lista,
        "regularidade": {
            "fator": fator,
            "texto": (
                f"Regularidade aplicada como multiplicador {fator:.2f}. "
                + (
                    "Haircuts aplicados: " + "; ".join(haircut_textos) + "."
                    if haircut_textos
                    else "Nenhum haircut aplicado."
                )
            ),
            "haircuts": haircuts,
            "flags": regularidade.get("flags", []),
        },
        "pontos_positivos": pontos_positivos,
        "pontos_atencao": pontos_atencao,
        "flags_relevantes": _flags_relevantes(dimensoes, regularidade, flags_extra),
    }


def _parecer_texto(parecer: dict[str, Any]) -> str:
    partes = [f"Conclusao: {parecer.get('conclusao', {}).get('texto', '—')}"]
    for dim in parecer.get("dimensoes", []):
        fatores = dim.get("fatores") or []
        fatores_texto = f" Fatores: {'; '.join(map(str, fatores))}." if fatores else ""
        partes.append(
            f"{dim.get('label')}: nivel {dim.get('nivel')}, nota {dim.get('score')}. "
            f"{dim.get('relatorio')}{fatores_texto}"
        )
    regularidade = parecer.get("regularidade", {})
    partes.append(f"Regularidade: {regularidade.get('texto', '—')}")
    pontos_positivos = parecer.get("pontos_positivos") or []
    pontos_atencao = parecer.get("pontos_atencao") or []
    flags = parecer.get("flags_relevantes") or []
    if pontos_positivos:
        partes.append("Pontos positivos: " + "; ".join(map(str, pontos_positivos)) + ".")
    if pontos_atencao:
        partes.append("Pontos de atencao: " + "; ".join(map(str, pontos_atencao)) + ".")
    if flags:
        partes.append("Flags relevantes: " + "; ".join(map(str, flags)) + ".")
    return "\n\n".join(partes)


def _parecer(score: float, rating: str, merit: float, dimensoes: dict[str, Any], regularidade: dict[str, Any], pontos_positivos: list[str], pontos_atencao: list[str], flags_extra: list[str] | None = None) -> str:
    estruturado = _parecer_estruturado(
        score,
        rating,
        merit,
        dimensoes,
        regularidade,
        pontos_positivos,
        pontos_atencao,
        flags_extra,
    )
    return _parecer_texto(estruturado)


def _parecer_resumo(score: float, rating: str, merit: float, regularidade: dict[str, Any]) -> str:
    return (
        f"Rating {rating}, score final {score:.1f}. "
        f"Merito {merit:.1f} x fator de regularidade {regularidade.get('fator', 1):.2f}."
    )


def consolidar_score(
    cnpj: str,
    snapshots: dict[str, Any],
    operacao: dict[str, Any] | None = None,
    porte_dimension: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshots = fix_dict_encoding(snapshots or {})
    operacao = operacao or {}
    bloqueios = gates_deterministicos(snapshots)
    if bloqueios:
        parecer_estruturado = _parecer_estruturado(
            20,
            "E",
            20,
            {},
            {"fator": 1.00, "haircuts": [], "flags": []},
            [],
            bloqueios,
            bloqueios=bloqueios,
        )
        return {
            "score": 20,
            "rating": "E",
            "merit": 20,
            "fator_regularidade": 1.00,
            "limite_sugerido_pct_contrato": LIMITE_PCT_CONTRATO,
            "limite_aprovado_rs": 0.0,
            "dimensoes": {},
            "regularidade": {"fator": 1.00, "haircuts": [], "flags": []},
            "bloqueios": bloqueios,
            "pontos_positivos": [],
            "pontos_atencao": bloqueios,
            "parecer_estruturado": parecer_estruturado,
            "parecer": bloqueios[0],
        }

    dimensoes = {
        "relacionamento_governamental": score_relacionamento(snapshots),
        "porte_operacionalidade": porte_dimension or score_porte_llm(cnpj, snapshots),
        "saude_cadastral": score_saude_cadastral(snapshots),
        "reputacao_mercado": score_reputacao(snapshots),
    }
    regularidade = score_regularidade(snapshots)
    merit = round(sum(dimensoes[dim]["score"] * peso for dim, peso in PESOS_MERITO.items()), 1)
    score_final = round(merit * regularidade["fator"], 1)
    rating = rating_de(score_final)

    pontos_positivos = [
        f"{dim}: {value['nivel']}"
        for dim, value in dimensoes.items()
        if value["score"] >= 80
    ]
    pontos_atencao = [
        f"{dim}: {value['nivel']}"
        for dim, value in dimensoes.items()
        if value["score"] < 67
    ]
    limite_aprovado_rs, limite_flags = _limite_aprovado(snapshots, operacao)
    parecer_estruturado = _parecer_estruturado(
        score_final,
        rating,
        merit,
        dimensoes,
        regularidade,
        pontos_positivos,
        pontos_atencao,
        limite_flags,
    )

    return {
        "score": score_final,
        "rating": rating,
        "merit": merit,
        "fator_regularidade": regularidade["fator"],
        "limite_sugerido_pct_contrato": LIMITE_PCT_CONTRATO,
        "limite_aprovado_rs": limite_aprovado_rs,
        "dimensoes": dimensoes,
        "regularidade": regularidade,
        "flags": limite_flags,
        "bloqueios": [],
        "pontos_positivos": pontos_positivos,
        "pontos_atencao": pontos_atencao,
        "parecer_estruturado": parecer_estruturado,
        "parecer": _parecer_texto(parecer_estruturado),
        "parecer_resumo": _parecer_resumo(score_final, rating, merit, regularidade),
    }


def _fetch(cnpj: str, token: str = None, operation_id: str = None) -> dict:
    from app.core.database import supabase

    snapshots: dict[str, Any] = {}
    operacao: dict[str, Any] = {}
    if operation_id:
        try:
            operation_result = supabase.table("operations")\
                .select("valor_solicitado,prazo_dias,contrato_saldo")\
                .eq("id", operation_id)\
                .maybe_single()\
                .execute()
            operacao = operation_result.data or {}

            result = supabase.table("component_snapshots")\
                .select("component, parsed_result, status")\
                .eq("operation_id", operation_id)\
                .in_("status", ["completed"])\
                .execute()

            for snap in result.data or []:
                if snap.get("parsed_result"):
                    snapshots[snap["component"]] = snap["parsed_result"]
            snapshots = fix_dict_encoding(snapshots)

            logger.info(
                "score_engine.snapshots_loaded",
                operation_id=operation_id,
                components=list(snapshots.keys()),
            )
        except Exception as exc:
            logger.error("score_engine.snapshots_error", error=str(exc), operation_id=operation_id)

    result = consolidar_score(cnpj, snapshots, operacao)

    if operation_id:
        for component, dim in COMPONENT_DIMENSION_MAP.items():
            try:
                score_contrib = result.get("dimensoes", {}).get(dim, {}).get("score_contrib")
                if score_contrib is None:
                    continue
                supabase.table("component_snapshots")\
                    .update({"score_contrib": score_contrib})\
                    .eq("operation_id", operation_id)\
                    .eq("component", component)\
                    .execute()
            except Exception as exc:
                logger.error(
                    "score_engine.score_contrib_error",
                    component=component,
                    error=str(exc),
                    operation_id=operation_id,
                )

    return result


_task = BaseComponentTask()


def run_score_engine(operation_id: str):
    return _task.execute(operation_id, component="score_engine", handler=_fetch)
