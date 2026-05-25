"""Teste standalone: CEIS"""
import sys, os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import httpx

TOKEN = os.getenv("PORTAL_TRANSPARENCIA_TOKEN")
CNPJ  = os.getenv("TEST_CNPJ", "03012610000101")

if not TOKEN:
    print("PORTAL_TRANSPARENCIA_TOKEN nao encontrado"); sys.exit(1)

print(f"Consultando CEIS - CNPJ: {CNPJ}")
print("-" * 60)

def fetch(cnpj, token):
    headers = {"chave-api-dados": token}
    registros = []
    for pagina in range(1, 6):
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"https://api.portaldatransparencia.gov.br/api-de-dados/ceis",
                headers=headers,
                params={"codigoSancionado": cnpj, "pagina": pagina},
            )
            resp.raise_for_status()
            data = resp.json()
        if not data: break
        registros.extend(data)
        if len(data) < 10: break
    return {"possui_sancao": len(registros) > 0, "total_registros": len(registros), "registros": registros}

try:
    r = fetch(CNPJ, TOKEN)
    print(f"Consulta concluida
")
    if r["possui_sancao"]:
        print(f"ALERTA: {r['total_registros']} registro(s) CEIS")
        print(json.dumps(r["registros"][0], ensure_ascii=False, indent=2))
    else:
        print(f"OK: Nenhuma sancao CEIS encontrada")

    out = Path(__file__).parent / "output_ceis.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultado salvo em: {out}")
except Exception as e:
    print(f"Erro: {e}")
    import traceback; traceback.print_exc(); sys.exit(1)
