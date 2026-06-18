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
  "raciocinio_reputacao": "<maximo 2 frases resumidas, nao o raciocinio completo>",
  "fatores_reputacao": ["<maximo 3 fatores principais>"],
  "flags_reputacao": ["<flags objetivas, sem explicacao longa>"],
  "noticias_negativas": <true|false>,
  "processos_relevantes": <true|false>,
  "reclamacoes_graves": <true|false>,
  "problemas_governo": <true|false>,
  "resumo": "<maximo 2 frases>",
  "alertas": ["<maximo 3 alertas, frases curtas>"],
  "fontes_consultadas": ["<fonte1>", "<fonte2>"]
}

IMPORTANTE: Seja conciso. O JSON inteiro deve caber em menos de 800 tokens.
Raciocinio longo vai em raciocinio_reputacao em NO MAXIMO 2 frases curtas.

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
  "raciocinio_reputacao": "<maximo 2 frases resumidas>",
  "fatores_reputacao": ["<maximo 3 fatores principais>"],
  "flags_reputacao": ["<flags objetivas, sem explicacao longa>"],
  "score_reputacao": <0-100>,
  "nivel_risco": "<baixo|medio|alto>",
  "noticias_negativas": <bool>,
  "processos_relevantes": <bool>,
  "reclamacoes_graves": <bool>,
  "problemas_governo": <bool>,
  "resumo": "<maximo 2 frases>",
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
    """
    Extracts and repairs a JSON object from LLM output.
    Handles: markdown fences, truncated JSON, extra text before/after.
    """
    import re
    # Strip markdown fences
    text = re.sub(r"```json|```", "", text).strip()

    # Find the start of JSON object
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in text")
    text = text[start:]

    # Try parsing as-is first
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Try to find complete JSON object by matching braces
    depth = 0
    in_string = False
    escape_next = False
    end_pos = -1
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end_pos = i
                break

    if end_pos != -1:
        candidate = text[:end_pos + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Last resort: truncation repair
    # Close any open strings, arrays, and objects
    repaired = text
    # Remove trailing incomplete string/value
    repaired = re.sub(r',\s*"[^"]*$', "", repaired)  # remove last incomplete key-value
    repaired = re.sub(r':\s*"[^"]*$', ': ""', repaired)  # close open string value
    repaired = re.sub(r',\s*$', "", repaired)  # remove trailing comma

    # Count unclosed brackets
    open_arrays = repaired.count("[") - repaired.count("]")
    open_objects = repaired.count("{") - repaired.count("}")
    repaired += "]" * max(0, open_arrays)
    repaired += "}" * max(0, open_objects)

    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not repair truncated JSON: {e}") from e


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
        max_tokens=2000,
        temperature=0,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # Collect all text blocks; JSON lives in the LAST one after tool_use/tool_result
    text_blocks = [block.text for block in response.content if block.type == "text"]
    result_text = text_blocks[-1] if text_blocks else ""
    if not result_text:
        logger.warning("web_research.no_text_block", operation_id="unknown",
                       block_types=[b.type for b in response.content])

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
