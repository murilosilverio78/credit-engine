"""
Teste standalone: web_research
Requer ANTHROPIC_API_KEY no .env

Uso:
    cd backend
    python tests/test_web_research.py
"""
import sys, os, json, re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic

API_KEY = os.getenv("ANTHROPIC_API_KEY")
CNPJ    = os.getenv("TEST_CNPJ", "03012610000101")

if not API_KEY:
    print("❌ ANTHROPIC_API_KEY não encontrado"); sys.exit(1)

print(f"🔍 Pesquisando reputação — CNPJ: {CNPJ}")
print(f"🤖 Modelo: claude-sonnet-4-20250514 + WebSearch")
print("-" * 60)

SYSTEM = """Você é um analista de risco de crédito especializado em fornecedores do governo brasileiro.
Pesquise a reputação da empresa e retorne APENAS um JSON válido:
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

try:
    client = anthropic.Anthropic(api_key=API_KEY)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content":
            f"Pesquise a reputação da empresa CNPJ {CNPJ} (MASF SERVICOS E FACILITIES LTDA) "
            f"fornecedora do governo federal brasileiro. Foque nos últimos 2 anos."
        }],
    )

    result_text = ""
    for block in response.content:
        if block.type == "text":
            result_text = block.text
            break

    print(f"✅ Pesquisa concluída\n")

    clean = re.sub(r"```json|```", "", result_text).strip()
    r = json.loads(clean)

    print(f"📊 Score reputação:   {r.get('score_reputacao')}/100")
    print(f"⚠️  Nível de risco:    {r.get('nivel_risco')}")
    print(f"📰 Notícias negativas: {'Sim' if r.get('noticias_negativas') else 'Não'}")
    print(f"⚖️  Processos relevantes: {'Sim' if r.get('processos_relevantes') else 'Não'}")
    print(f"😡 Reclamações graves: {'Sim' if r.get('reclamacoes_graves') else 'Não'}")
    print(f"🏛️  Problemas governo:  {'Sim' if r.get('problemas_governo') else 'Não'}")
    print(f"\n📝 Resumo:\n   {r.get('resumo')}")

    if r.get("alertas"):
        print(f"\n🚨 Alertas:")
        for a in r["alertas"]:
            print(f"   • {a}")

    if r.get("fontes_consultadas"):
        print(f"\n🔗 Fontes consultadas:")
        for f in r["fontes_consultadas"]:
            print(f"   • {f}")

    out = Path(__file__).parent / "output_web_research.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")

except json.JSONDecodeError:
    print(f"⚠️  Resposta não é JSON válido. Raw response:")
    print(result_text)
except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc(); sys.exit(1)
