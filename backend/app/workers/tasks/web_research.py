"""
Componente: web_research
Pesquisa de reputação da empresa via Claude Sonnet + WebSearch.
Busca razão social no snapshot brasil_api para contextualizar a pesquisa.

Tipo: LLM | Fila: llm | Cache: 48h
"""
import anthropic
import json
import re
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
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
  "noticias_negativas": <true|false>,
  "processos_relevantes": <true|false>,
  "reclamacoes_graves": <true|false>,
  "problemas_governo": <true|false>,
  "resumo": "<2-3 frases objetivas>",
  "alertas": ["<alerta1>", "<alerta2>"],
  "fontes_consultadas": ["<fonte1>", "<fonte2>"]
}"""


def _fetch(cnpj: str, token: str = None, operation_id: str = None) -> dict:
    from app.core.config import settings
    from app.core.database import supabase

    # Busca razão social do snapshot brasil_api
    razao_social = ""
    nome_fantasia = ""
    try:
        if operation_id:
            result = supabase.table("component_snapshots")\
                .select("parsed_result")\
                .eq("operation_id", operation_id)\
                .eq("component", "brasil_api")\
                .single()\
                .execute()
            if result.data and result.data.get("parsed_result"):
                razao_social = result.data["parsed_result"].get("razao_social", "")
                nome_fantasia = result.data["parsed_result"].get("nome_fantasia", "")
    except Exception:
        pass

    empresa_info = f"CNPJ {cnpj}"
    if razao_social:
        empresa_info += f" — {razao_social}"
        if nome_fantasia and nome_fantasia != razao_social:
            empresa_info += f" (nome fantasia: {nome_fantasia})"

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    prompt = f"""Pesquise a reputação da empresa {empresa_info} no mercado brasileiro.

Foque especialmente em:
- Histórico de fornecimento de serviços para o governo federal
- Problemas contratuais com órgãos públicos nos últimos 2 anos
- Processos no TCU, CGU ou Ministério Público
- Notícias negativas recentes
- Reclamações no Reclame Aqui

IMPORTANTE: Pesquise especificamente esta empresa pelo nome e CNPJ. Não confunda com outras empresas de nomes similares.

Retorne apenas o JSON estruturado conforme instruído."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = ""
    for block in response.content:
        if block.type == "text":
            result_text = block.text
            break

    try:
        clean = re.sub(r"```json|```", "", result_text).strip()
        return json.loads(clean)
    except Exception:
        return {
            "score_reputacao": 50,
            "nivel_risco": "medio",
            "noticias_negativas": False,
            "processos_relevantes": False,
            "reclamacoes_graves": False,
            "problemas_governo": False,
            "resumo": "Não foi possível processar a pesquisa de reputação.",
            "alertas": [],
            "fontes_consultadas": [],
            "raw_response": result_text,
        }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="llm",
    name="web_research.run",
    max_retries=1,
    default_retry_delay=30,
)
def run_web_research(self, operation_id: str):
    return self.execute(operation_id, component="web_research", handler=_fetch)
