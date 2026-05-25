"""
Componente: BrasilAPI
Consulta dados cadastrais da empresa (CNPJ, QSA, atividade, situação).
Tipo: automatizado | Fila: fast | Cache: 24h
"""
import httpx
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BRASIL_API_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="brasil_api.run",
    max_retries=3,
    default_retry_delay=5,
)
def run_brasil_api(self, operation_id: str):
    """Consulta dados cadastrais via BrasilAPI."""
    return self.execute(operation_id, component="brasil_api", handler=_fetch)


def _fetch(cnpj: str) -> dict:
    url = BRASIL_API_URL.format(cnpj=cnpj)
    with httpx.Client(timeout=15) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()

    return {
        "cnpj": data.get("cnpj"),
        "razao_social": data.get("razao_social"),
        "nome_fantasia": data.get("nome_fantasia"),
        "situacao_cadastral": data.get("descricao_situacao_cadastral"),
        "data_situacao": data.get("data_situacao_cadastral"),
        "data_abertura": data.get("data_inicio_atividade"),
        "natureza_juridica": data.get("natureza_juridica"),
        "porte": data.get("porte"),
        "capital_social": data.get("capital_social"),
        "atividade_principal": data.get("cnae_fiscal_descricao"),
        "atividades_secundarias": [
            a.get("descricao") for a in (data.get("cnaes_secundarios") or [])
        ],
        "qsa": [
            {
                "nome": s.get("nome_socio"),
                "qualificacao": s.get("qualificacao_socio"),
                "cpf_representante": s.get("cpf_representante_legal"),
            }
            for s in (data.get("qsa") or [])
        ],
        "municipio": data.get("municipio"),
        "uf": data.get("uf"),
        "email": data.get("email"),
        "telefone": data.get("ddd_telefone_1"),
    }
