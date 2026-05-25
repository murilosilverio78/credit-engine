"""
Componente: ceis
Cadastro de Empresas Inidôneas e Suspensas — Portal da Transparência.
Executado apenas se pessoa_juridica.sancionado_ceis = True.

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

    for pagina in range(1, 6):
        with httpx.Client(timeout=20, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/ceis",
                headers=headers,
                params={"codigoSancionado": cnpj, "pagina": pagina},
            )
            resp.raise_for_status()
            data = resp.json()

        if not data:
            break
        registros.extend(data)
        if len(data) < 10:
            break

    return {
        "possui_sancao": len(registros) > 0,
        "total_registros": len(registros),
        "registros": registros,
    }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="ceis.run",
    max_retries=3,
    default_retry_delay=10,
)
def run_ceis(self, operation_id: str):
    return self.execute(operation_id, component="ceis", handler=_fetch)
