"""
Teste standalone do componente recursos_recebidos.

Uso:
    cd backend
    python tests/test_recursos_recebidos.py
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

def _mes_ano(d):
    return d.strftime("%m/%Y")

def fetch(cnpj, token):
    headers = {"chave-api-dados": token}
    hoje = date.today()
    mes_fim    = _mes_ano(hoje)
    mes_inicio = _mes_ano(hoje.replace(year=hoje.year - 2))

    recursos = []
    for pagina in range(1, 6):
        # Monta URL como string para evitar encoding do "/" pelo httpx
        url = (
            f"{BASE_URL}/despesas/recursos-recebidos"
            f"?codigoFavorecido={cnpj}"
            f"&mesAnoInicio={mes_inicio}"
            f"&mesAnoFim={mes_fim}"
            f"&pagina={pagina}"
        )
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if not data:
            break
        recursos.extend(data)
        if len(data) < 10:
            break

    valor_total = sum(float(r.get("valor") or 0) for r in recursos)
    orgaos = list({r.get("nomeOrgao", "") for r in recursos if r.get("nomeOrgao")})

    por_ano = {}
    for r in recursos:
        ano = (r.get("mes") or "")[-4:]
        if ano:
            por_ano[ano] = por_ano.get(ano, 0) + float(r.get("valor") or 0)

    return {
        "total_registros": len(recursos),
        "valor_total_recebido": valor_total,
        "periodo_inicio": mes_inicio,
        "periodo_fim": mes_fim,
        "orgaos_pagadores": orgaos[:10],
        "valor_por_ano": por_ano,
        "recursos_detalhe": [
            {
                "mes": r.get("mes"),
                "valor": r.get("valor"),
                "orgao": r.get("nomeOrgao"),
                "acao": r.get("nomeAcao"),
            }
            for r in recursos[:50]
        ],
        "raw_primeiro": recursos[0] if recursos else {},
    }

try:
    r = fetch(CNPJ, TOKEN)

    print(f"✅ Consulta concluída\n")
    print(f"📅 Período:              {r['periodo_inicio']} → {r['periodo_fim']}")
    print(f"📋 Total de registros:   {r['total_registros']}")
    print(f"💰 Valor total recebido: R$ {r['valor_total_recebido']:,.2f}")

    if r['valor_por_ano']:
        print(f"\n📊 Recebimentos por ano:")
        for ano, val in sorted(r['valor_por_ano'].items()):
            print(f"   • {ano}: R$ {val:,.2f}")

    if r['orgaos_pagadores']:
        print(f"\n🏛️  Órgãos pagadores:")
        for o in r['orgaos_pagadores']:
            print(f"   • {o}")

    if r['recursos_detalhe']:
        print(f"\n📄 Últimos recebimentos (top 5):")
        for rec in r['recursos_detalhe'][:5]:
            print(f"   • {rec['mes']} | R$ {float(rec['valor'] or 0):,.2f} | {rec['orgao']}")
            print(f"     {(rec['acao'] or '')[:80]}")

    if r['raw_primeiro']:
        print(f"\n🔎 Campos do primeiro registro:")
        print(json.dumps(r['raw_primeiro'], ensure_ascii=False, indent=2))

    out = Path(__file__).parent / "output_recursos_recebidos.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")

except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
