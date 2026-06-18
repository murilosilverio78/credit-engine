"""
Teste do componente web_research.

Por padrao, a suite roda offline: apenas o parsing unitario e executado.
Para exercitar a chamada real ao Anthropic, configure ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

CNPJ = os.getenv("TEST_CNPJ", "03012610000101")
MODEL = "claude-sonnet-4-6"

SYSTEM = """Voce e um analista de risco de credito especializado em fornecedores do governo brasileiro.
Pesquise a reputacao da empresa e retorne APENAS um JSON valido:
{
  "score_reputacao": <0-100>,
  "nivel_risco": "<baixo|medio|alto|critico>",
  "noticias_negativas": <true|false>,
  "processos_relevantes": <true|false>,
  "reclamacoes_graves": <true|false>,
  "problemas_governo": <true|false>,
  "resumo": "<2-3 frases objetivas>",
  "alertas": ["<alerta1>"],
  "fontes_consultadas": ["<fonte1>"]
}"""


def parse_web_research_response(result_text: str) -> dict:
    clean = re.sub(r"```json|```", "", result_text).strip()
    return json.loads(clean)


def test_parse_web_research_response_accepts_json_fence():
    result = parse_web_research_response(
        """```json
        {
          "score_reputacao": 82,
          "nivel_risco": "baixo",
          "noticias_negativas": false,
          "processos_relevantes": false,
          "reclamacoes_graves": false,
          "problemas_governo": false,
          "resumo": "Sem alertas materiais.",
          "alertas": [],
          "fontes_consultadas": ["consulta publica"]
        }
        ```"""
    )

    assert result["score_reputacao"] == 82
    assert result["nivel_risco"] == "baixo"
    assert result["alertas"] == []


@pytest.mark.network
def test_web_research_anthropic_integration():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY nao configurado")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Pesquise a reputacao da empresa CNPJ {CNPJ} "
                        "(MASF SERVICOS E FACILITIES LTDA) fornecedora do "
                        "governo federal brasileiro. Foque nos ultimos 2 anos."
                    ),
                }
            ],
        )
    except Exception as exc:
        pytest.skip(f"chamada externa indisponivel: {exc}")

    result_text = ""
    for block in response.content:
        if block.type == "text":
            result_text = block.text
            break

    result = parse_web_research_response(result_text)
    assert 0 <= result["score_reputacao"] <= 100
    assert result["nivel_risco"] in {"baixo", "medio", "alto", "critico"}

    out = Path(__file__).parent / "output_web_research.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
