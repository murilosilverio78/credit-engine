"""
Componente: score_engine
Geração do score final, rating e parecer via Claude Opus.
Consolida todos os snapshots anteriores e produz a decisão de crédito.

Tipo: LLM | Fila: llm | Cache: nunca (sempre recalcula)
"""
import json
import re
import anthropic
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
from app.utils.encoding import fix_dict_encoding
import structlog

logger = structlog.get_logger()

SYSTEM_PROMPT = """Você é um analista sênior de crédito PJ especializado em fornecedores do governo brasileiro.

Seu trabalho é analisar os dados coletados sobre uma empresa e produzir:
1. Um score de crédito de 0 a 100
2. Um rating de A a E
3. Uma taxa de juros sugerida para antecipação de recebíveis
4. Um parecer executivo objetivo

SCORECARD 5D (pesos):
- Saúde Cadastral (25%): situação na Receita, tempo de existência, capital social, porte
- Regularidade Fiscal/Sanções (35%): certidões, CEIS, CNEP, CEPIM, sanções
- Relacionamento Governamental (15%): contratos ativos, diversificação de órgãos, histórico
- Reputação/Mercado (15%): pesquisa web, notícias, reclamações
- Porte/Operacionalidade (10%): faturamento estimado, recursos recebidos, capacidade operacional

RATING:
- A: 85-100 | Taxa: CDI + 1,5% a.m.
- B: 70-84  | Taxa: CDI + 2,0% a.m.
- C: 55-69  | Taxa: CDI + 2,8% a.m.
- D: 40-54  | Taxa: CDI + 3,5% a.m.
- E: 0-39   | Não operar

REGRAS DE BLOQUEIO AUTOMÁTICO (score → E independente dos demais):
- Situação cadastral != ATIVA
- Qualquer sanção ativa (CEIS, CNEP, CEPIM, CEAF)
- Acordo de leniência ativo

Retorne APENAS um JSON válido:
{
  "score": <0-100>,
  "rating": "<A|B|C|D|E>",
  "taxa_sugerida_am": <float>,
  "limite_sugerido_pct_contrato": <float entre 0 e 0.70>,
  "dimensoes": {
    "saude_cadastral": {"score": <0-100>, "peso": 0.25, "justificativa": "..."},
    "regularidade_fiscal": {"score": <0-100>, "peso": 0.35, "justificativa": "..."},
    "relacionamento_governamental": {"score": <0-100>, "peso": 0.15, "justificativa": "..."},
    "reputacao_mercado": {"score": <0-100>, "peso": 0.15, "justificativa": "..."},
    "porte_operacionalidade": {"score": <0-100>, "peso": 0.10, "justificativa": "..."}
  },
  "bloqueios": ["<motivo de bloqueio se houver>"],
  "pontos_positivos": ["<ponto1>", "<ponto2>"],
  "pontos_atencao": ["<ponto1>", "<ponto2>"],
  "parecer": "<3-5 frases executivas>"
}"""


def _fetch(cnpj: str, token: str = None, operation_id: str = None) -> dict:
    from app.core.config import settings
    from app.core.database import supabase

    # Busca todos os snapshots completados da operação
    snapshots = {}
    if operation_id:
        try:
            result = supabase.table("component_snapshots")\
                .select("component, parsed_result, status")\
                .eq("operation_id", operation_id)\
                .in_("status", ["completed"])\
                .execute()

            for snap in result.data:
                if snap.get("parsed_result"):
                    snapshots[snap["component"]] = snap["parsed_result"]
            snapshots = fix_dict_encoding(snapshots)

            logger.info(
                "score_engine.snapshots_loaded",
                operation_id=operation_id,
                components=list(snapshots.keys()),
            )
        except Exception as e:
            logger.error("score_engine.snapshots_error", error=str(e))

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    prompt = f"""Analise o perfil de crédito da empresa com CNPJ {cnpj}.

DADOS COLETADOS DOS COMPONENTES:
{json.dumps(snapshots, ensure_ascii=False, indent=2)}

Produza a análise completa conforme o scorecard 5D e retorne o JSON estruturado."""

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = response.content[0].text

    try:
        clean = re.sub(r"```json|```", "", result_text).strip()
        return fix_dict_encoding(json.loads(clean))
    except Exception:
        return fix_dict_encoding({
            "score": 0,
            "rating": "E",
            "taxa_sugerida_am": 0.035,
            "limite_sugerido_pct_contrato": 0,
            "dimensoes": {},
            "bloqueios": ["Erro ao processar score engine"],
            "pontos_positivos": [],
            "pontos_atencao": [],
            "parecer": "Não foi possível processar a análise.",
            "raw_response": result_text,
        })


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="llm",
    name="score_engine.run",
    max_retries=1,
    default_retry_delay=60,
)
def run_score_engine(self, operation_id: str):
    return self.execute(operation_id, component="score_engine", handler=_fetch)
