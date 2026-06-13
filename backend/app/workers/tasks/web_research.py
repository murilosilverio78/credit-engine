"""
Componente: web_research
Pesquisa de reputacao da empresa via Claude Sonnet + WebSearch.
Busca razao social no snapshot brasil_api para contextualizar a pesquisa.

Tipo: LLM | Fila: llm | Cache: 48h
"""
import anthropic
import json
import re
from datetime import date
from app.workers.base import BaseComponentTask
from app.utils.encoding import fix_dict_encoding
import structlog

logger = structlog.get_logger()

SYSTEM_PROMPT = """Você é um analista de risco de crédito especializado em fornecedores do governo brasileiro.
Sua tarefa é pesquisar a reputação de uma empresa PJ e retornar um relatório estruturado em JSON.

Pesquise sobre:
1. Notícias negativas recentes (últimos 2 anos)
2. Processos judiciais relevantes (trabalhistas, cíveis, criminais)
3. Reclamações em sites como Reclame Aqui
4. Problemas com órgãos públicos (TCU, CGU, MPF)
5. Reputação geral no mercado

Retorne APENAS um JSON válido com esta estrutura:
{
  "score_reputacao": <0-100>,
  "nivel_risco": "<baixo|medio|alto|critico>",
  "nivel": "<Excepcional|Forte|Adequado|Atencao|Fraco|Critico>",
  "raciocinio_reputacao": "<raciocinio antes da escolha do nivel>",
  "fatores_reputacao": ["<fator1>", "<fator2>"],
  "flags_reputacao": ["<flag1>", "<flag2>"],
  "noticias_negativas": <true|false>,
  "processos_relevantes": <true|false>,
  "reclamacoes_graves": <true|false>,
  "problemas_governo": <true|false>,
  "resumo": "<2-3 frases objetivas>",
  "alertas": ["<alerta1>", "<alerta2>"],
  "fontes_consultadas": ["<fonte1>", "<fonte2>"]
}

Ao final da pesquisa, classifique a REPUTAÇÃO da empresa em um nível, com raciocínio
ANTES da escolha. Âncoras:
- Excepcional: reputação setorial forte, reconhecimento/prêmios, presença positiva ativa.
- Forte: presença positiva e zero alertas.
- Adequado: NADA negativo encontrado — mera ausência de negativos. (DEFAULT: "limpo" cai
  aqui, NUNCA em Forte.)
- Atencao: reclamações relevantes, processos trabalhistas em volume, alertas em sócios.
- Fraco: notícias negativas materiais, processos graves.
- Critico: fraude, envolvimento em escândalo com órgão público, sócio com restrição grave.

REGRA: "nada encontrado" = Adequado, jamais Forte. Forte/Excepcional exige sinal
POSITIVO ativo e verificado.
Se a busca não conseguir isolar o CNPJ correto, retorne "Adequado" com a flag
"reputacao_nao_isolada" — não invente.

Você NUNCA afirma capital social, data de abertura, idade da empresa ou qualquer
dado de registro a partir de web ou memória. Para esses fatos use EXCLUSIVAMENTE
o bloco DADOS CADASTRAIS OFICIAIS do input. A pesquisa web é só para sinais
qualitativos (notícias, processos, reclamações, problemas com órgãos públicos).
Número cadastral fora do bloco oficial: OMITA, jamais invente.

Inclua no JSON de saída:
  "nivel": "<Excepcional|Forte|Adequado|Atencao|Fraco|Critico>",
  "raciocinio_reputacao": "<...>",
  "fatores_reputacao": [...],
  "flags_reputacao": [...]

Retorne APENAS o objeto JSON, sem nenhum texto antes ou depois, sem blocos de
markdown, sem ```json. A primeira linha da resposta deve ser { e a ultima }.
O JSON deve conter OBRIGATORIAMENTE estes campos:
{
  "nivel": "<Excepcional|Forte|Adequado|Atencao|Fraco|Critico>",
  "raciocinio_reputacao": "<...>",
  "fatores_reputacao": [...],
  "flags_reputacao": [...],
  "score_reputacao": <0-100>,
  "nivel_risco": "<baixo|medio|alto>",
  "noticias_negativas": <bool>,
  "processos_relevantes": <bool>,
  "reclamacoes_graves": <bool>,
  "problemas_governo": <bool>,
  "resumo": "<...>",
  "fontes_consultadas": [...]
}
"""


