"""
Componente: BrasilAPI
Consulta dados cadastrais da empresa (CNPJ, QSA, atividade, situação, regime tributário).
Tipo: automatizado | Fila: fast | Cache: 24h

Nota: verify=False necessário por limitação de certificados SSL em alguns ambientes Windows.
      Em produção (Railway/Linux) funciona normalmente com verify=True.
"""
import httpx
import os
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BRASIL_API_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
SSL_VERIFY = os.getenv("SSL_VERIFY", "true").lower() != "false"


_task = BaseComponentTask()


def run_brasil_api(operation_id: str):
    """Consulta dados cadastrais via BrasilAPI."""
    return _task.execute(operation_id, component="brasil_api", handler=_fetch)


def _fetch(cnpj: str) -> dict:
    url = BRASIL_API_URL.format(cnpj=cnpj)
    with httpx.Client(timeout=15, verify=SSL_VERIFY) as client:
        response = client.get(url)
        response.raise_for_status()
        d = response.json()

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
