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
from app.services.pricing_engine import compute_taxa
from app.utils.encoding import fix_dict_encoding
import structlog

logger = structlog.get_logger()

SYSTEM_PROMPT = """Voce e um analista senior de credito PJ especializado em
fornecedores do governo brasileiro.

Analise os dados coletados dos componentes e atribua uma nota de 0 a 100 para
CADA dimensao do Scorecard 5D com base nas evidencias presentes.

SCORECARD 5D - criterios de pontuacao por dimensao:

D1 - Saude Cadastral (peso 25%):
  100 -> CNPJ ativo >=5 anos, capital social >=R$500k, sem restricoes cadastrais
   80 -> CNPJ ativo >=2 anos, capital adequado ao porte declarado
   60 -> CNPJ ativo <2 anos OU capital social abaixo do porte
   40 -> Irregularidades cadastrais menores ou dados inconsistentes
    0 -> situacao_cadastral != "ATIVA" (bloqueio automatico)

D2 - Regularidade Fiscal/Sancoes (peso 35%):
  100 -> Todas certidoes negativas + sem ocorrencias em CEIS/CNEP/CEPIM
   80 -> Certidoes OK, sem sancoes ativas
   60 -> Certidao ausente (nao enviada) OU divida ativa de baixo valor
   30 -> Sancao ativa em CEIS ou CNEP (situacao="Ativo")
    0 -> Acordo de leniencia ativo (bloqueio automatico)

D3 - Relacionamento Governamental (peso 15%):
  100 -> >=3 contratos ativos, >=2 orgaos distintos, historico de contratos >3 anos
   80 -> 1-2 contratos ativos com bom historico de cumprimento
   50 -> Sem contratos ativos mas historico positivo anterior
   20 -> Nunca contratado pelo governo OU historico muito recente (<1 ano)

D4 - Reputacao/Mercado (peso 15%):
  100 -> score_reputacao web >=80, sem alertas, sem noticias_negativas
   70 -> score_reputacao 50-79, sem problemas_governo
   40 -> noticias_negativas=true OU reclamacoes_graves=true
   10 -> problemas_governo=true OU processos_relevantes graves

D5 - Porte/Operacionalidade (peso 10%):
  100 -> recursos_recebidos >R$5M/ano, empresa >=5 anos no mercado
   70 -> recursos_recebidos R$1M-R$5M/ano
   40 -> recursos_recebidos abaixo de R$1M/ano OU dados operacionais limitados
   20 -> porte incompativel com contratos/recebiveis analisados

IMPORTANTE:
- Nao calcule score final.
- Nao determine rating.
- Nao sugira taxa.
- Apenas atribua notas por dimensao, liste bloqueios e escreva o parecer.

Retorne APENAS um JSON valido:
{
  "dimensoes": {
    "saude_cadastral":              {"nota": <0-100>, "justificativa": "<1-2 frases>"},
    "regularidade_fiscal":          {"nota": <0-100>, "justificativa": "<1-2 frases>"},
    "relacionamento_governamental": {"nota": <0-100>, "justificativa": "<1-2 frases>"},
    "reputacao_mercado":            {"nota": <0-100>, "justificativa": "<1-2 frases>"},
    "porte_operacionalidade":       {"nota": <0-100>, "justificativa": "<1-2 frases>"}
  },
  "bloqueios": [],
  "pontos_positivos": ["", ""],
  "pontos_atencao":   ["", ""],
  "parecer": "<3-5 frases executivas para o relatorio de credito>"
}"""

PESOS = {
    "saude_cadastral": 0.25,
    "regularidade_fiscal": 0.35,
    "relacionamento_governamental": 0.15,
    "reputacao_mercado": 0.15,
    "porte_operacionalidade": 0.10,
}
LIMITES_POR_RATING = {"A": 0.70, "B": 0.70, "C": 0.60, "D": 0.40, "E": 0.0}
COMPONENT_DIMENSION_MAP = {
    "brasil_api": "saude_cadastral",
    "pessoa_juridica": "saude_cadastral",
    "ceis": "regularidade_fiscal",
    "cnep": "regularidade_fiscal",
    "cepim": "regularidade_fiscal",
    "acordos_leniencia": "regularidade_fiscal",
    "cndt_tst": "regularidade_fiscal",
    "cnd_federal": "regularidade_fiscal",
    "fgts": "regularidade_fiscal",
    "contratos": "relacionamento_governamental",
    "web_research": "reputacao_mercado",
    "recursos_recebidos": "porte_operacionalidade",
}


