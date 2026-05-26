"""
Componente: cepim
Cadastro de Entidades Privadas Sem Fins Lucrativos Impedidas — Portal da Transparência.
Executado apenas se pessoa_juridica.sancionado_cepim = True.

Tipo: automatizado | Fila: fast | Cache: 24h
"""
import httpx
from app.workers.celery_app import celery_app
from app.workers.base import BaseComponentTask
import structlog

logger = structlog.get_logger()

BASE_URL = "https://api.portaldatransparencia.gov.br/api-de-dados"


def _fetch(cnpj: str, token: str = None) -> dict:
    from app.core.config import settings
    api_token = token or settings.PORTAL_TRANSPARENCIA_TOKEN

    headers = {"chave-api-dados": api_token}
    registros = []

    pagina = 1
    while True:
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/cepim",
                headers=headers,
                params={"cnpjSancionado": cnpj, "pagina": pagina},
            )
            resp.raise_for_status()
            data = [] if not resp.content or not resp.text.strip() else resp.json()

        if not data:
            break
        registros.extend(data)
        if len(data) < 10:
            break
        pagina += 1

    return {
        "possui_sancao": len(registros) > 0,
        "total_registros": len(registros),
        "registros": registros,
    }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="cepim.run",
    max_retries=3,
    default_retry_delay=10,
)
def run_cepim(self, operation_id: str):
    return self.execute(operation_id, component="cepim", handler=_fetch)
