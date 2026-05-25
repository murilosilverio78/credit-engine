"""
Inspeciona os campos retornados pela API do Portal da Transparência.
Roda uma vez para identificar os nomes corretos dos campos.
"""
import sys, os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

TOKEN = os.getenv("PORTAL_TRANSPARENCIA_TOKEN")
CNPJ = os.getenv("TEST_CNPJ", "03012610000101")

with httpx.Client(timeout=20) as client:
    resp = client.get(
        "https://api.portaldatransparencia.gov.br/api-de-dados/contratos/cpf-cnpj",
        headers={"chave-api-dados": TOKEN},
        params={"cpfCnpj": CNPJ, "pagina": 1, "tamanhoPagina": 1},
    )
    data = resp.json()

if not data:
    print("Nenhum contrato retornado")
    sys.exit(1)

primeiro = data[0]
print("=== CAMPOS DO CONTRATO ===")
print(json.dumps(primeiro, ensure_ascii=False, indent=2))
