"""Teste standalone: acordos_leniencia"""
import sys, os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import httpx

TOKEN = os.getenv("PORTAL_TRANSPARENCIA_TOKEN")
CNPJ  = os.getenv("TEST_CNPJ", "03012610000101")

if not TOKEN:
    print("❌ PORTAL_TRANSPARENCIA_TOKEN não encontrado"); sys.exit(1)

print(f"🔍 Consultando acordos de leniência — CNPJ: {CNPJ}")
print("-" * 60)

def fetch(cnpj, token):
    cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")
    headers = {"chave-api-dados": token}
    acordos = []
    for pagina in range(1, 6):
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(
                "https://api.portaldatransparencia.gov.br/api-de-dados/acordos-leniencia",
                headers=headers,
                params={"cnpjSancionado": cnpj_limpo, "pagina": pagina},
            )
            resp.raise_for_status()
            data = resp.json()
        if not data: break
        acordos.extend(data)
        if len(data) < 10: break
    return {"possui_acordo": len(acordos) > 0, "total_acordos": len(acordos), "acordos": acordos}

try:
    r = fetch(CNPJ, TOKEN)
    print(f"✅ Consulta concluída\n")
    if r["possui_acordo"]:
        print(f"🚨 POSSUI {r['total_acordos']} acordo(s) de leniência")
        for a in r["acordos"]:
            print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Nenhum acordo de leniência encontrado")

    out = Path(__file__).parent / "output_acordos_leniencia.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")
except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc(); sys.exit(1)
