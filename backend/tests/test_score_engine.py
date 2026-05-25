"""
Teste standalone: score_engine
Lê os outputs dos outros testes para simular o pipeline completo.
Requer ANTHROPIC_API_KEY no .env

Uso:
    cd backend
    python tests/test_score_engine.py
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

# Carrega outputs dos componentes anteriores
tests_dir = Path(__file__).parent
snapshots = {}

for comp in ["brasil_api", "pessoa_juridica", "contratos", "recursos_recebidos",
             "acordos_leniencia", "ceis", "cnep", "cepim", "web_research"]:
    output_file = tests_dir / f"output_{comp}.json"
    if output_file.exists():
        snapshots[comp] = json.loads(output_file.read_text(encoding="utf-8"))
        print(f"✅ Carregado: {comp}")
    else:
        print(f"⬜ Não encontrado: {comp} (pulando)")

print(f"\n🤖 Gerando score via Claude Opus...")
print("-" * 60)

SYSTEM = """Você é um analista sênior de crédito PJ especializado em fornecedores do governo brasileiro.

SCORECARD 5D:
- Saúde Cadastral (25%): situação Receita, tempo existência, capital social, porte
- Regularidade Fiscal/Sanções (35%): certidões, CEIS, CNEP, CEPIM, sanções
- Relacionamento Governamental (15%): contratos ativos, diversificação órgãos, histórico
- Reputação/Mercado (15%): pesquisa web, notícias, reclamações
- Porte/Operacionalidade (10%): faturamento estimado, recursos recebidos

RATING: A(85-100) B(70-84) C(55-69) D(40-54) E(<40)
TAXAS:  A=CDI+1.5% B=CDI+2.0% C=CDI+2.8% D=CDI+3.5% E=não operar
LIMITE: máximo 70% do saldo do contrato ativo

BLOQUEIOS AUTOMÁTICOS → rating E:
- Situação cadastral != ATIVA
- Qualquer sanção CEIS/CNEP/CEPIM/CEAF ativa
- Acordo de leniência ativo

Retorne APENAS JSON válido:
{
  "score": <0-100>,
  "rating": "<A|B|C|D|E>",
  "taxa_sugerida_am": <float>,
  "limite_sugerido_pct_contrato": <float 0-0.70>,
  "dimensoes": {
    "saude_cadastral": {"score": <0-100>, "peso": 0.25, "justificativa": "..."},
    "regularidade_fiscal": {"score": <0-100>, "peso": 0.35, "justificativa": "..."},
    "relacionamento_governamental": {"score": <0-100>, "peso": 0.15, "justificativa": "..."},
    "reputacao_mercado": {"score": <0-100>, "peso": 0.15, "justificativa": "..."},
    "porte_operacionalidade": {"score": <0-100>, "peso": 0.10, "justificativa": "..."}
  },
  "bloqueios": [],
  "pontos_positivos": ["..."],
  "pontos_atencao": ["..."],
  "parecer": "<3-5 frases executivas>"
}"""

try:
    client = anthropic.Anthropic(api_key=API_KEY)

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content":
            f"Analise o perfil de crédito da empresa CNPJ {CNPJ}.\n\n"
            f"DADOS COLETADOS:\n{json.dumps(snapshots, ensure_ascii=False, indent=2)}"
        }],
    )

    result_text = response.content[0].text
    clean = re.sub(r"```json|```", "", result_text).strip()
    r = json.loads(clean)

    print(f"\n{'='*60}")
    print(f"📊 SCORE FINAL:  {r['score']}/100")
    print(f"🏆 RATING:       {r['rating']}")
    print(f"💸 TAXA SUGERIDA: {r['taxa_sugerida_am']*100:.2f}% a.m.")
    print(f"📋 LIMITE:       {r['limite_sugerido_pct_contrato']*100:.0f}% do contrato")
    print(f"{'='*60}\n")

    print(f"📐 Dimensões:")
    for dim, vals in r.get("dimensoes", {}).items():
        print(f"   {dim:35} {vals['score']:3}/100 (peso {vals['peso']*100:.0f}%)")

    if r.get("bloqueios"):
        print(f"\n🚫 Bloqueios:")
        for b in r["bloqueios"]:
            print(f"   • {b}")

    print(f"\n✅ Pontos positivos:")
    for p in r.get("pontos_positivos", []):
        print(f"   • {p}")

    print(f"\n⚠️  Pontos de atenção:")
    for p in r.get("pontos_atencao", []):
        print(f"   • {p}")

    print(f"\n📝 Parecer:\n   {r.get('parecer')}")

    out = Path(__file__).parent / "output_score_engine.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")

except json.JSONDecodeError:
    print(f"⚠️  Resposta não é JSON válido:")
    print(result_text)
except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc(); sys.exit(1)
