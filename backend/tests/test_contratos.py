"""
Teste standalone do componente contratos.

Uso:
    cd backend
    python tests/test_contratos.py
"""
import sys, os, json
from pathlib import Path
from datetime import date
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

TOKEN = os.getenv("PORTAL_TRANSPARENCIA_TOKEN")
CNPJ  = os.getenv("TEST_CNPJ", "03012610000101")

if not TOKEN:
    print("❌ PORTAL_TRANSPARENCIA_TOKEN não encontrado no .env")
    sys.exit(1)

print(f"🔍 Consultando CNPJ: {CNPJ}")
print(f"🔑 Token: {TOKEN[:8]}...")
print("-" * 60)

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"

def _is_ativo(c):
    try:
        return date.fromisoformat(c.get("dataFimVigencia") or "") >= date.today()
    except Exception:
        return False

def _get_orgao(c):
    try:
        return c["unidadeGestora"]["orgaoMaximo"]["nome"]
    except (KeyError, TypeError):
        return ""

def _parse(c):
    return {
        "numero": c.get("numero"),
        "objeto": (c.get("objeto") or "").replace("Objeto: ", "").strip(),
        "situacao": c.get("situacaoContrato"),
        "valor_inicial": c.get("valorInicialCompra"),
        "valor_final": c.get("valorFinalCompra"),
        "data_inicio": c.get("dataInicioVigencia"),
        "data_fim": c.get("dataFimVigencia"),
        "orgao": _get_orgao(c),
        "ativo": _is_ativo(c),
    }

def fetch(cnpj, token):
    headers = {"chave-api-dados": token}
    contratos = []
    for pagina in range(1, 6):
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/contratos/cpf-cnpj",
                headers=headers,
                params={"cpfCnpj": cnpj, "pagina": pagina, "tamanhoPagina": 50},
            )
            resp.raise_for_status()
            data = resp.json()
        if not data:
            break
        contratos.extend(data)
        if len(data) < 50:
            break

    parsed    = [_parse(c) for c in contratos]
    ativos    = [c for c in parsed if c["ativo"]]
    encerrados = [c for c in parsed if not c["ativo"]]

    return {
        "total_contratos": len(parsed),
        "contratos_ativos": len(ativos),
        "contratos_encerrados": len(encerrados),
        "valor_total_ativo": sum(float(c["valor_inicial"] or 0) for c in ativos),
        "valor_total_historico": sum(float(c["valor_inicial"] or 0) for c in parsed),
        "orgaos_contratantes": list({c["orgao"] for c in parsed if c["orgao"]})[:10],
        "contratos_detalhe": parsed,
    }

try:
    r = fetch(CNPJ, TOKEN)

    print(f"✅ Consulta concluída\n")
    print(f"📋 Total de contratos:    {r['total_contratos']}")
    print(f"✅ Contratos ativos:      {r['contratos_ativos']}")
    print(f"❌ Contratos encerrados:  {r['contratos_encerrados']}")
    print(f"💰 Valor total ativo:     R$ {r['valor_total_ativo']:,.2f}")
    print(f"💰 Valor total histórico: R$ {r['valor_total_historico']:,.2f}")

    print(f"\n🏛️  Órgãos contratantes:")
    for o in r["orgaos_contratantes"]:
        print(f"   • {o}")

    print(f"\n📄 Contratos ativos:")
    for c in [x for x in r["contratos_detalhe"] if x["ativo"]]:
        print(f"   ✅ {c['numero']} | R$ {c['valor_inicial']:,.2f} | {c['data_inicio']} → {c['data_fim']}")
        print(f"      {c['orgao']}")
        print(f"      {c['objeto'][:100]}")
        print()

    out = Path(__file__).parent / "output_contratos.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 Resultado salvo em: {out}")

except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
