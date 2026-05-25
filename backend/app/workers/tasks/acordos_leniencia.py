"""
Componente: acordos_leniencia
Consulta acordos de leniência anticorrupção no Portal da Transparência.
Sempre executado — independente das flags do pessoa_juridica.

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

    cnpj_limpo = cnpj.replace(".", "").replace("/", "").replace("-", "")
    headers = {"chave-api-dados": api_token}
    acordos = []

    for pagina in range(1, 6):
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(
                f"{BASE_URL}/acordos-leniencia",
                headers=headers,
                params={"cnpjSancionado": cnpj_limpo, "pagina": pagina},
            )
            resp.raise_for_status()
            data = resp.json()

        if not data:
            break
        acordos.extend(data)
        if len(data) < 10:
            break

    return {
        "possui_acordo": len(acordos) > 0,
        "total_acordos": len(acordos),
        "acordos": [
            {
                "situacao": a.get("situacao"),
                "data_inicio": a.get("dataInicioAcordo"),
                "data_fim": a.get("dataFimAcordo"),
                "orgao": a.get("orgaoResponsavel"),
                "objeto": a.get("objeto"),
            }
            for a in acordos
        ],
    }


@celery_app.task(
    bind=True,
    base=BaseComponentTask,
    queue="fast",
    name="acordos_leniencia.run",
    max_retries=3,
    default_retry_delay=10,
)
def run_acordos_leniencia(self, operation_id: str):
    return self.execute(operation_id, component="acordos_leniencia", handler=_fetch)
