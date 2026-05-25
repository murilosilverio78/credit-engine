"""
Teste standalone do componente BrasilAPI.
Roda direto no terminal sem Celery, config ou banco.

Uso:
    cd backend
    python tests/test_brasil_api.py
"""
import sys, os, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

CNPJ = os.getenv("TEST_CNPJ", "03012610000101")

print(f"🔍 Consultando CNPJ: {CNPJ}")
print("-" * 60)

BASE_URL = "https://brasilapi.com.br/api/cnpj/v1"

def fetch(cnpj: str) -> dict:
    # verify=False necessário no Windows por limitação do schannel/certificados
    with httpx.Client(timeout=15, verify=False) as client:
        resp = client.get(f"{BASE_URL}/{cnpj}")
        resp.raise_for_status()
        d = resp.json()

    return {
        "cnpj": d.get("cnpj"),
        "razao_social": d.get("razao_social"),
        "nome_fantasia": d.get("nome_fantasia"),
        "situacao_cadastral": d.get("descricao_situacao_cadastral"),
        "data_situacao": d.get("data_situacao_cadastral"),
        "data_abertura": d.get("data_inicio_atividade"),
        "natureza_juridica": d.get("natureza_juridica"),
        "porte": d.get("porte"),
        "capital_social": d.get("capital_social"),
        "atividade_principal": d.get("cnae_fiscal_descricao"),
        "regime_tributario": [
            {"ano": r.get("ano"), "forma": r.get("forma_de_tributacao")}
            for r in (d.get("regime_tributario") or [])
        ],
        "atividades_secundarias": [
            a.get("descricao") for a in (d.get("cnaes_secundarios") or [])
        ],
        "qsa": [
            {
                "nome": s.get("nome_socio"),
                "qualificacao": s.get("qualificacao_socio"),
                "data_entrada": s.get("data_entrada_sociedade"),
            }
            for s in (d.get("qsa") or [])
        ],
        "municipio": d.get("municipio"),
        "uf": d.get("uf"),
        "email": d.get("email"),
        "telefone": d.get("ddd_telefone_1"),
        "opcao_simples": d.get("opcao_pelo_simples"),
        "opcao_mei": d.get("opcao_pelo_mei"),
    }

try:
    r = fetch(CNPJ)

    print(f"✅ Consulta concluída\n")
    print(f"🏢 Razão Social:       {r['razao_social']}")
    print(f"🏷️  Nome Fantasia:      {r['nome_fantasia']}")
    print(f"📋 Situação Cadastral: {r['situacao_cadastral']}")
    print(f"📅 Data Abertura:      {r['data_abertura']}")
    print(f"⚖️  Natureza Jurídica:  {r['natureza_juridica']}")
    print(f"📊 Porte:              {r['porte']}")
    print(f"💰 Capital Social:     R$ {float(r['capital_social'] or 0):,.2f}")
    print(f"🏙️  Município:          {r['municipio']} / {r['uf']}")
    print(f"📞 Telefone:           {r['telefone']}")
    print(f"📧 Email:              {r['email']}")
    print(f"🧾 Simples Nacional:   {'Sim' if r['opcao_simples'] else 'Não'}")
    print(f"🧾 MEI:                {'Sim' if r['opcao_mei'] else 'Não'}")

    print(f"\n🔧 Atividade Principal:")
    print(f"   {r['atividade_principal']}")

    if r['regime_tributario']:
        print(f"\n📊 Regime Tributário:")
        for reg in r['regime_tributario']:
            print(f"   • {reg['ano']}: {reg['forma']}")

    if r['atividades_secundarias']:
        print(f"\n🔧 Atividades Secundárias ({len(r['atividades_secundarias'])}):")
        for a in r['atividades_secundarias'][:5]:
            print(f"   • {a}")

    if r['qsa']:
        print(f"\n👥 Quadro Societário:")
        for s in r['qsa']:
            print(f"   • {s['nome']} — {s['qualificacao']} (entrada: {s['data_entrada']})")

    out = Path(__file__).parent / "output_brasil_api.json"
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 Resultado salvo em: {out}")

except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