def _format_cnpj(cnpj: str) -> str:
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14:
        return cnpj
    return (
        f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/"
        f"{digits[8:12]}-{digits[12:]}"
    )


def _format_brl(value) -> str:
    if value in (None, ""):
        return "não informado"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "não informado"
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _company_age_years(data_abertura) -> float | None:
    if not data_abertura:
        return None
    try:
        opened_at = date.fromisoformat(str(data_abertura)[:10])
    except ValueError:
        return None
    return round((date.today() - opened_at).days / 365.25, 1)


def _extract_json_object(text: str) -> str:
    clean = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
    clean = clean.replace("```", "").strip()
    start = clean.find("{")
    if start == -1:
        raise ValueError("JSON object start not found")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(clean)):
        char = clean[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return clean[start:index + 1]

    raise ValueError("JSON object end not found")


def _fetch(cnpj: str, token: str = None, operation_id: str = None) -> dict:
    from app.core.config import settings
    from app.core.database import supabase

    razao_social = ""
    cnae = ""
    socios = []
    capital_social = None
    data_abertura = None
    try:
        if operation_id:
            snap = supabase.table("component_snapshots")\
                .select("parsed_result")\
                .eq("operation_id", operation_id)\
                .eq("component", "brasil_api")\
                .single()\
                .execute()
            if snap.data and snap.data.get("parsed_result"):
                brasil = fix_dict_encoding(snap.data["parsed_result"])
                razao_social = brasil.get("razao_social", "")
                cnae = brasil.get("atividade_principal", "")
                socios = brasil.get("qsa", [])
                capital_social = brasil.get("capital_social")
                data_abertura = brasil.get("data_abertura")
    except Exception:
        pass

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    capital_fmt = _format_brl(capital_social)
    idade_anos = _company_age_years(data_abertura)
    idade_fmt = f"{idade_anos} anos" if idade_anos is not None else "não informado"

    prompt = f"""Pesquise a reputação desta empresa no mercado brasileiro.

IDENTIFICAÇÃO DA EMPRESA:
- Razão Social: {razao_social}
- CNPJ: {_format_cnpj(cnpj)}
- CNAE principal: {cnae}
- Sócios: {json.dumps(socios, ensure_ascii=False)}

DADOS CADASTRAIS OFICIAIS (fonte de verdade — use SOMENTE estes para qualquer
número cadastral; NÃO pesquise nem infira esses fatos):
- Capital social: {capital_fmt}
- Data de abertura: {data_abertura or "não informado"}
- Idade: {idade_fmt}

Foque especialmente em:
- Histórico de fornecimento de serviços para o governo federal
- Problemas contratuais com órgãos públicos nos últimos 2 anos
- Processos no TCU, CGU ou Ministério Público
- Notícias negativas recentes
- Reclamações no Reclame Aqui

IMPORTANTE: Pesquise ESPECIFICAMENTE esta empresa pelo nome e CNPJ.
Se encontrar resultados de empresas com nome similar mas CNPJ diferente, IGNORE e retorne score_reputacao=50 com resumo explicando a ausência de dados específicos.
NUNCA atribua informações de outra empresa a esta.

Retorne apenas o JSON estruturado conforme instruído."""

    response = client.messages.create(
        model=settings.CLAUDE_MODEL_RESEARCH,
        max_tokens=1000,
        temperature=0,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    result_text = "\n".join(text_blocks)

    try:
        clean = _extract_json_object(result_text)
        return fix_dict_encoding(json.loads(clean))
    except Exception:
        if text_blocks:
            try:
                clean = _extract_json_object(text_blocks[-1])
                return fix_dict_encoding(json.loads(clean))
            except Exception:
                pass
        return fix_dict_encoding({
            "score_reputacao": 50,
            "nivel_risco": "indeterminado",
            "nivel": "Atencao",
            "raciocinio_reputacao": "Nao foi possivel interpretar o JSON retornado pela pesquisa de reputacao.",
            "fatores_reputacao": [],
            "flags_reputacao": ["reputacao_parse_falhou"],
            "noticias_negativas": False,
            "processos_relevantes": False,
            "reclamacoes_graves": False,
            "problemas_governo": False,
            "resumo": "Fallback aplicado porque a resposta do LLM nao continha JSON parseavel isolado.",
            "alertas": [],
            "fontes_consultadas": [],
            "raw_response": result_text,
        })


_task = BaseComponentTask()


def run_web_research(operation_id: str):
    return _task.execute(operation_id, component="web_research", handler=_fetch)