def _calcular(dimensoes: dict, bloqueios: list) -> tuple[float, str]:
    if bloqueios:
        return 0.0, "E"
    score = round(sum(
        dimensoes.get(dim, {}).get("nota", 0) * peso
        for dim, peso in PESOS.items()
    ), 2)
    if score >= 85:
        return score, "A"
    if score >= 70:
        return score, "B"
    if score >= 55:
        return score, "C"
    if score >= 40:
        return score, "D"
    return score, "E"


def _fetch(cnpj: str, token: str = None, operation_id: str = None) -> dict:
    from app.core.config import settings
    from app.core.database import supabase

    # Busca todos os snapshots completados da operação
    snapshots = {}
    operacao = {}
    if operation_id:
        try:
            operation_result = supabase.table("operations")\
                .select("valor_solicitado,prazo_dias,contrato_saldo")\
                .eq("id", operation_id)\
                .single()\
                .execute()
            operacao = operation_result.data or {}

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
        opus_result = fix_dict_encoding(json.loads(clean))
    except Exception:
        opus_result = fix_dict_encoding({
            "dimensoes": {
                dim: {"nota": 0, "justificativa": "erro"}
                for dim in PESOS
            },
            "bloqueios": ["Erro ao processar score engine"],
            "pontos_positivos": [],
            "pontos_atencao": [],
            "parecer": "Nao foi possivel processar a analise.",
            "raw_response": result_text,
        })

    dimensoes = opus_result.get("dimensoes", {})
    bloqueios = opus_result.get("bloqueios", [])
    score, rating = _calcular(dimensoes, bloqueios)

    contrato_saldo = float(operacao.get("contrato_saldo") or 0)
    valor_solicitado = float(operacao.get("valor_solicitado") or 0)
    pct_limite = LIMITES_POR_RATING.get(rating, 0.0)
    if contrato_saldo > 0:
        limite_pelo_saldo = round(contrato_saldo * pct_limite, 2)
        if valor_solicitado > 0:
            limite_aprovado_rs = min(limite_pelo_saldo, valor_solicitado)
        else:
            limite_aprovado_rs = limite_pelo_saldo
    else:
        limite_aprovado_rs = round(valor_solicitado * pct_limite, 2)

    for dim, peso in PESOS.items():
        if dim in dimensoes:
            nota = dimensoes[dim].get("nota", 0)
            dimensoes[dim]["peso"] = peso
            dimensoes[dim]["score"] = nota
            dimensoes[dim]["score_contrib"] = round(nota * peso, 2)

    pricing = {}
    if rating != "E":
        try:
            valor = float(operacao.get("valor_solicitado") or 500_000)
            prazo_meses = max(round((float(operacao.get("prazo_dias") or 180)) / 30), 1)
            pricing = compute_taxa(rating, valor, prazo_meses)
        except Exception as exc:
            logger.error("score_engine.pricing_error", error=str(exc), operation_id=operation_id)

    result = {
        "score": score,
        "rating": rating,
        "taxa_sugerida_am": pricing.get("taxa_sugerida_am", 0.0),
        "taxa_breakdown": pricing,
        "limite_aprovado_rs": limite_aprovado_rs,
        "limite_sugerido_pct_contrato": pct_limite,
        "dimensoes": dimensoes,
        "bloqueios": bloqueios,
        "pontos_positivos": opus_result.get("pontos_positivos", []),
        "pontos_atencao": opus_result.get("pontos_atencao", []),
        "parecer": opus_result.get("parecer", ""),
    }
    if "raw_response" in opus_result:
        result["raw_response"] = opus_result["raw_response"]

    if operation_id:
        for component, dim in COMPONENT_DIMENSION_MAP.items():
            try:
                score_contrib = dimensoes.get(dim, {}).get("score_contrib")
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
