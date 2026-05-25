"""
Teste standalone do componente pessoa_juridica.

Uso:
    cd backend
    python tests/test_pessoa_juridica.py
"""
import sys, os, json
from pathlib import Path
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

def fetch(cnpj, token):
    cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")
    with httpx.Client(timeout=15, verify=False) as client:
        resp = client.get(
            f"{BASE_URL}/pessoa-juridica",
            headers={"chave-api-dados": token},
            params={"cnpj": cnpj_limpo},
        )
        resp.raise_for_status()
        d = resp.json()

    flags = {
        "sancionado_ceis":             d.get("sancionadoCEIS", False),
        "sancionado_cnep":             d.get("sancionadoCNEP", False),
        "sancionado_cepim":            d.get("sancionadoCEPIM", False),
        "sancionado_ceaf":             d.get("sancionadoCEAF", False),
        "possui_contratacao":          d.get("possuiContratacao", False),
        "favorecido_despesas":         d.get("favorecidoDespesas", False),
        "favorecido_transferencias":   d.get("favorecidoTransferencias", False),
        "convenios":                   d.get("convenios", False),
        "participa_licitacao":         d.get("participanteLicitacao", False),
        "emitiu_nfe":                  d.get("emitiuNFe", False),
        "beneficiado_renuncia_fiscal": d.get("beneficiadoRenunciaFiscal", False),
    }

    return {
        "cnpj": cnpj_limpo,
        "razao_social": d.get("razaoSocial"),
        "nome_fantasia": d.get("nomeFantasia"),
        **flags,
        "possui_sancao": any([flags["sancionado_ceis"], flags["sancionado_cnep"],
                              flags["sancionado_cepim"], flags["sancionado_ceaf"]]),
    }

try:
    r = fetch(CNPJ, TOKEN)

    print(f"✅ Consulta concluída\n")
    print(f"🏢 Razão Social:   {r['razao_social']}")
    print(f"🏷️  Nome Fantasia:  {r['nome_fantasia']}")
    print()
    print(f"🚦 FLAGS — pipeline downstream:")
    print(f"   {'✅' if r['possui_contratacao']        else '⬜'} possui_contratacao          → {'dispara' if r['possui_contratacao']        else 'pula'} worker contratos")
    print(f"   {'✅' if r['favorecido_despesas']       else '⬜'} favorecido_despesas         → {'dispara' if r['favorecido_despesas']       else 'pula'} worker recursos_recebidos")
    print(f"   {'✅' if r['favorecido_transferencias'] else '⬜'} favorecido_transferencias")
    print(f"   {'✅' if r['convenios']                 else '⬜'} convenios")
    print(f"   {'✅' if r['participa_licitacao']       else '⬜'} participa_licitacao")
    print(f"   {'✅' if r['emitiu_nfe']                else '⬜'} emitiu_nfe")
    print()
    print(f"🔴 SANÇÕES:")
    print(f"   {'🚨' if r['sancionado_ceis']  else '✅'} sancionado_ceis   → {'dispara' if r['sancionado_ceis']  else 'pula'} worker ceis")
    print(f"   {'🚨' if r['sancionado_cnep']  else '✅'} sancionado_cnep   → {'dispara' if r['sancionado_cnep']  else 'pula'} worker cnep")
    print(f"   {'🚨' if r['sancionado_cepim'] else '✅'} sancionado_cepim  → {'dispara' if r['sancionado_cepim'] else 'pula'} worker cepim")
    print(f"   {'🚨' if r['sancionado_ceaf']  else '✅'} sancionado_ceaf")
    print()

    if r['possui_sancao']:
        print(f"⛔ ALERTA: empresa possui sanção ativa")
    else:
        print(f"✅ Sem sanções ativas — pipeline pode prosseguir")

    out = Path(__file__).parent / "output_pessoa_juridica.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")

except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
